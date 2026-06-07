# ADR-0002: Coverage gap risk ranking

Date: 2026-06-07. Status: accepted.

The spec requires skipped check families to be ranked so the analyst sees
the most important unchecked risk first, but does not define the order.
Decision: a fixed order in plumb/engine/verdict.py (FAMILY_RISK_ORDER):

assertions > regression > metadata > static > performance >
tableau_static > tableau_live

Rationale: assertions catch the failure modes Plumb exists for (fan-out,
grain errors, reconciliation drift). Regression is the confidence
centerpiece. Metadata errors also surface loudly at execution time, so an
unchecked metadata family is less silently dangerous. Static is
preventive. Performance is advisory.

Reversibility: cheap. One tuple, one test.
