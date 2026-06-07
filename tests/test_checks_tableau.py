"""Phase 2 Tableau static family: parser plus the T-* catalog, against a
representative .twb fixture."""

from pathlib import Path

import pytest

from plumb.checks._tableau import parse_workbook
from plumb.checks.tableau_static import (
    t_calc_001,
    t_calc_002,
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

    def test_uncertified_source_is_high_fail(self, workbook):
        res = t_src_003(_ctx(workbook), {})
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
