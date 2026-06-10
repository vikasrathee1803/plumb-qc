"""Phase 2 Tableau static family: parser plus the T-* catalog, against a
representative .twb fixture."""

from pathlib import Path

import pytest

from plumb.checks._tableau import parse_workbook
from plumb.checks.tableau_static import (
    t_calc_001,
    t_calc_002,
    t_calc_003,
    t_filt_001,
    t_lod_001,
    t_name_001,
    t_rls_001,
    t_src_001,
    t_src_003,
    t_total_001,
    t_unused_001,
)
from plumb.config.models import Naming, Ruleset
from plumb.engine.models import CheckFamily, Severity, Status, Target
from plumb.engine.registry import CheckContext

FIXTURE = Path(__file__).parent / "fixtures" / "tableau" / "sales_dashboard.twb"


@pytest.fixture(scope="module")
def workbook():
    return parse_workbook(FIXTURE)


def test_large_twbx_parses_only_the_twb_xml(tmp_path):
    """A .twbx bundling a big data extract parses fine: only the small .twb XML
    is read, never the data, so complex workbooks are not blocked by size."""
    import zipfile

    from plumb.checks._tableau import read_twb_xml

    twb = FIXTURE.read_bytes()
    twbx = tmp_path / "big.twbx"
    with zipfile.ZipFile(twbx, "w") as z:
        z.writestr("wb.twb", twb)
        z.writestr("Data/extract.hyper", b"x" * (30 * 1024 * 1024))  # 30 MB, > the old cap
    assert len(read_twb_xml(twbx)) == len(twb)  # only the definition is read
    assert parse_workbook(twbx).datasources  # parses despite the large package


def test_oversized_twb_xml_is_refused(tmp_path, monkeypatch):
    import plumb.checks._tableau as tb

    monkeypatch.setattr(tb, "MAX_TWB_XML_BYTES", 1024)
    big = tmp_path / "big.twb"
    big.write_bytes(b"<workbook>" + b" " * 4096 + b"</workbook>")
    with pytest.raises(tb.TableauParseError, match="too large"):
        tb.read_twb_xml(big)


def _ctx(workbook, ruleset: Ruleset | None = None) -> CheckContext:
    return CheckContext(
        run_id="t",
        target=Target(type="tableau", name="wb", source_ref=str(FIXTURE)),
        ruleset=ruleset or Ruleset(version="1"),
        workbook=workbook,
    )


class TestParser:
    def test_skips_parameters_and_dedupes_datasources(self, workbook):
        captions = [d.caption for d in workbook.datasources]
        assert captions == ["Sales Mart Certified", "Raw Orders Extract"]

    def test_published_and_extract_flags(self, workbook):
        by_caption = {d.caption: d for d in workbook.datasources}
        assert by_caption["Sales Mart Certified"].is_published is True
        assert by_caption["Raw Orders Extract"].has_extract is True
        assert by_caption["Raw Orders Extract"].custom_sql

    def test_twbx_zip_is_read(self, tmp_path):
        import zipfile

        twbx = tmp_path / "wb.twbx"
        with zipfile.ZipFile(twbx, "w") as z:
            z.write(FIXTURE, arcname="wb.twb")
        parsed = parse_workbook(twbx)
        assert len(parsed.datasources) == 2


class TestCatalog:
    def test_custom_sql_warns(self, workbook):
        res = t_src_001(_ctx(workbook), {})
        assert res.status is Status.WARN
        assert "Raw Orders Extract" in (res.observed or "")

    def test_uncertified_source_warns_when_no_list_configured(self, workbook):
        """Cycle-2 consistency fix: with no certified_sources configured the
        check cannot fully assert governance, so it WARNs (note, not REVIEW)
        â€” mirroring S-META-004, which skips when unconfigured. A HIGH fail
        on every unconfigured team's first run is alert fatigue, not QC."""
        res = t_src_003(_ctx(workbook), {})
        assert res.status is Status.WARN
        assert "Raw Orders Extract" in (res.observed or "")
        assert "certified_sources" in (res.remediation or "")

    def test_uncertified_source_is_high_fail_when_list_configured(self, workbook):
        ruleset = Ruleset(version="1", certified_sources=["Some Other Source"])
        res = t_src_003(_ctx(workbook, ruleset), {})
        assert res.status is Status.FAIL
        assert res.severity is Severity.HIGH
        assert "Raw Orders Extract" in (res.observed or "")

    def test_certified_list_satisfies_source_check(self, workbook):
        ruleset = Ruleset(version="1", certified_sources=["Raw Orders Extract"])
        res = t_src_003(_ctx(workbook, ruleset), {})
        assert res.status is Status.PASS

    def test_fixed_lod_inventory_warns_with_evidence(self, workbook):
        res = t_lod_001(_ctx(workbook), {})
        assert res.status is Status.WARN
        assert res.severity is Severity.HIGH
        assert any("FIXED" in (r["formula"] or "") for r in res.evidence.sample_rows)

    def test_aggregation_over_ratio_warns(self, workbook):
        res = t_calc_001(_ctx(workbook), {})
        assert res.status is Status.WARN
        assert any("Profit Ratio" == r["field"] for r in res.evidence.sample_rows)

    def test_hardcoded_literal_warns(self, workbook):
        res = t_calc_002(_ctx(workbook), {})
        assert res.status is Status.WARN
        flagged = {r["field"] for r in res.evidence.sample_rows}
        assert "High Value Flag" in flagged

    def test_naming_convention_flags_lowercase(self, workbook):
        ruleset = Ruleset(version="1", naming=Naming(tableau_field_regex="^[A-Z][A-Za-z0-9 ]+$"))
        res = t_name_001(_ctx(workbook, ruleset), {})
        assert res.status is Status.WARN
        assert "untidy ratio" in (res.observed or "")

    def test_unused_calculated_fields_warn(self, workbook):
        res = t_unused_001(_ctx(workbook), {})
        assert res.status is Status.WARN

    def test_filters_under_threshold_pass(self, workbook):
        res = t_filt_001(_ctx(workbook), {"max_filters": 12})
        assert res.status is Status.PASS

    def test_rls_skipped_unless_required(self, workbook):
        assert t_rls_001(_ctx(workbook), {}).status is Status.SKIP

    def test_rls_present_passes_when_required(self, workbook):
        res = t_rls_001(_ctx(workbook), {"required": True})
        assert res.status is Status.PASS

    def test_rls_absent_fails_when_required(self):
        from plumb.checks._tableau import TableauWorkbook

        empty = TableauWorkbook()
        res = t_rls_001(_ctx(empty), {"required": True})
        assert res.status is Status.FAIL

    def test_grand_totals_on_non_additive_warns(self, workbook):
        res = t_total_001(_ctx(workbook), {})
        assert res.status is Status.WARN

    def test_no_workbook_skips(self):
        ctx = CheckContext(
            run_id="t",
            target=Target(type="tableau", name="wb"),
            ruleset=Ruleset(version="1"),
            workbook=None,
        )
        assert t_src_001(ctx, {}).status is Status.SKIP


