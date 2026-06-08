import { useEffect, useMemo, useState } from "react";
import {
  fetchCatalog, fetchConnection, fetchProfiles, fetchRulesetChecks, fetchRulesets,
  runSql, runTableau,
} from "./api";
import { ConfigPanel } from "./ConfigPanel";
import { Report } from "./Report";
import type { CatalogCheck, CheckState, Connection, RunResult } from "./types";

const SAMPLE_SQL =
  "SELECT customer_id, segment, region, lifetime_revenue, last_order_date\n" +
  "FROM PORTFOLIO_DEMO_DB.ANALYTICS.V_CUSTOMER_LTV";

// Presets seed the check list. A preset is just a starting point you can edit.
interface Preset { id: string; label: string; rules: string; mode?: "all" | "static"; }
const PRESETS: Preset[] = [
  { id: "recommended", label: "Recommended", rules: "plumb" },
  { id: "customer_ltv", label: "Customer LTV (demo)", rules: "customer_ltv" },
  { id: "everything", label: "Everything", rules: "plumb", mode: "all" },
  { id: "minimal", label: "Quick (static only)", rules: "plumb", mode: "static" },
];

// Standards are the team rulebook: how strict the pass/fail bar is.
const STANDARDS: { id: string; label: string; note: string }[] = [
  { id: "", label: "Standard", note: "Balanced. Fails the build on a review-level issue." },
  { id: "finance", label: "Finance (strict)", note: "Strictest gate, hides row samples, 12h freshness." },
  { id: "marketing", label: "Marketing (lenient)", note: "Tolerates 2% nulls and 48h-old data." },
];

export function App() {
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [tab, setTab] = useState<"sql" | "tableau">("sql");
  const [conn, setConn] = useState<Connection>({ configured: false });
  const [profiles, setProfiles] = useState<string[]>([]);
  const [version, setVersion] = useState("");
  const [catalog, setCatalog] = useState<CatalogCheck[]>([]);

  useEffect(() => { document.documentElement.setAttribute("data-theme", theme); }, [theme]);
  useEffect(() => {
    fetchConnection().then(setConn).catch(() => undefined);
    fetchProfiles().then((d) => { setProfiles(d.profiles); setVersion(d.ruleset_version); }).catch(() => undefined);
    fetchRulesets().catch(() => undefined);
    fetchCatalog().then(setCatalog).catch(() => undefined);
  }, []);

  return (
    <>
      <div className="appbar">
        <div className="brand">
          <span className="logo">plumb<span className="dot">.</span></span>
          <span className="tag">prove a build is correct before it ships</span>
        </div>
        <span className="spacer" />
        <span className={`chip-conn ${conn.configured ? "live" : ""}`}>
          <span className="led" />
          {conn.configured ? `${conn.account} · ${conn.warehouse}` : "no Snowflake connection"}
        </span>
        <button className="icon-btn" title="Toggle theme"
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
          {theme === "dark" ? "☀" : "☾"}
        </button>
      </div>

      <div className="shell">
        <div className="card pad">
          <div className="tabs">
            <button className={tab === "sql" ? "on" : ""} onClick={() => setTab("sql")}>SQL</button>
            <button className={tab === "tableau" ? "on" : ""} onClick={() => setTab("tableau")}>Tableau</button>
          </div>
          <div style={{ marginTop: 16 }}>
            {tab === "sql"
              ? <SqlView conn={conn} profiles={profiles} catalog={catalog} version={version} />
              : <TableauView profiles={profiles} catalog={catalog} />}
          </div>
        </div>
      </div>
    </>
  );
}

function HowItWorks({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="explain">
      <button className="explain-btn" onClick={() => setOpen(!open)}>
        How this works {open ? "−" : "+"}
      </button>
      {open && <div className="explain-body">{children}</div>}
    </div>
  );
}

function applyPreset(preset: Preset, catalog: CatalogCheck[], seed: CheckState[]): Record<string, CheckState> {
  const fromRuleset = new Map(seed.map((c) => [c.id, c]));
  const next: Record<string, CheckState> = {};
  for (const c of catalog) {
    if (c.family.startsWith("tableau")) continue;
    let enabled: boolean;
    if (preset.mode === "all") enabled = true;
    else if (preset.mode === "static") enabled = c.family === "static";
    else enabled = fromRuleset.get(c.id)?.enabled ?? false;
    next[c.id] = { id: c.id, enabled, params: fromRuleset.get(c.id)?.params ?? {} };
  }
  return next;
}

