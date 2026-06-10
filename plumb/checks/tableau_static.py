"""Phase 2: Tableau workbook static analysis (T-* catalog).

Parses a .twb or .twbx with lxml (ADR-0011) and checks it against the
ruleset. No execution, no Tableau Server access. Each check reads the
parsed TableauWorkbook from the context; with no workbook it SKIPs, which
surfaces in coverage. Definitive faults FAIL, heuristics WARN, consistent
with the SQL static policy (ADR-0010).
"""

from __future__ import annotations

import re

from plumb.checks import _tableau
from plumb.checks._base import build_result
from plumb.engine.models import CheckFamily, ExecutionType, Severity, Status
from plumb.engine.registry import CheckContext, register_check

# Formula scrubbing for T-CALC-003. Refs are judged only after string
# literals, comments, and QUALIFIED refs ([Datasource].[Field] /
# [Parameters].[X]) are removed: qualified refs point outside the parsed
# field list (the parser deliberately drops the Parameters datasource, and
# blends cross datasources), so judging them would manufacture noise.
_TCALC_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_TCALC_LINE_COMMENT = re.compile(r"//[^\n]*")
_TCALC_STRING_LIT = re.compile(r"'[^']*'|\"[^\"]*\"")
_TCALC_QUALIFIED_REF = re.compile(r"\[[^\]]+\]\s*\.\s*\[[^\]]+\]")
# Tableau's internal name for a (renamed) calculated field. These can never
# come from a database, so an unresolved one is definitively a deleted calc
# — the only reference class judgeable statically with zero noise (plain DB
# columns are usually NOT materialized as <column> elements in the XML, so
# a bare unknown ref is normal, not a finding).
_TCALC_INTERNAL_CALC = re.compile(r"^\[Calculation_[0-9]+\]$")


def _formula_refs(formula: str) -> set[str]:
    """The bare [Field] references a formula resolves locally."""
    text = _TCALC_BLOCK_COMMENT.sub(" ", formula)
    text = _TCALC_LINE_COMMENT.sub(" ", text)
    text = _TCALC_STRING_LIT.sub(" ", text)
    text = _TCALC_QUALIFIED_REF.sub(" ", text)
    return set(_tableau._FIELD_REF.findall(text))


def _workbook(ctx: CheckContext, check_id: str):
    if ctx.workbook is None:
        return None, build_result(
            ctx, check_id, Status.SKIP, observed="no Tableau workbook provided"
        )
    return ctx.workbook, None


@register_check(
    check_id="T-SRC-001",
    name="Custom SQL present; prefer a certified view",
    family=CheckFamily.TABLEAU_STATIC,
    default_severity=Severity.MEDIUM,
    execution_type=ExecutionType.STATIC,
)
def t_src_001(ctx: CheckContext, params: dict):
    wb, skip = _workbook(ctx, "T-SRC-001")
    if skip is not None:
        return skip
    with_custom = [ds.caption for ds in wb.datasources if ds.custom_sql]
    if with_custom:
        return build_result(
            ctx, "T-SRC-001", Status.WARN,
            observed=f"custom SQL in: {', '.join(with_custom)}",
            expected="build on a certified view or published source",
            remediation="Replace custom SQL with a governed view so logic is shared and tested.",
        )
    return build_result(ctx, "T-SRC-001", Status.PASS, observed="no custom SQL")


@register_check(
    check_id="T-SRC-002",
    name="Live vs extract; extract refresh staleness",
    family=CheckFamily.TABLEAU_STATIC,
    default_severity=Severity.MEDIUM,
    execution_type=ExecutionType.STATIC,
)
def t_src_002(ctx: CheckContext, params: dict):
    wb, skip = _workbook(ctx, "T-SRC-002")
    if skip is not None:
        return skip
    extracts = [ds.caption for ds in wb.datasources if ds.has_extract]
    if extracts:
        return build_result(
            ctx, "T-SRC-002", Status.WARN,
            observed=f"extract data source(s): {', '.join(extracts)}",
            expected="confirm extract refresh schedule meets the freshness SLA",
            remediation="An extract can serve stale data; verify its refresh cadence.",
        )
    return build_result(ctx, "T-SRC-002", Status.PASS, observed="all sources live")


