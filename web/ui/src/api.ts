import type {
  About, CatalogCheck, CheckState, ColumnsInfo, Connection, HistoryRun, LineageGraph, RunResult,
  SnowflakeSettings, TableauSettings, TestResult, Trend,
} from "./types";

// A stale session cookie (a previous `plumb web` launch, or a cached SPA
// shell) makes every API call 401. Before this guard those failures were
// swallowed silently, so the app rendered as "no connection" with zero
// checks and a disabled Run button while the server was perfectly healthy.
// Reloading the shell re-issues the cookie; do it once, then surface the
// error so a real auth problem cannot loop the page.
const RELOAD_FLAG = "plumb_auth_reloaded";
function handleAuthExpired(): never {
  if (!sessionStorage.getItem(RELOAD_FLAG)) {
    sessionStorage.setItem(RELOAD_FLAG, "1");
    window.location.reload();
  }
  throw new Error("Session expired - reload the page.");
}

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (r.status === 401) handleAuthExpired();
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  sessionStorage.removeItem(RELOAD_FLAG);
  return r.json() as Promise<T>;
}

async function sendJSON<T>(url: string, method: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401) handleAuthExpired();
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((j as { detail?: string }).detail ?? `${url}: ${r.status}`);
  sessionStorage.removeItem(RELOAD_FLAG);
  return j as T;
}

export const fetchSnowflakeSettings = () => getJSON<SnowflakeSettings>("/api/settings/snowflake");
export const saveSnowflakeSettings = (b: Record<string, unknown>) =>
  sendJSON<{ ok: boolean }>("/api/settings/snowflake", "POST", b);
export const testSnowflake = () => sendJSON<TestResult>("/api/settings/snowflake/test", "POST", {});
export const deleteSnowflake = () => sendJSON<{ ok: boolean }>("/api/settings/snowflake", "DELETE");
export const fetchTableauSettings = () => getJSON<TableauSettings>("/api/settings/tableau");
export const saveTableauSettings = (b: Record<string, unknown>) =>
  sendJSON<{ ok: boolean }>("/api/settings/tableau", "POST", b);
export const testTableau = () => sendJSON<TestResult>("/api/settings/tableau/test", "POST", {});
export const deleteTableau = () => sendJSON<{ ok: boolean }>("/api/settings/tableau", "DELETE");

export const fetchConnection = () => getJSON<Connection>("/api/connection");
export const fetchAbout = () => getJSON<About>("/api/about");
export const fetchProfiles = () =>
  getJSON<{ ruleset_version: string; profiles: string[] }>("/api/profiles");
export const fetchRulesets = () =>
  getJSON<{ default: string; rulesets: string[] }>("/api/rulesets");
export const fetchCatalog = () =>
  getJSON<{ checks: CatalogCheck[] }>("/api/checks").then((d) => d.checks);
export const fetchRulesetChecks = (name: string) =>
  getJSON<{ version: string; checks: CheckState[] }>(`/api/ruleset?name=${encodeURIComponent(name)}`);
export const fetchProfileChanges = (name: string) =>
  getJSON<{ name: string; changes: string[] }>(`/api/profile?name=${encodeURIComponent(name)}`);
export const fetchHistory = (opts?: { limit?: number; q?: string }) => {
  const p = new URLSearchParams();
  if (opts?.limit != null) p.set("limit", String(opts.limit));
  if (opts?.q) p.set("q", opts.q);
  const qs = p.toString();
  return getJSON<{ runs: HistoryRun[]; total: number; matched: number }>(
    `/api/history${qs ? `?${qs}` : ""}`
  );
};
export const fetchRun = (id: string) => getJSON<RunResult>(`/api/run/${id}`);
export const fetchTrend = (target: string) =>
  getJSON<Trend>(`/api/trend?target=${encodeURIComponent(target)}`);

export async function fetchColumns(sql: string): Promise<ColumnsInfo> {
  return sendJSON<ColumnsInfo>("/api/columns", "POST", { sql });
}

