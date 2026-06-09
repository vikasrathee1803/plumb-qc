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
  build_notes?: string[];
}

export interface ParamHint {
  name: string;
  type: "list" | "str" | "int" | "float" | "bool" | "sql";
  required?: boolean;
}

export interface CatalogCheck {
  id: string;
  name: string;
  family: string;
  default_severity: string;
  execution_type: string;
  description: string;
  params: ParamHint[];
}

export interface Connection {
  configured: boolean;
  account?: string;
  warehouse?: string;
  role?: string;
  user?: string;
  privileged_role?: boolean;
}

export interface About {
  version: string;
  total_checks: number;
  families: { family: string; count: number }[];
  connection: { configured: boolean; account?: string; warehouse?: string };
  ai_ready: boolean;
  verdict_tiers: string[];
  invariants: string[];
  stack: { group: string; items: { name: string; version: string }[] }[];
}

// One configurable check in the UI: enabled flag plus param values keyed by name.
export interface CheckState {
  id: string;
  enabled: boolean;
  params: Record<string, unknown>;
}

// A user-authored assertion: a SQL query whose returned rows are violations.
export interface CustomCheck {
  name: string;
  severity: string;
  sql: string;
}

export interface HistoryRun {
  run_id: string;
  verdict: string;
  target: string;
  type: string;
  timestamp: string;
  checks: number;
}

export interface TrendPoint {
  run_id: string;
  verdict: string;
  timestamp: string;
  passed: number;
  failed: number;
}

export interface Trend {
  target: string;
  points: TrendPoint[];
  total: number;
  ready_or_better: number;
}

export interface LineageNode {
  id: string;
  label: string;
  kind: "table" | "cte" | "subquery" | "output" | "values";
  detail: string;
  flags: string[];
  columns: string[];
  calculations: string[];
  filters: string[];
}

export interface SnowflakeSettings {
  configured: boolean;
  account?: string;
  user?: string;
  authenticator?: string;
  private_key_path?: string | null;
  role?: string;
  warehouse?: string;
  privileged_role?: boolean;
  has_passphrase?: boolean;
  has_oauth_token?: boolean;
  has_pat?: boolean;
}

export interface TableauSettings {
  configured: boolean;
  server?: string;
  site?: string;
  auth?: string;
  pat_name?: string;
  client_id?: string;
  secret_id?: string;
  username?: string;
  has_secret?: boolean;
}

export interface TestResult {
  ok: boolean;
  role?: string;
  warehouse?: string;
  site?: string;
  error?: string;
}

export interface ColumnsInfo {
  columns: string[];
  suggestions: { key?: string[]; timestamp?: string[]; amount?: string[] };
}

export interface ColumnLink {
  from_col: string;
  to_col: string;
}

export interface LineageEdge {
  source: string;
  target: string;
  relation: string;
  on: string | null;
  risk: boolean;
  columns: ColumnLink[];
}

export interface LineageGraph {
  nodes: LineageNode[];
  edges: LineageEdge[];
  risks: string[];
  build_notes?: string[];
}
