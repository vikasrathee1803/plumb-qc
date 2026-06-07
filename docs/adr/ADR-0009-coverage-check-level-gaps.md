# ADR-0009: Check-level coverage gaps

Date: 2026-06-07. Status: accepted.

The spec's coverage block is family-level (families_run,
families_skipped). The Phase 1 acceptance criterion requires a clean run
with no baseline and skipped reconciliation to report both gaps,
explicitly and ranked. Reconciliation (D-RECON-001) lives inside the
assertions family, which otherwise runs, so a family-level model alone
cannot surface "reconciliation skipped".

Decision: add an additive Coverage.checks_skipped list of SkippedCheck
{id, name, family, reason}. It holds enabled checks that returned SKIP
inside a family that otherwise ran. Fully skipped families remain at the
family level and their checks are not duplicated here. Both lists are
ranked by the family risk order (ADR-0002); checks then by id.

coverage_caption() composes the headline qualifier, for example
"limited coverage: reconciliation skipped; regression: no baseline found".

This is additive and backward compatible: the spec JSON example round
trips with checks_skipped defaulting to an empty list.

Reversibility: costly once consumers parse it, so locked now.
