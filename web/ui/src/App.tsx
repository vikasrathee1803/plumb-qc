import { useEffect, useState } from "react";
import type { RunResult, CheckResult } from "./types";

const SAMPLE_SQL = "SELECT o.order_id, c.region, o.amount\nFROM orders o, dim_customer c";

export function App() {
  const [tab, setTab] = useState<"sql" | "tableau">("sql");
  const [profiles, setProfiles] = useState<string[]>([]);
  const [rulesetVersion, setRulesetVersion] = useState("");

  useEffect(() => {
    fetch("/api/profiles")
      .then((r) => r.json())
      .then((d) => {
        setProfiles(d.profiles ?? []);
        setRulesetVersion(d.ruleset_version ?? "");
      })
      .catch(() => undefined);
  }, []);

  return (
    <div className="wrap">
      <header>
        <h1>Plumb</h1>
        <span className="sub">
          BI build QC and confidence engine{rulesetVersion && ` · ruleset ${rulesetVersion}`}
        </span>
      </header>
      <nav className="tabs">
        <button className={tab === "sql" ? "on" : ""} onClick={() => setTab("sql")}>
          SQL
        </button>
        <button className={tab === "tableau" ? "on" : ""} onClick={() => setTab("tableau")}>
          Tableau
        </button>
      </nav>
      {tab === "sql" ? <SqlPanel profiles={profiles} /> : <TableauPanel profiles={profiles} />}
    </div>
  );
}

function ProfileSelect({ value, onChange, profiles }: {
  value: string;
  onChange: (v: string) => void;
  profiles: string[];
}) {
  return (
    <label>
      Profile{" "}
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">(base)</option>
        {profiles.map((p) => (
          <option key={p} value={p}>
            {p}
          </option>
        ))}
      </select>
    </label>
  );
}

function SqlPanel({ profiles }: { profiles: string[] }) {
  const [sql, setSql] = useState(SAMPLE_SQL);
  const [profile, setProfile] = useState("");
  const [staticOnly, setStaticOnly] = useState(true);
  const [explain, setExplain] = useState(false);
  const [result, setResult] = useState<RunResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const r = await fetch("/api/check/sql", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sql, profile: profile || null, static_only: staticOnly, explain }),
      });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail ?? "check failed");
      setResult(body);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel">
      <textarea value={sql} onChange={(e) => setSql(e.target.value)} rows={8} spellCheck={false} />
      <div className="controls">
        <ProfileSelect value={profile} onChange={setProfile} profiles={profiles} />
        <label>
          <input type="checkbox" checked={staticOnly} onChange={(e) => setStaticOnly(e.target.checked)} />{" "}
          Static only (no Snowflake)
        </label>
        <label>
          <input type="checkbox" checked={explain} onChange={(e) => setExplain(e.target.checked)} /> Explain
          failures (AI)
        </label>
        <button className="run" onClick={run} disabled={busy}>
          {busy ? "Running..." : "Run checks"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {result && <Report result={result} />}
    </div>
  );
}

function TableauPanel({ profiles }: { profiles: string[] }) {
  const [profile, setProfile] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<RunResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function run() {
    if (!file) {
      setError("choose a .twb or .twbx file");
      return;
    }
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const form = new FormData();
      form.append("workbook", file);
      if (profile) form.append("profile", profile);
      const r = await fetch("/api/check/tableau", { method: "POST", body: form });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail ?? "check failed");
      setResult(body);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel">
      <input
        type="file"
        accept=".twb,.twbx"
        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
      />
      <div className="controls">
        <ProfileSelect value={profile} onChange={setProfile} profiles={profiles} />
        <button className="run" onClick={run} disabled={busy}>
          {busy ? "Parsing..." : "Check workbook"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {result && <Report result={result} />}
    </div>
  );
}

function Report({ result }: { result: RunResult }) {
  const s = result.summary;
  return (
    <div className="report">
      <div className={`verdict v-${result.verdict}`}>{result.verdict.replace(/_/g, " ")}</div>
      <Coverage result={result} />
      <div className="summary">
        <span>passed {s.passed}</span>
        <span>blocker {s.blocker}</span>
        <span>high {s.high}</span>
        <span>medium {s.medium}</span>
        <span>low {s.low}</span>
        <span>warned {s.warned}</span>
        <span>errored {s.errored}</span>
        <span>skipped {s.skipped}</span>
      </div>
      <a className="download" href={`/api/report/${result.run_id}.html`} target="_blank" rel="noreferrer">
        Open full HTML report
      </a>
      <table>
        <thead>
          <tr>
            <th>Check</th>
            <th>Status</th>
            <th>Observed</th>
            <th>Detail</th>
          </tr>
        </thead>
        <tbody>
          {result.checks.map((c) => (
            <CheckRow key={c.id} check={c} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Coverage({ result }: { result: RunResult }) {
  const { families_run, families_skipped, checks_skipped } = result.coverage;
  return (
    <div className="coverage">
      <div>
        <strong>Ran:</strong> {families_run.join(", ") || "none"}
      </div>
      {(families_skipped.length > 0 || checks_skipped.length > 0) && (
        <div className="gaps">
          <strong>Coverage gaps (most important first):</strong>
          <ol>
            {checks_skipped.map((c) => (
              <li key={c.id}>
                {c.name}: {c.reason}
              </li>
            ))}
            {families_skipped.map((f) => (
              <li key={f.family}>
                {f.family}: {f.reason}
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}

function CheckRow({ check }: { check: CheckResult }) {
  return (
    <tr>
      <td>
        <strong>{check.id}</strong>
        <div className="muted">
          {check.name} · {check.family} · {check.severity}
        </div>
      </td>
      <td>
        <span className={`pill s-${check.status}`}>{check.status}</span>
      </td>
      <td>
        {check.observed}
        {check.expected && <div className="muted">expected: {check.expected}</div>}
      </td>
      <td>
        {check.remediation}
        {check.ai_explanation && (
          <div className="ai">AI: {check.ai_explanation}</div>
        )}
      </td>
    </tr>
  );
}