def test_family_is_tableau_static(workbook):
    res = t_src_001(_ctx(workbook), {})
    assert res.family is CheckFamily.TABLEAU_STATIC


# --- T-CALC-003: dangling field references ----------------------------------


def _wb_from_xml(tmp_path, xml: str):
    p = tmp_path / "wb.twb"
    p.write_text(xml, encoding="utf-8")
    return parse_workbook(p)


def _calc_twb(formula: str, extra_columns: str = "") -> str:
    return (
        "<?xml version='1.0' encoding='utf-8' ?>\n"
        "<workbook version='18.1'><datasources>"
        "<datasource caption='Sales' name='federated.1'>"
        "<connection class='federated' />"
        "<column caption='Amount' datatype='real' name='[AMOUNT]' role='measure' />"
        "<column caption='Region' datatype='string' name='[REGION]' role='dimension' />"
        + extra_columns +
        "<column caption='My Calc' datatype='real' name='[Calculation_1]' role='measure'>"
        f"<calculation class='tableau' formula=\"{formula}\" />"
        "</column>"
        "</datasource></datasources><worksheets /></workbook>"
    )


class TestTCalc003:
    def test_resolving_references_pass(self, tmp_path):
        wb = _wb_from_xml(tmp_path, _calc_twb("SUM([AMOUNT]) / 100"))
        res = t_calc_003(_ctx(wb), {})
        assert res.status is Status.PASS

    def test_deleted_calc_reference_fails_naming_it(self, tmp_path):
        """[Calculation_NNN] never comes from a database: unresolved means
        the referenced calc was deleted and this formula is broken."""
        wb = _wb_from_xml(tmp_path, _calc_twb("[Calculation_99] + SUM([AMOUNT])"))
        res = t_calc_003(_ctx(wb), {})
        assert res.status is Status.FAIL
        assert "[Calculation_99]" in res.observed
        assert res.evidence.sample_rows[0]["missing"] == "[Calculation_99]"

    def test_bare_unknown_refs_are_not_judged(self, tmp_path):
        """Plain DB columns are usually NOT materialized as <column>
        elements in the .twb (the representative fixture proves it), so a
        bare unknown reference is normal — flagging it would be noise."""
        wb = _wb_from_xml(tmp_path, _calc_twb("SUM([SOME_DB_COLUMN]) + SUM([AMOUNT])"))
        res = t_calc_003(_ctx(wb), {})
        assert res.status is Status.PASS

    def test_existing_calc_reference_resolves(self, tmp_path):
        extra = (
            "<column caption='Base' datatype='real' name='[Calculation_42]' role='measure'>"
            "<calculation class='tableau' formula='SUM([AMOUNT])' /></column>"
        )
        wb = _wb_from_xml(tmp_path, _calc_twb("[Calculation_42] * 2", extra))
        res = t_calc_003(_ctx(wb), {})
        assert res.status is Status.PASS

    def test_caption_reference_resolves(self, tmp_path):
        """Formulas sometimes reference by caption after a rename; a caption
        match is a resolved field, never a finding."""
        wb = _wb_from_xml(tmp_path, _calc_twb("SUM([Amount])"))
        res = t_calc_003(_ctx(wb), {})
        assert res.status is Status.PASS

    def test_parameter_and_qualified_refs_are_exempt(self, tmp_path):
        """[Parameters].[X] and [Other DS].[Field] resolve outside the parsed
        field list; judging them would manufacture noise."""
        wb = _wb_from_xml(
            tmp_path, _calc_twb("[AMOUNT] * [Parameters].[Rate] + [Blend DS].[Fx]")
        )
        res = t_calc_003(_ctx(wb), {})
        assert res.status is Status.PASS

    def test_brackets_in_strings_and_comments_are_ignored(self, tmp_path):
        wb = _wb_from_xml(
            tmp_path,
            _calc_twb("// uses [OLD_NAME]&#10;IF [REGION] = '[N/A]' THEN 0 ELSE [AMOUNT] END"),
        )
        res = t_calc_003(_ctx(wb), {})
        assert res.status is Status.PASS

    def test_fixture_workbook_is_clean(self, workbook):
        """The representative fixture must not trip the check (noise gate)."""
        res = t_calc_003(_ctx(workbook), {})
        assert res.status is Status.PASS
