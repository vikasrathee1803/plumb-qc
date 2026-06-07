# ADR-0006: Additive summary fields for WARN, ERROR, SKIP

Date: 2026-06-07. Status: accepted.

The spec's summary block counts failures by severity plus passed and
total, but mandates that ERROR is surfaced separately and never counts as
a pass. Decision: the Summary model keeps every spec field with identical
semantics (severity buckets count FAILs only) and adds three additive
fields: warned, errored, skipped. total counts all evaluated checks.

Consumers written against the spec contract keep working; consumers that
care about errors have a first-class field instead of inferring from
totals.

Reversibility: costly once external consumers parse the JSON, so this is
locked at Gate 0.
