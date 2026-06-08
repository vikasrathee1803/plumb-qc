import type { About, CatalogCheck, CheckState, Connection, RunResult } from "./types";

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json() as Promise<T>;
}

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
