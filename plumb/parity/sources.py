"""Workbook → SourceRelation extraction for the parity family (PARITY-PLAN S2.1).

Walks a .twb / .twbx through the single workbook byte loader
(checks/_tableau.read_twb_xml) and derives, per datasource, the physical
relations parity can prove: single tables and custom SQL are eligible;
joins, unions, extract-only and published datasources are refused with a
machine-readable reason (PARITY-PLAN D6 — decomposition lies about grain,
so we refuse-and-report rather than guess). Parsing is defensive in the
_tableau.py style: missing elements degrade to None fields, never raise;
only unreadable XML raises TableauParseError. Pure extraction — no
Snowflake, no engine imports.
"""

from __future__ import annotations

import re
from pathlib import Path

from lxml import etree

from plumb.checks._tableau import TableauParseError, read_twb_xml
from plumb.parity.contracts import (
    REFUSAL_EXTRACT_ONLY,
    REFUSAL_JOIN,
    REFUSAL_UNION,
    REFUSAL_UNRECOGNIZED,
    SourceRelation,
)

# Published (Tableau Server / sqlproxy) datasources: their relations live
# server-side, so parity cannot derive warehouse objects from the workbook.
# Lives here, not in contracts.py, until another module needs it.
REFUSAL_PUBLISHED = "published"

# Connection classes that hold extract bytes rather than a live relational
# source. A datasource backed only by these is refused as extract-only.
_NON_RELATIONAL_CLASSES = frozenset({"hyper", "dataengine"})

_BRACKETED_PART = re.compile(r"\[([^\]]+)\]")


def extract_relations(path: Path) -> list[SourceRelation]:
    """Extract every physical source relation a workbook depends on.

    One SourceRelation per direct-child relation of each datasource's
    connection: kind "table" / "custom_sql" when eligible, kind "refused"
    (with refusal_reason) for join, union, published, extract-only, and
    unrecognized shapes. Raises TableauParseError for unreadable workbooks.
    """
    raw = read_twb_xml(path)
    try:
        root = etree.fromstring(raw)
    except etree.XMLSyntaxError as exc:
        raise TableauParseError(f"could not parse workbook XML: {exc}") from exc

    relations: list[SourceRelation] = []
    # Direct children only: a worksheet's <view><datasources> holds
    # reference stubs, not real workbook data sources (same rule as
    # _tableau.parse_workbook).
    for ds in root.findall("datasources/datasource"):
        name = ds.get("name", "")
        if name == "Parameters":
            continue
        caption = ds.get("caption") or name
        if (
            ds.find(".//repository-location") is not None
            or ds.find('.//connection[@class="sqlproxy"]') is not None
        ):
            relations.append(
                SourceRelation(
                    datasource=caption, kind="refused", refusal_reason=REFUSAL_PUBLISHED
                )
            )
            continue
        relations.extend(_datasource_relations(ds, caption))
    return relations


def _datasource_relations(ds: etree._Element, caption: str) -> list[SourceRelation]:
    """All relations of one (non-published) datasource."""
    has_extract = ds.find(".//extract") is not None
    emitted: list[SourceRelation] = []
    non_relational_only = False

    for conn in ds.findall("connection"):
        conn_class = conn.get("class", "")
        if conn_class == "federated":
            # Modern shape: named-connections resolve each relation's
            # connection attr to an inner (e.g. snowflake) connection.
            lookup: dict[str, etree._Element] = {}
            for nc in conn.findall("named-connections/named-connection"):
                nc_name = nc.get("name")
                inner = nc.find("connection")
                if nc_name and inner is not None:
                    lookup[nc_name] = inner
            for rel in conn.findall("relation"):
                emitted.append(_build_relation(rel, lookup, None, caption, has_extract))
        elif conn_class in _NON_RELATIONAL_CLASSES:
            non_relational_only = True
        else:
            # Legacy direct shape: <connection class='snowflake'> with its
            # relations as direct children; the connection resolves itself.
            for rel in conn.findall("relation"):
                emitted.append(_build_relation(rel, {}, conn, caption, has_extract))

    if not emitted and (has_extract or non_relational_only):
        return [
            SourceRelation(
                datasource=caption, kind="refused", refusal_reason=REFUSAL_EXTRACT_ONLY
            )
        ]
    return emitted


def _build_relation(
    rel: etree._Element,
    lookup: dict[str, etree._Element],
    default_conn: etree._Element | None,
    datasource: str,
    has_extract: bool,
) -> SourceRelation:
    """One SourceRelation from one direct-child relation element.

    Nested relations under a join/union are deliberately not visited: the
    join tree is refused whole (PARITY-PLAN D6).
    """
    conn = lookup.get(rel.get("connection") or "")
    if conn is None:
        conn = default_conn
    conn_class = conn.get("class") if conn is not None else None

    rel_type = rel.get("type", "")
    if rel_type == "table":
        parts = _table_parts(rel.get("table") or "")
        schema = conn.get("schema") if conn is not None else None
        table: str | None = rel.get("name")
        if len(parts) >= 2:
            schema, table = parts[-2], parts[-1]
        elif len(parts) == 1:
            table = parts[0]
        return SourceRelation(
            datasource=datasource,
            kind="table",
            database=conn.get("dbname") if conn is not None else None,
            schema=schema,
            table=table,
            connection_class=conn_class,
            has_extract=has_extract,
        )
    if rel_type == "text":
        return SourceRelation(
            datasource=datasource,
            kind="custom_sql",
            custom_sql=(rel.text or "").strip(),
            connection_class=conn_class,
            has_extract=has_extract,
        )
    if rel_type == "join":
        reason = REFUSAL_JOIN
    elif rel_type == "union":
        reason = REFUSAL_UNION
    else:
        reason = REFUSAL_UNRECOGNIZED
    return SourceRelation(
        datasource=datasource,
        kind="refused",
        refusal_reason=reason,
        connection_class=conn_class,
        has_extract=has_extract,
    )


def _table_parts(table_attr: str) -> list[str]:
    """Split a relation table attr like "[SALES].[ORDERS]" or "[ORDERS]"
    into its bracket-stripped parts; tolerate unbracketed dotted names."""
    parts = _BRACKETED_PART.findall(table_attr)
    if parts:
        return parts
    return [p for p in table_attr.split(".") if p]
