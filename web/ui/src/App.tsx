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

export function App() {
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [tab, setTab] = useState<"sql" | "tableau">("sql");
  const [conn, setConn] = useState<Connection>({ configured: false });
  const [profiles, setProfiles] = useState<string[]>([]);
  const [rulesets, setRulesets] = useState<string[]>([]);
  const [version, setVersion] = useState("");
  const [catalog, setCatalog] = useState<CatalogCheck[]>([]);

  useEffect(() => { document.documentElement.setAttribute("data-theme", theme); }, [theme]);
  useEffect(() => {
    fetchConnection().then(setConn).catch(() => undefined);
    fetchProfiles().then((d) => { setProfiles(d.profiles); setVersion(d.ruleset_version); }).catch(() => undefined);
    fetchRulesets().then((d) => setRulesets(d.rulesets)).catch(() => undefined);
    fetchCatalog().then(setCatalog).catch(() => undefined);
  }, []);

  return (
    <>
      <div className="appbar">
        <div className="brand">
          <span className="logo">plumb<span className="dot">.</span></span>
          <span className="tag">BI build QC and confidence engine</span>
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
          <div style={{ marginTop: 14 }}>
            {tab === "sql"
              ? <SqlView conn={conn} profiles={profiles} rulesets={rulesets} catalog={catalog} version={version} />
              : <TableauView profiles={profiles} />}
          </div>
        </div>
      </div>
    </>
  );
}

function SqlView({ conn, profiles, rulesets, catalog, version }: {
  conn: Connection; profiles: string[]; rulesets: string[]; catalog: CatalogCheck[]; version: string;
}) {
  const [sql, setSql] = useState(SAMPLE_SQL);
  const [profile, setProfile] = useState("");
  const [ruleset, setRuleset] = useState("");
  const [live, setLive] = useState(false);
  const [explain, setExplain] = useState(false);
  const [checkState, setCheckState] = useState<Record<string, CheckState>>({});
  const [result, setResult] = useState<RunResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { setLive(conn.configured); }, [conn.configured]);
  useEffect(() => {
    if (!rulesets.length) return;
    setRuleset((r) => r || (rulesets.includes("customer_ltv") ? "customer_ltv" : "plumb"));
  }, [rulesets]);

  // Seed the check config from the chosen ruleset (enabled+params) plus the
  // full catalog (everything else defaults to disabled).
  useEffect(() => {
    if (!ruleset || !catalog.length) return;
    fetchRulesetChecks(ruleset).then((d) => {
      const fromRuleset = new Map(d.checks.map((c) => [c.id, c]));
      const next: Record<string, CheckState> = {};
      for (const c of catalog) {
        if (c.family.startsWith("tableau")) continue;
        const r = fromRuleset.get(c.id);
        next[c.id] = { id: c.id, enabled: r?.enabled ?? false, params: r?.params ?? {} };
      }
      setCheckState(next);
    }).catch(() => undefined);
  }, [ruleset, catalog]);

  const enabledChecks = useMemo(
    () => Object.values(checkState).filter((c) => c.enabled),
    [checkState]
  );

  async function run() {
    setBusy(true); setError(""); setResult(null);
    try {
      const r = await runSql({
        sql, profile: profile || null, rules: ruleset || null,
        static_only: !live, explain,
        checks: Object.values(checkState),
      });
      setResult(r);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally { setBusy(false); }
  }

  return (
    <>
      <textarea value={sql} rows={6} spellCheck={false} onChange={(e) => setSql(e.target.value)} />
      <div className="controls">
        <label className="fld">Check set
          <select value={ruleset} onChange={(e) => setRuleset(e.target.value)}>
            {rulesets.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
        <label className="fld">Profile
          <select value={profile} onChange={(e) => setProfile(e.target.value)}>
            <option value="">(base)</option>
            {profiles.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
        <label className={`toggle ${conn.configured ? "" : "disabled"}`}>
          <input type="checkbox" checked={live} disabled={!conn.configured}
            onChange={(e) => setLive(e.target.checked)} />
          Live (Snowflake)
        </label>
        <label className="toggle">
          <input type="checkbox" checked={explain} onChange={(e) => setExplain(e.target.checked)} />
          Explain failures (AI)
        </label>
        <button className="btn-primary" onClick={run} disabled={busy || !enabledChecks.length}>
          {busy ? <><span className="spin" /> Running</> : `Run ${enabledChecks.length} checks`}
        </button>
      </div>
      {error && <p className="error">{error}</p>}

      <ConfigPanel catalog={catalog} state={checkState} setState={setCheckState} />

      <div style={{ marginTop: 18 }}>
        {result ? <Report result={result} />
          : <div className="card pad empty">Configure your checks and run against
              {conn.configured ? " your live Snowflake" : " parsed SQL"}. Ruleset {version}.</div>}
      </div>
    </>
  );
}

function TableauView({ profiles }: { profiles: string[] }) {
  const [file, setFile] = useState<File | null>(null);
  const [profile, setProfile] = useState("");
  const [result, setResult] = useState<RunResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function run() {
    if (!file) { setError("choose a .twb or .twbx file"); return; }
    setBusy(true); setError(""); setResult(null);
    try { setResult(await runTableau(file, profile || null)); }
    catch (e) { setError(String(e instanceof Error ? e.message : e)); }
    finally { setBusy(false); }
  }

  return (
    <>
      <label className="fld">Workbook (.twb or .twbx)
        <input type="text" readOnly value={file?.name ?? "no file chosen"} style={{ display: "none" }} />
      </label>
      <input type="file" accept=".twb,.twbx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      <div className="controls">
        <label className="fld">Profile
          <select value={profile} onChange={(e) => setProfile(e.target.value)}>
            <option value="">(base)</option>
            {profiles.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
        <button className="btn-primary" onClick={run} disabled={busy}>
          {busy ? <><span className="spin" /> Parsing</> : "Check workbook"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      <div style={{ marginTop: 18 }}>
        {result ? <Report result={result} />
          : <div className="card pad empty">Upload a Tableau workbook to run the T-* catalog.</div>}
      </div>
    </>
  );
}
