// Mirrors plumb.engine.models.RunResult, the one contract every Plumb
// surface consumes. The SPA only renders this; it computes no verdict.

export type Verdict = "BLOCKED" | "REVIEW" | "READY_WITH_NOTES" | "READY";
export type Status = "PASS" | "FAIL" | "WARN" | "SKIP" | "ERROR";

export interface CheckResult {
  id: string;
  name: string;
  family: string;
  severity: string;
  status: Status;
  observed: string | null;
  expected: string | null;
  remediation: string | null;
  ai_explanation: string | null;
  evidence: { query: string | null; sample_rows: Record<string, unknown>[] };
}

export interface RunResult {
  run_id: string;
  timestamp: string;
  target: { type: string; name: string; source_ref: string | null };
  ruleset_version: string;
  profile: string | null;
  verdict: Verdict;
  coverage: {
    families_run: string[];
    families_skipped: { family: string; reason: string }[];
    checks_skipped: { id: string; name: string; family: string; reason: string }[];
  };
  summary: Record<string, number>;
  checks: CheckResult[];
  environment: { warehouse: string | null; role: string | null; query_tag: string | null };
}
