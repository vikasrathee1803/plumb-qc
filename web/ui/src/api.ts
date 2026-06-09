import type {
  About, CatalogCheck, CheckState, Connection, HistoryRun, LineageGraph, RunResult,
  SnowflakeSettings, TableauSettings, TestResult, Trend,
} from "./types";

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json() as Promise<T>;
}

async function sendJSON<T>(url: string, method: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((j as { detail?: string }).detail ?? `${url}: ${r.status}`);
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

export async function runTableau(
  file: File,
  profile: string | null,
  checks: CheckState[]
): Promise<RunResult> {
  const form = new FormData();
  form.append("workbook", file);
  if (profile) form.append("profile", profile);
  if (checks.length) form.append("checks", JSON.stringify(checks));
  const r = await fetch("/api/check/tableau", { method: "POST", body: form });
  const j = await r.json();
  if (!r.ok) throw new Error(j.detail ?? "check failed");
  return j as RunResult;
}