@register_check(
    check_id="T-SRC-003",
    name="Uses a certified or published data source",
    family=CheckFamily.TABLEAU_STATIC,
    default_severity=Severity.HIGH,
    execution_type=ExecutionType.STATIC,
)
def t_src_003(ctx: CheckContext, params: dict):
    wb, skip = _workbook(ctx, "T-SRC-003")
    if skip is not None:
        return skip
    certified = {c.upper() for c in (getattr(ctx.ruleset, "certified_sources", []) or [])}
    uncertified = [
        ds.caption
        for ds in wb.datasources
        if not ds.is_published and ds.caption.upper() not in certified
    ]
    if uncertified:
        # Without a configured certification list this check cannot fully
        # assert governance — a hard HIGH fail on every unconfigured team's
        # first run is alert fatigue, not QC (and the SQL-side counterpart
        # S-META-004 skips when unconfigured). WARN until the team declares
        # certified_sources; with a list configured, the gate is hard.
        if not certified:
            return build_result(
                ctx, "T-SRC-003", Status.WARN,
                observed=(
                    f"non-published source(s): {', '.join(uncertified)} "
                    "(no certified_sources configured)"
                ),
                expected="every data source is published or certified",
                remediation="Publish the sources, or declare certified_sources in the "
                "ruleset to make this a hard gate.",
            )
        return build_result(
            ctx, "T-SRC-003", Status.FAIL,
            observed=f"non-published, non-certified source(s): {', '.join(uncertified)}",
            expected="every data source is published or certified",
            remediation="Publish the source to Tableau Server or point at a certified one.",
        )
    return build_result(
        ctx, "T-SRC-003", Status.PASS, observed="all sources published or certified"
    )


@register_check(
    check_id="T-LOD-001",
    name="FIXED LOD inventory and double-count risk",
    family=CheckFamily.TABLEAU_STATIC,
    default_severity=Severity.HIGH,
    execution_type=ExecutionType.STATIC,
)
def t_lod_001(ctx: CheckContext, params: dict):
    wb, skip = _workbook(ctx, "T-LOD-001")
    if skip is not None:
        return skip
    fixed = [f for f in wb.calculated_fields() if _tableau.has_fixed_lod(f.formula or "")]
    if fixed:
        rows = [
            {"field": f.caption, "datasource": f.datasource, "formula": f.formula}
            for f in fixed
        ]
        return build_result(
            ctx, "T-LOD-001", Status.WARN,
            observed=f"{len(fixed)} FIXED LOD calc(s): {', '.join(f.caption for f in fixed[:4])}",
            expected="confirm each FIXED LOD does not double count when blended or filtered",
            evidence_rows=rows,
            remediation="FIXED ignores viz filters; verify totals against the database grain.",
        )
    return build_result(ctx, "T-LOD-001", Status.PASS, observed="no FIXED LOD calcs")


@register_check(
    check_id="T-CALC-001",
    name="Aggregation inside a calc that may mismatch DB grain",
    family=CheckFamily.TABLEAU_STATIC,
    default_severity=Severity.MEDIUM,
    execution_type=ExecutionType.STATIC,
)
def t_calc_001(ctx: CheckContext, params: dict):
    wb, skip = _workbook(ctx, "T-CALC-001")
    if skip is not None:
        return skip
    risky = [
        f for f in wb.calculated_fields()
        if _tableau.aggregation_over_arithmetic(f.formula or "")
    ]
    if risky:
        rows = [{"field": f.caption, "formula": f.formula} for f in risky]
        return build_result(
            ctx, "T-CALC-001", Status.WARN,
            observed=f"{len(risky)} calc(s) aggregate a per-row ratio/product: "
            f"{', '.join(f.caption for f in risky[:4])}",
            expected="aggregate base measures, compute ratios at the right grain",
            evidence_rows=rows,
            remediation="Averaging a row-level ratio rarely matches the database grain.",
        )
    return build_result(ctx, "T-CALC-001", Status.PASS, observed="no grain-mismatch calc smell")


@register_check(
    check_id="T-CALC-002",
    name="Hardcoded values in calcs or filters",
    family=CheckFamily.TABLEAU_STATIC,
    default_severity=Severity.MEDIUM,
    execution_type=ExecutionType.STATIC,
)
def t_calc_002(ctx: CheckContext, params: dict):
    wb, skip = _workbook(ctx, "T-CALC-002")
    if skip is not None:
        return skip
    hardcoded = [
        f for f in wb.calculated_fields()
        if _tableau.has_hardcoded_literal(f.formula or "")
    ]
    if hardcoded:
        rows = [{"field": f.caption, "formula": f.formula} for f in hardcoded]
        return build_result(
            ctx, "T-CALC-002", Status.WARN,
            observed=f"{len(hardcoded)} calc(s) with hardcoded literals: "
            f"{', '.join(f.caption for f in hardcoded[:4])}",
            expected="drive thresholds and dates from parameters",
            evidence_rows=rows,
            remediation="Hardcoded numbers and dates silently go stale; parameterize them.",
        )
    return build_result(ctx, "T-CALC-002", Status.PASS, observed="no hardcoded literals in calcs")


