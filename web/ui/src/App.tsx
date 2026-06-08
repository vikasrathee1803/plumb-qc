import { useEffect, useMemo, useRef, useState } from "react";
import {
  fetchAbout, fetchCatalog, fetchConnection, fetchHistory, fetchProfileChanges,
  fetchProfiles, fetchRun, fetchRulesetChecks, runSql, runTableau,
} from "./api";
import { Architecture } from "./Architecture";
import { ChecksEditor, CustomChecksEditor } from "./Customize";
import { HistoryModal, RecentRuns } from "./History";
import { LineageMap } from "./Lineage";
import { Report } from "./Report";
import { Drawer, Segmented, SwitchRow } from "./ui";
import type { About, CatalogCheck, CheckState, Connection, CustomCheck, HistoryRun, RunResult } from "./types";

const SAMPLE_SQL =
  "SELECT customer_id, segment, region, lifetime_revenue, last_order_date\n" +
  "FROM PORTFOLIO_DEMO_DB.ANALYTICS.V_CUSTOMER_LTV";

interface Preset { id: string; label: string; rules: string; mode?: "all" | "static"; }
const PRESETS: Preset[] = [
  { id: "recommended", label: "Recommended", rules: "plumb" },
  { id: "customer_ltv", label: "Customer LTV demo", rules: "customer_ltv" },
  { id: "everything", label: "Everything", rules: "plumb", mode: "all" },
  { id: "minimal", label: "Quick", rules: "plumb", mode: "static" },
];

function useHistory(): { runs: HistoryRun[]; total: number; refresh: () => void } {
  const [runs, setRuns] = useState<HistoryRun[]>([]);
  const [total, setTotal] = useState(0);
  const refresh = () => {
    fetchHistory({ limit: 3 }).then((d) => { setRuns(d.runs); setTotal(d.total); }).catch(() => undefined);
  };
  useEffect(refresh, []);
  return { runs, total, refresh };
}

export function App() {
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [tab, setTab] = useState<"sql" | "tableau">("sql");
  const [conn, setConn] = useState<Connection>({ configured: false });
  const [profiles, setProfiles] = useState<string[]>([]);
  const [catalog, setCatalog] = useState<CatalogCheck[]>([]);
  const [about, setAbout] = useState<About | null>(null);
  const [archOpen, setArchOpen] = useState(false);

  useEffect(() => { document.documentElement.setAttribute("data-theme", theme); }, [theme]);
  useEffect(() => {
    fetchConnection().then(setConn).catch(() => undefined);
    fetchProfiles().then((d) => setProfiles(d.profiles)).catch(() => undefined);
    fetchCatalog().then(setCatalog).catch(() => undefined);
    fetchAbout().then(setAbout).catch(() => undefined);
  }, []);

  return (
    <>
      <div className="topbar">
        <span className="wordmark">plumb<span className="dot">.</span></span>
        <span className="spacer" />
        <span className={`conn ${conn.configured ? "live" : ""}`}>
          <span className="led" />{conn.configured ? `${conn.account}` : "no connection"}
        </span>
        <button className="iconbtn" title="How Plumb works" aria-label="How Plumb works" onClick={() => setArchOpen(true)}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="5" cy="6" r="2" /><circle cx="5" cy="18" r="2" /><circle cx="19" cy="12" r="2" />
            <path d="M7 6h6a3 3 0 0 1 3 3v1M7 18h6a3 3 0 0 0 3-3v-1" />
          </svg>
        </button>
        <button className="iconbtn" aria-label="Toggle theme" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
          {theme === "dark" ? "☀" : "☾"}
        </button>
      </div>

      <Architecture open={archOpen} onClose={() => setArchOpen(false)} about={about} />

      <div className="stage">
        <h1 className="h1">Prove it before you ship.</h1>
        <p className="sub">Run trusted checks on a SQL build or a Tableau workbook in seconds.</p>
        <div style={{ display: "flex", justifyContent: "center", marginBottom: 18 }}>
          <Segmented value={tab} onChange={setTab}
            options={[{ value: "sql", label: "SQL" }, { value: "tableau", label: "Tableau" }]} />
        </div>
        {tab === "sql"
          ? <SqlView conn={conn} profiles={profiles} catalog={catalog} />
          : <TableauView profiles={profiles} catalog={catalog} />}
      </div>
    </>
  );
}