export const saveBaseline = (sql: string) =>
  sendJSON<{ ok: boolean; name: string; rows: number }>("/api/baseline/save", "POST", { sql });

export async function fetchLineage(sql: string): Promise<LineageGraph> {
  const r = await fetch("/api/lineage", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sql }),
  });
  const j = await r.json();
  if (!r.ok) throw new Error(j.detail ?? "could not build map");
  return j as LineageGraph;
}

export async function runSql(body: {
  sql: string;
  profile: string | null;
  rules: string | null;
  static_only: boolean;
  explain: boolean;
  checks: CheckState[];
}): Promise<RunResult> {
  const r = await fetch("/api/check/sql", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = await r.json();
  if (!r.ok) throw new Error(j.detail ?? "check failed");
  return j as RunResult;
}

export interface ParityRunResponse {
  results: RunResult[];
  stopped_after_snapshot: boolean;
}

export interface ParityDemoInfo {
  available: boolean;
  workbook: string;
  maps: string[];
}

export const fetchParityDemo = () => getJSON<ParityDemoInfo>("/api/parity/demo");

// Demo assets arrive as real File objects so the demo drives the exact
// same inputs and code path a user's own upload would.
export async function fetchDemoFile(url: string, name: string): Promise<File> {
  const r = await fetch(url);
  if (r.status === 401) handleAuthExpired();
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  sessionStorage.removeItem(RELOAD_FLAG);
  return new File([await r.blob()], name);
}

export interface WorkbookRelation {
  datasource: string;
  kind: string;
  fqn: string | null;
  label: string;
  refusal_reason: string | null;
}

export async function fetchWorkbookSources(file: File): Promise<WorkbookRelation[]> {
  const form = new FormData();
  form.append("workbook", file);
  const r = await fetch("/api/parity/sources", { method: "POST", body: form });
  if (r.status === 401) handleAuthExpired();
  const j = await r.json().catch(() => ({}) as { detail?: string });
  if (!r.ok) throw new Error((j as { detail?: string }).detail ?? "could not read the workbook");
  return (j as { relations: WorkbookRelation[] }).relations;
}

export const buildParityMap = (payload: unknown) =>
  sendJSON<{ yaml: string }>("/api/parity/map/build", "POST", payload).then((d) => d.yaml);

export async function runParity(
  file: File,
  map: File | null,
  opts: { mode: "snapshot" | "check" | "run"; static_only: boolean; post_swap: boolean }
): Promise<ParityRunResponse> {
  const form = new FormData();
  form.append("workbook", file);
  if (map) form.append("map_file", map);
  form.append("mode", opts.mode);
  form.append("static_only", String(opts.static_only));
  form.append("post_swap", String(opts.post_swap));
  let r: Response;
  try {
    r = await fetch("/api/parity/run", { method: "POST", body: form });
  } catch {
    throw new Error(
      "Could not reach the server. The workbook may be too large, or your session " +
        "expired - reload the page and try again."
    );
  }
  if (r.status === 401) handleAuthExpired();
  const j = await r.json().catch(() => ({}) as { detail?: string });
  if (!r.ok) throw new Error((j as { detail?: string }).detail ?? "parity run failed");
  return j as ParityRunResponse;
}

export async function runTableau(
  file: File,
  profile: string | null,
  checks: CheckState[]
): Promise<RunResult> {
  const form = new FormData();
  form.append("workbook", file);
  if (profile) form.append("profile", profile);
  if (checks.length) form.append("checks", JSON.stringify(checks));
  let r: Response;
  try {
    r = await fetch("/api/check/tableau", { method: "POST", body: form });
  } catch {
    throw new Error(
      "Could not reach the server. The workbook may be too large, or your session " +
        "expired - reload the page and try again."
    );
  }
  if (r.status === 401) handleAuthExpired();
  const j = await r.json().catch(() => ({}) as { detail?: string });
  if (!r.ok) throw new Error((j as { detail?: string }).detail ?? "check failed");
  return j as RunResult;
}