@register_check(
    check_id="T-NAME-001",
    name="Field and data source naming conventions",
    family=CheckFamily.TABLEAU_STATIC,
    default_severity=Severity.LOW,
    execution_type=ExecutionType.STATIC,
)
def t_name_001(ctx: CheckContext, params: dict):
    wb, skip = _workbook(ctx, "T-NAME-001")
    if skip is not None:
        return skip
    import re

    naming = getattr(ctx.ruleset, "naming", None)
    pattern = getattr(naming, "tableau_field_regex", None) if naming else None
    if not pattern:
        return build_result(
            ctx, "T-NAME-001", Status.SKIP, observed="no tableau_field_regex configured"
        )
    compiled = re.compile(pattern)
    # Only calculated fields are author-named; physical columns inherit DB names.
    offenders = [f.caption for f in wb.calculated_fields() if not compiled.match(f.caption)]
    if offenders:
        return build_result(
            ctx, "T-NAME-001", Status.WARN,
            observed=f"{len(offenders)} field(s) break naming: {', '.join(offenders[:10])}",
            expected=f"field captions match {pattern}",
            remediation="Rename calculated fields to the team convention.",
        )
    return build_result(ctx, "T-NAME-001", Status.PASS, observed="field names conform")


@register_check(
    check_id="T-UNUSED-001",
    name="Unused fields, data sources, or sheets",
    family=CheckFamily.TABLEAU_STATIC,
    default_severity=Severity.LOW,
    execution_type=ExecutionType.STATIC,
)
def t_unused_001(ctx: CheckContext, params: dict):
    wb, skip = _workbook(ctx, "T-UNUSED-001")
    if skip is not None:
        return skip
    used: set[str] = set()
    for ws in wb.worksheets:
        used |= ws.referenced_fields
    # A field is also "used" if another field's formula references it.
    formulas = " ".join(f.formula or "" for f in wb.calculated_fields())
    unused = [
        f.caption for f in wb.calculated_fields()
        if f.name not in used and f.name not in formulas
    ]
    if unused:
        return build_result(
            ctx, "T-UNUSED-001", Status.WARN,
            observed=f"{len(unused)} calculated field(s) not used in any sheet",
            expected="remove or use calculated fields",
            remediation="Unused calcs add maintenance cost and confuse reviewers.",
        )
    return build_result(ctx, "T-UNUSED-001", Status.PASS, observed="no unused calculated fields")


@register_check(
    check_id="T-CALC-003",
    name="Calculation references a deleted calculation",
    family=CheckFamily.TABLEAU_STATIC,
    default_severity=Severity.MEDIUM,
    execution_type=ExecutionType.STATIC,
)
def t_calc_003(ctx: CheckContext, params: dict):
    """A formula referencing another calc by its internal name
    ([Calculation_NNN]) where no such calc exists: the referenced calc was
    deleted and this formula is broken (Tableau shows it as invalid).
    Deliberately judges ONLY internal calc names — bare references like
    [Amount] are usually database columns that the .twb never materializes
    as <column> elements, so judging them would manufacture noise. String
    literals, comments, and qualified/parameter references are exempt; a
    FAIL here is always a real defect."""
    wb, skip = _workbook(ctx, "T-CALC-003")
    if skip is not None:
        return skip
    defined: set[str] = set()
    for f in wb.fields:
        if f.name:
            defined.add(f.name if f.name.startswith("[") else f"[{f.name}]")
        if f.caption:
            defined.add(f"[{f.caption}]")
    rows = []
    for f in wb.calculated_fields():
        for ref in sorted(_formula_refs(f.formula or "") - defined):
            if _TCALC_INTERNAL_CALC.match(ref):
                rows.append(
                    {"calculation": f.caption, "datasource": f.datasource, "missing": ref}
                )
    if rows:
        missing = sorted({r["missing"] for r in rows})
        shown = ", ".join(missing[:6]) + (" …" if len(missing) > 6 else "")
        return build_result(
            ctx, "T-CALC-003", Status.FAIL,
            observed=f"{len(rows)} reference(s) to deleted calculation(s): {shown}",
            expected="every calculation a formula references still exists",
            evidence_rows=rows[:10],
            remediation="The referenced calculated field was deleted; Tableau shows "
            "this formula as invalid. Restore the calc or remove the reference "
            "before publishing.",
        )
    return build_result(
        ctx, "T-CALC-003", Status.PASS,
        observed=f"{len(wb.calculated_fields())} calculation(s), no deleted-calc references",
    )