function StandardPicker({ standard, setStandard, profiles }: {
  standard: string; setStandard: (s: string) => void; profiles: string[];
}) {
  const [changes, setChanges] = useState<string[]>([]);
  useEffect(() => {
    if (!standard) { setChanges(["The team default. Balanced gate, standard thresholds."]); return; }
    fetchProfileChanges(standard).then((d) => setChanges(d.changes)).catch(() => setChanges([]));
  }, [standard]);
  return (
    <>
      <div className="dgroup-label">Standard</div>
      <div className="preset-pills">
        <button className={`pill-btn ${standard === "" ? "on" : ""}`} onClick={() => setStandard("")}>Standard</button>
        {profiles.map((p) => (
          <button key={p} className={`pill-btn ${standard === p ? "on" : ""}`}
            onClick={() => setStandard(p)}>{p}</button>
        ))}
      </div>
      <ul className="std-changes">{changes.map((c, i) => <li key={i}>{c}</li>)}</ul>
    </>
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

function SqlView({ conn, profiles, catalog }: { conn: Connection; profiles: string[]; catalog: CatalogCheck[] }) {
  const [sql, setSql] = useState(SAMPLE_SQL);
  const [presetId, setPresetId] = useState("customer_ltv");
  const [standard, setStandard] = useState("");
  const [live, setLive] = useState(false);
  const [explain, setExplain] = useState(false);
  const [checkState, setCheckState] = useState<Record<string, CheckState>>({});
  const [custom, setCustom] = useState<CustomCheck[]>([]);
  const [drawer, setDrawer] = useState(false);
  const [mapOpen, setMapOpen] = useState(false);
  const [result, setResult] = useState<RunResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const { runs: history, total: historyTotal, refresh: refreshHistory } = useHistory();
  const [historyOpen, setHistoryOpen] = useState(false);

  const preset = PRESETS.find((p) => p.id === presetId) ?? PRESETS[0];
  const sqlCatalog = useMemo(() => catalog.filter((c) => !c.family.startsWith("tableau")), [catalog]);
  const standardLabel = standard || "Standard";

  useEffect(() => { setLive(conn.configured); }, [conn.configured]);
  useEffect(() => {
    if (!catalog.length) return;
    fetchRulesetChecks(preset.rules)
      .then((d) => setCheckState(applyPreset(preset, catalog, d.checks)))
      .catch(() => setCheckState(applyPreset(preset, catalog, [])));
  }, [presetId, catalog]);

  const validCustom = custom.filter((c) => c.name.trim() && c.sql.trim());
  const enabledCount = useMemo(
    () => Object.values(checkState).filter((c) => c.enabled && sqlCatalog.some((s) => s.id === c.id)).length + validCustom.length,
    [checkState, sqlCatalog, validCustom.length]
  );

  async function run() {
    setBusy(true); setError(""); setResult(null);
    try {
      const customSpecs: CheckState[] = validCustom.map((c) => ({
        id: "D-CUSTOM-001", enabled: true, params: { name: c.name, sql: c.sql, severity: c.severity },
      }));
      const r = await runSql({
        sql, profile: standard || null, rules: preset.rules,
        static_only: !live, explain, checks: [...Object.values(checkState), ...customSpecs],
      });
      setResult(r); refreshHistory();
    } catch (e) { setError(String(e instanceof Error ? e.message : e)); }
    finally { setBusy(false); }
  }
  const runRef = useRef(run); runRef.current = run;
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); runRef.current(); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  return (
    <>
      <div className="panel">
        <label className="field">
          <span className="lab">Your SQL build</span>
          <textarea value={sql} rows={6} spellCheck={false} onChange={(e) => setSql(e.target.value)} />
        </label>
        <div className="setup">
          <span className="desc"><b>{enabledCount}</b> checks · <b>{preset.label}</b> preset · <b>{standardLabel}</b> standard</span>
          <button className="linkbtn" onClick={() => setMapOpen(true)}>Map</button>
          <button className="linkbtn" onClick={() => setDrawer(true)}>Customize</button>
        </div>
        <div className="toggles">
          <SwitchRow checked={live} onChange={setLive} disabled={!conn.configured}
            label={conn.configured ? "Run live against Snowflake" : "Live (no connection)"} />
          <SwitchRow checked={explain} onChange={setExplain} label="Explain failures with AI" />
        </div>
        <button className="run" onClick={run} disabled={busy || !enabledCount}>
          {busy ? <><span className="spin" />Running</> : <>Run {enabledCount} checks <kbd>⌘↵</kbd></>}
        </button>
        {error && <p className="error">{error}</p>}
      </div>

      <RecentRuns runs={history} total={historyTotal} onShowAll={() => setHistoryOpen(true)}
        onSelect={(id) => fetchRun(id).then(setResult).catch(() => undefined)} />
      <HistoryModal open={historyOpen} onClose={() => setHistoryOpen(false)}
        onSelect={(id) => fetchRun(id).then(setResult).catch(() => undefined)} />

      {result ? <Report result={result} onSelectRun={(id) => fetchRun(id).then(setResult).catch(() => undefined)} />
        : <div className="empty">Ready when you are. Press <kbd>⌘↵</kbd> to run, or open Customize.</div>}

      <Drawer open={drawer} onClose={() => setDrawer(false)} title="Customize checks">
        <div className="dgroup-label">Preset</div>
        <div className="preset-pills">
          {PRESETS.map((p) => (
            <button key={p.id} className={`pill-btn ${p.id === presetId ? "on" : ""}`}
              onClick={() => setPresetId(p.id)}>{p.label}</button>
          ))}
        </div>
        <div className="note">A starting set of checks. Switch any on or off below.</div>
        <StandardPicker standard={standard} setStandard={setStandard} profiles={profiles} />
        <ChecksEditor catalog={sqlCatalog} state={checkState} setState={setCheckState} />
        <CustomChecksEditor checks={custom} setChecks={setCustom} />
      </Drawer>

      <LineageMap open={mapOpen} onClose={() => setMapOpen(false)} sql={sql} />
    </>
  );
}

function TableauView({ profiles, catalog }: { profiles: string[]; catalog: CatalogCheck[] }) {
  const [file, setFile] = useState<File | null>(null);
  const [standard, setStandard] = useState("");
  const [checkState, setCheckState] = useState<Record<string, CheckState>>({});
  const [drawer, setDrawer] = useState(false);
  const [result, setResult] = useState<RunResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const { runs: history, total: historyTotal, refresh: refreshHistory } = useHistory();
  const [historyOpen, setHistoryOpen] = useState(false);

  const tabCatalog = useMemo(() => catalog.filter((c) => c.family.startsWith("tableau")), [catalog]);
  useEffect(() => {
    if (!tabCatalog.length) return;
    fetchRulesetChecks("plumb").then((d) => {
      const seed = new Map(d.checks.map((c) => [c.id, c]));
      const next: Record<string, CheckState> = {};
      for (const c of tabCatalog) next[c.id] = { id: c.id, enabled: seed.get(c.id)?.enabled ?? true, params: seed.get(c.id)?.params ?? {} };
      setCheckState(next);
    }).catch(() => undefined);
  }, [tabCatalog]);

  const enabledCount = useMemo(() => Object.values(checkState).filter((c) => c.enabled).length, [checkState]);

  async function run() {
    if (!file) { setError("Choose a .twb or .twbx file first."); return; }
    setBusy(true); setError(""); setResult(null);
    try { setResult(await runTableau(file, standard || null, Object.values(checkState))); refreshHistory(); }
    catch (e) { setError(String(e instanceof Error ? e.message : e)); }
    finally { setBusy(false); }
  }

  return (
    <>
      <div className="panel">
        <span className="lab" style={{ display: "block", marginBottom: 7 }}>Tableau workbook</span>
        <label className="drop">
          {file ? <><strong>{file.name}</strong><div className="note">click to choose a different file</div></>
            : <><strong>Choose a .twb or .twbx</strong><div className="note">parsed locally, no Tableau Server access</div></>}
          <input type="file" accept=".twb,.twbx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        <div className="setup">
          <span className="desc"><b>{enabledCount}</b> workbook checks · <b>{standard || "Standard"}</b> standard</span>
          <button className="linkbtn" onClick={() => setDrawer(true)}>Customize</button>
        </div>
        <button className="run" onClick={run} disabled={busy}>
          {busy ? <><span className="spin" />Parsing</> : `Check workbook (${enabledCount} checks)`}
        </button>
        {error && <p className="error">{error}</p>}
      </div>

      <RecentRuns runs={history} total={historyTotal} onShowAll={() => setHistoryOpen(true)}
        onSelect={(id) => fetchRun(id).then(setResult).catch(() => undefined)} />
      <HistoryModal open={historyOpen} onClose={() => setHistoryOpen(false)}
        onSelect={(id) => fetchRun(id).then(setResult).catch(() => undefined)} />

      {result ? <Report result={result} onSelectRun={(id) => fetchRun(id).then(setResult).catch(() => undefined)} />
        : <div className="empty">Upload a workbook to run the Tableau checks.</div>}

      <Drawer open={drawer} onClose={() => setDrawer(false)} title="Customize checks">
        <StandardPicker standard={standard} setStandard={setStandard} profiles={profiles} />
        <ChecksEditor catalog={tabCatalog} state={checkState} setState={setCheckState} />
      </Drawer>
    </>
  );
}