function SqlView({ conn, profiles, catalog, version }: {
  conn: Connection; profiles: string[]; catalog: CatalogCheck[]; version: string;
}) {
  const [sql, setSql] = useState(SAMPLE_SQL);
  const [presetId, setPresetId] = useState("customer_ltv");
  const [standard, setStandard] = useState("");
  const [live, setLive] = useState(false);
  const [explain, setExplain] = useState(false);
  const [checkState, setCheckState] = useState<Record<string, CheckState>>({});
  const [result, setResult] = useState<RunResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const preset = PRESETS.find((p) => p.id === presetId) ?? PRESETS[0];
  const sqlCatalog = useMemo(() => catalog.filter((c) => !c.family.startsWith("tableau")), [catalog]);

  useEffect(() => { setLive(conn.configured); }, [conn.configured]);

  // (re)seed the check list whenever the preset or catalog changes.
  useEffect(() => {
    if (!catalog.length) return;
    fetchRulesetChecks(preset.rules)
      .then((d) => setCheckState(applyPreset(preset, catalog, d.checks)))
      .catch(() => setCheckState(applyPreset(preset, catalog, [])));
  }, [presetId, catalog]);

  const enabled = useMemo(
    () => Object.values(checkState).filter((c) => c.enabled && sqlCatalog.some((s) => s.id === c.id)),
    [checkState, sqlCatalog]
  );
  const std = STANDARDS.find((s) => s.id === standard) ?? STANDARDS[0];

  async function run() {
    setBusy(true); setError(""); setResult(null);
    try {
      setResult(await runSql({
        sql, profile: standard || null, rules: preset.rules,
        static_only: !live, explain, checks: Object.values(checkState),
      }));
    } catch (e) { setError(String(e instanceof Error ? e.message : e)); }
    finally { setBusy(false); }
  }

  return (
    <>
      <HowItWorks>
        Plumb runs deterministic checks against your SQL and gives a trusted verdict.
        Pick a <b>preset</b> to start from a sensible set of checks, choose a <b>standard</b> for how
        strict the pass bar is, then fine-tune individual checks below. Turn on <b>Live</b> to run
        against Snowflake; off, it analyzes the SQL without connecting.
      </HowItWorks>

      <label className="fld" style={{ marginTop: 14 }}>Your SQL
        <textarea value={sql} rows={6} spellCheck={false} onChange={(e) => setSql(e.target.value)} />
      </label>

      <div className="picks">
        <div className="pick">
          <div className="pick-label">Preset</div>
          <select value={presetId} onChange={(e) => setPresetId(e.target.value)}>
            {PRESETS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
          <div className="pick-note">A starting set of checks. Edit any of them below.</div>
        </div>
        <div className="pick">
          <div className="pick-label">Standard</div>
          <select value={standard} onChange={(e) => setStandard(e.target.value)}>
            {STANDARDS.filter((s) => s.id === "" || profiles.includes(s.id))
              .map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
          <div className="pick-note">{std.note}</div>
        </div>
      </div>

      <div className="run-bar">
        <label className={`toggle ${conn.configured ? "" : "disabled"}`}>
          <input type="checkbox" checked={live} disabled={!conn.configured}
            onChange={(e) => setLive(e.target.checked)} />
          Live (run against Snowflake)
        </label>
        <label className="toggle">
          <input type="checkbox" checked={explain} onChange={(e) => setExplain(e.target.checked)} />
          Explain failures with AI
        </label>
        <button className="btn-primary" onClick={run} disabled={busy || !enabled.length}>
          {busy ? <><span className="spin" /> Running</> : `Run ${enabled.length} checks`}
        </button>
      </div>
      {error && <p className="error">{error}</p>}

      <ConfigPanel catalog={sqlCatalog} state={checkState} setState={setCheckState} />

      <div style={{ marginTop: 18 }}>
        {result ? <Report result={result} />
          : <div className="card pad empty">
              Ready to run {enabled.length} checks{live ? " against your live Snowflake" : " on the SQL"}. Ruleset {version}.
            </div>}
      </div>
    </>
  );
}

function TableauView({ profiles, catalog }: { profiles: string[]; catalog: CatalogCheck[] }) {
  const [file, setFile] = useState<File | null>(null);
  const [standard, setStandard] = useState("");
  const [checkState, setCheckState] = useState<Record<string, CheckState>>({});
  const [result, setResult] = useState<RunResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const tabCatalog = useMemo(() => catalog.filter((c) => c.family.startsWith("tableau")), [catalog]);

  useEffect(() => {
    if (!tabCatalog.length) return;
    fetchRulesetChecks("plumb").then((d) => {
      const seed = new Map(d.checks.map((c) => [c.id, c]));
      const next: Record<string, CheckState> = {};
      for (const c of tabCatalog) {
        const r = seed.get(c.id);
        next[c.id] = { id: c.id, enabled: r?.enabled ?? true, params: r?.params ?? {} };
      }
      setCheckState(next);
    }).catch(() => undefined);
  }, [tabCatalog]);

  const enabled = useMemo(() => Object.values(checkState).filter((c) => c.enabled), [checkState]);

  async function run() {
    if (!file) { setError("Choose a .twb or .twbx file first."); return; }
    setBusy(true); setError(""); setResult(null);
    try { setResult(await runTableau(file, standard || null, Object.values(checkState))); }
    catch (e) { setError(String(e instanceof Error ? e.message : e)); }
    finally { setBusy(false); }
  }

  return (
    <>
      <HowItWorks>
        Upload a Tableau workbook (.twb or .twbx) and Plumb parses it locally, with no Tableau Server
        access, then runs the workbook checks below. Choose a <b>standard</b> for how strict the verdict is.
      </HowItWorks>

      <label className="fld" style={{ marginTop: 14 }}>Workbook
        <input type="file" accept=".twb,.twbx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      </label>

      <div className="picks">
        <div className="pick">
          <div className="pick-label">Standard</div>
          <select value={standard} onChange={(e) => setStandard(e.target.value)}>
            <option value="">Standard</option>
            {profiles.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <div className="pick-note">How strict the pass/fail bar is.</div>
        </div>
      </div>

      <div className="run-bar">
        <button className="btn-primary" onClick={run} disabled={busy}>
          {busy ? <><span className="spin" /> Parsing</> : `Check workbook (${enabled.length} checks)`}
        </button>
      </div>
      {error && <p className="error">{error}</p>}

      <ConfigPanel catalog={tabCatalog} state={checkState} setState={setCheckState} />

      <div style={{ marginTop: 18 }}>
        {result ? <Report result={result} />
          : <div className="card pad empty">Upload a workbook to run {enabled.length} Tableau checks.</div>}
      </div>
    </>
  );
}