@register_check(
    check_id="T-FMT-001",
    name="Number and date format consistency across sheets",
    family=CheckFamily.TABLEAU_STATIC,
    default_severity=Severity.LOW,
    execution_type=ExecutionType.STATIC,
)
def t_fmt_001(ctx: CheckContext, params: dict):
    wb, skip = _workbook(ctx, "T-FMT-001")
    if skip is not None:
        return skip
    # Per-field display format lives in worksheet style rules that vary widely
    # by Tableau version; Plumb does not assert it in Phase 2 rather than emit
    # a misleading result. Honest skip so coverage shows the gap.
    return build_result(
        ctx, "T-FMT-001", Status.SKIP,
        observed="format consistency not analyzed in Phase 2",
    )


@register_check(
    check_id="T-FILT-001",
    name="Quick filter count and performance smells",
    family=CheckFamily.TABLEAU_STATIC,
    default_severity=Severity.LOW,
    execution_type=ExecutionType.STATIC,
)
def t_filt_001(ctx: CheckContext, params: dict):
    wb, skip = _workbook(ctx, "T-FILT-001")
    if skip is not None:
        return skip
    threshold = int(params.get("max_filters", 12))
    if wb.filter_count > threshold:
        return build_result(
            ctx, "T-FILT-001", Status.WARN,
            observed=f"{wb.filter_count} filters across sheets (> {threshold})",
            expected=f"<= {threshold} filters",
            remediation="Many quick filters slow rendering; consolidate or use parameters.",
        )
    return build_result(
        ctx, "T-FILT-001", Status.PASS, observed=f"{wb.filter_count} filters"
    )


@register_check(
    check_id="T-RLS-001",
    name="Row-level security calc present where required",
    family=CheckFamily.TABLEAU_STATIC,
    default_severity=Severity.HIGH,
    execution_type=ExecutionType.STATIC,
)
def t_rls_001(ctx: CheckContext, params: dict):
    wb, skip = _workbook(ctx, "T-RLS-001")
    if skip is not None:
        return skip
    if not params.get("required"):
        return build_result(
            ctx, "T-RLS-001", Status.SKIP, observed="RLS not required by this profile"
        )
    has_rls = any(_tableau.has_rls_function(f.formula or "") for f in wb.calculated_fields())
    if has_rls:
        return build_result(
            ctx, "T-RLS-001", Status.PASS, observed="row-level security calc present"
        )
    return build_result(
        ctx, "T-RLS-001", Status.FAIL,
        observed="no row-level security calc found",
        expected="a USERNAME/ISMEMBEROF based security calc",
        remediation="This profile requires RLS; add a user-based security filter.",
    )


@register_check(
    check_id="T-TOTAL-001",
    name="Grand totals applied to a non-additive measure",
    family=CheckFamily.TABLEAU_STATIC,
    default_severity=Severity.MEDIUM,
    execution_type=ExecutionType.STATIC,
)
def t_total_001(ctx: CheckContext, params: dict):
    wb, skip = _workbook(ctx, "T-TOTAL-001")
    if skip is not None:
        return skip
    totals_on = any(ws.has_grand_totals for ws in wb.worksheets)
    if not totals_on:
        return build_result(ctx, "T-TOTAL-001", Status.PASS, observed="no grand totals enabled")
    non_additive = [
        f.caption for f in wb.calculated_fields()
        if _tableau.aggregation_over_arithmetic(f.formula or "")
        or (f.formula or "").upper().startswith("AVG(")
    ]
    if non_additive:
        joined = ", ".join(non_additive[:10])
        return build_result(
            ctx, "T-TOTAL-001", Status.WARN,
            observed=f"grand totals on with non-additive measure(s): {joined}",
            expected="do not grand-total averages or ratios",
            remediation="A grand total of an average is not the average of the whole; use a LOD.",
        )
    return build_result(
        ctx, "T-TOTAL-001", Status.PASS, observed="grand totals on additive measures"
    )
