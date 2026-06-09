"""Tableau .twb / .twbx parsing with lxml (ADR-0011).

A .twbx is a zip whose contained .twb is the workbook XML. We parse the
XML defensively: Tableau's schema varies by version, so every extraction
tolerates missing elements and falls back rather than raising. The parsed
TableauWorkbook is what the T-* checks consume; they never touch lxml.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

# Functions that signal row-level security logic in a calculation.
RLS_FUNCTIONS = ("USERNAME(", "USERDOMAIN(", "ISMEMBEROF(", "ISUSERNAME(", "FULLNAME(")

_LOD_FIXED = re.compile(r"\{\s*FIXED", re.IGNORECASE)
_NUMERIC_LITERAL = re.compile(r"(?<![A-Za-z0-9_\[])\d+(\.\d+)?(?![A-Za-z0-9_\]])")
_DATE_LITERAL = re.compile(r"#\s*\d{4}-\d{2}-\d{2}")
_FIELD_REF = re.compile(r"\[[^\]]+\]")
_AGG_FUNCS = ("SUM(", "AVG(", "COUNT(", "MIN(", "MAX(", "MEDIAN(", "COUNTD(")


class TableauParseError(Exception):
    """The workbook could not be read or parsed."""


@dataclass
class TableauField:
    datasource: str
    name: str
    caption: str
    datatype: str
    role: str
    formula: str | None = None

    @property
    def is_calculated(self) -> bool:
        return self.formula is not None


@dataclass
class TableauDatasource:
    name: str
    caption: str
    is_published: bool = False
    has_extract: bool = False
    custom_sql: list[str] = field(default_factory=list)


@dataclass
class TableauWorksheet:
    name: str
    referenced_fields: set[str] = field(default_factory=set)
    has_grand_totals: bool = False


@dataclass
class TableauWorkbook:
    datasources: list[TableauDatasource] = field(default_factory=list)
    fields: list[TableauField] = field(default_factory=list)
    worksheets: list[TableauWorksheet] = field(default_factory=list)
    filter_count: int = 0
    version: str | None = None

    def calculated_fields(self) -> list[TableauField]:
        return [f for f in self.fields if f.is_calculated]


# Plumb parses only the workbook definition (the .twb XML), never the data
# extracts a .twbx may bundle. This bounds the XML we actually read, so a large,
# complex workbook is fine while a runaway file is still refused.
MAX_TWB_XML_BYTES = 64 * 1024 * 1024


def _too_large(actual: int) -> TableauParseError:
    return TableauParseError(
        f"workbook definition is too large to analyze: "
        f"{actual // (1024 * 1024)} MB (limit {MAX_TWB_XML_BYTES // (1024 * 1024)} MB). "
        "This is the .twb XML, not the data extract."
    )


def read_twb_xml(path: Path) -> bytes:
    if not path.exists():
        raise TableauParseError(f"workbook not found: {path}")
    if path.suffix.lower() == ".twbx":
        try:
            with zipfile.ZipFile(path) as archive:
                twb_names = [n for n in archive.namelist() if n.lower().endswith(".twb")]
                if not twb_names:
                    raise TableauParseError(f"no .twb inside {path}")
                info = archive.getinfo(twb_names[0])
                if info.file_size > MAX_TWB_XML_BYTES:
                    raise _too_large(info.file_size)
                return archive.read(twb_names[0])
        except zipfile.BadZipFile as exc:
            raise TableauParseError(f"{path} is not a valid .twbx zip: {exc}") from exc
    size = path.stat().st_size
    if size > MAX_TWB_XML_BYTES:
        raise _too_large(size)
    return path.read_bytes()


def parse_workbook(path: Path) -> TableauWorkbook:
    raw = read_twb_xml(path)
    try:
        root = etree.fromstring(raw)
    except etree.XMLSyntaxError as exc:
        raise TableauParseError(f"could not parse workbook XML: {exc}") from exc

    workbook = TableauWorkbook(version=root.get("version"))

    # Direct child only: a worksheet's <view><datasources> holds reference
    # stubs that must not be mistaken for real workbook data sources.
    for ds in root.findall("datasources/datasource"):
        name = ds.get("name", "")
        caption = ds.get("caption") or name
        if name == "Parameters":
            continue
        is_published = (
            ds.find(".//repository-location") is not None
            or ds.find('.//connection[@class="sqlproxy"]') is not None
        )
        has_extract = ds.find(".//extract") is not None
        custom_sql = [
            (rel.text or "").strip()
            for rel in ds.findall('.//relation[@type="text"]')
            if (rel.text or "").strip()
        ]
        workbook.datasources.append(
            TableauDatasource(
                name=name,
                caption=caption,
                is_published=is_published,
                has_extract=has_extract,
                custom_sql=custom_sql,
            )
        )
        for col in ds.findall(".//column"):
            calc = col.find("calculation")
            formula = calc.get("formula") if calc is not None else None
            workbook.fields.append(
                TableauField(
                    datasource=caption,
                    name=col.get("name", ""),
                    caption=col.get("caption") or col.get("name", ""),
                    datatype=col.get("datatype", ""),
                    role=col.get("role", ""),
                    formula=formula,
                )
            )

    for ws in root.findall("worksheets/worksheet"):
        referenced = {
            c.get("name", "")
            for c in ws.findall(".//datasource-dependencies/column")
            if c.get("name")
        }
        totals = (
            ws.find(".//*[@show-grand-totals]") is not None
            or ws.find(".//total") is not None
        )
        workbook.worksheets.append(
            TableauWorksheet(
                name=ws.get("name", ""),
                referenced_fields=referenced,
                has_grand_totals=totals,
            )
        )

    workbook.filter_count = len(root.findall(".//worksheets//filter"))
    return workbook


def has_fixed_lod(formula: str) -> bool:
    return bool(_LOD_FIXED.search(formula))


def has_hardcoded_literal(formula: str) -> bool:
    return bool(_NUMERIC_LITERAL.search(formula) or _DATE_LITERAL.search(formula))


def has_rls_function(formula: str) -> bool:
    upper = formula.upper()
    return any(fn in upper for fn in RLS_FUNCTIONS)


def aggregation_over_arithmetic(formula: str) -> bool:
    """A coarse grain-mismatch smell: an aggregate wrapping an expression
    that divides or multiplies two field references, for example
    AVG([a] / [b]). Summing or averaging a per-row ratio rarely matches the
    database grain."""
    upper = formula.upper()
    for agg in _AGG_FUNCS:
        idx = upper.find(agg)
        while idx != -1:
            segment = _balanced_segment(formula, idx + len(agg) - 1)
            if (
                segment
                and ("/" in segment or "*" in segment)
                and len(_FIELD_REF.findall(segment)) >= 2
            ):
                return True
            idx = upper.find(agg, idx + 1)
    return False


def _balanced_segment(text: str, open_paren_idx: int) -> str | None:
    depth = 0
    for i in range(open_paren_idx, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren_idx + 1 : i]
    return None
