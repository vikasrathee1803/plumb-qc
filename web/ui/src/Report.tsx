import { useState } from "react";
import type { CheckResult, RunResult } from "./types";

const VERDICT_LABEL: Record<string, string> = {
  BLOCKED: "Blocked", REVIEW: "Review", READY_WITH_NOTES: "Ready with notes", READY: "Ready",
};
const VERDICT_GLYPH: Record<string, string> = {
  BLOCKED: "✕", REVIEW: "!", READY_WITH_NOTES: "✓", READY: "✓",
};

export function Report({ result }: { result: RunResult }) {
  const s = result.summary;
  const cov = result.coverage;
  const gaps = [
    ...cov.checks_skipped.map((c) => ({ label: c.name, reason: c.reason })),
    ...cov.families_skipped.map((f) => ({ label: f.family, reason: f.reason })),
  ];
  return (
    <>
      <div className={`verdict v-${result.verdict}`}>
        <div className="ring">{VERDICT_GLYPH[result.verdict] ?? "•"}</div>
        <div>
          <div className="vtitle">{VERDICT_LABEL[result.verdict] ?? result.verdict}</div>
          <div className="vsub">
            <b>{result.target.name}</b> · {result.checks.length} checks ·
            {result.environment.query_tag ? ` live on ${result.environment.warehouse}` : " static"}
          </div>
        </div>
      </div>

      <div className="statstrip">
        <Stat cls="pass" n={s.passed} l="passed" />
        <Stat cls="fail" n={(s.blocker ?? 0) + (s.high ?? 0) + (s.medium ?? 0) + (s.low ?? 0)} l="failed" />
        <Stat cls="warn" n={s.warned} l="warned" />
        <Stat cls="err" n={s.errored} l="errored" />
        <Stat cls="skip" n={s.skipped} l="skipped" />
      </div>

      <div className="cov">
        <h4>Coverage</h4>
        <div className="runpills">
          {cov.families_run.length === 0 && <span className="note">no families ran</span>}
          {cov.families_run.map((f) => <span className="runpill" key={f}>{f}</span>)}
        </div>
        {gaps.length > 0 && (
          <ul className="gaps">
            {gaps.map((g, i) => <li key={i}><b>{g.label}</b>: {g.reason}</li>)}
          </ul>
        )}
        <a className="report-link" href={`/api/report/${result.run_id}.html`} target="_blank" rel="noreferrer">
          Open full report ↗
        </a>
      </div>

      <div className="results-head"><h3>Checks</h3><span className="note">tap a row for detail</span></div>
      {result.checks.map((c) => <Row key={c.id} check={c} />)}
    </>
  );
}

function Stat({ cls, n, l }: { cls: string; n: number; l: string }) {
  return <div className={`stat ${cls}`}><div className="n">{n ?? 0}</div><div className="l">{l}</div></div>;
}

function Row({ check }: { check: CheckResult }) {
  const [open, setOpen] = useState(check.status === "FAIL" || check.status === "ERROR");
  const rows = check.evidence.sample_rows;
  const cols = rows.length ? Object.keys(rows[0]) : [];
  return (
    <div className="crow">
      <div className="crow-head" onClick={() => setOpen(!open)}>
        <span className={`dot d-${check.status}`} />
        <span className={`caret ${open ? "open" : ""}`}>▸</span>
        <span className="cid mono">{check.id}</span>
        <span className="cname">{check.name}</span>
        <span className="cobs">{check.observed}</span>
        <span className={`statuspill s-${check.status}`}>{check.status}</span>
      </div>
      {open && (
        <div className="cdetail">
          <div className="kv">
            {check.observed && (<><span className="k">observed</span><span>{check.observed}</span></>)}
            {check.expected && (<><span className="k">expected</span><span>{check.expected}</span></>)}
            <span className="k">family</span><span>{check.family} · {check.severity}</span>
          </div>
          {check.remediation && (<><div className="dh">Root cause / fix</div><div style={{ fontSize: 13 }}>{check.remediation}</div></>)}
          {check.ai_explanation && (<><div className="dh">AI explanation</div><div className="ai"><span className="b">AI</span>{check.ai_explanation}</div></>)}
          {rows.length > 0 && (
            <>
              <div className="dh">Evidence ({rows.length} sampled, PII redacted)</div>
              <table className="ev">
                <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
                <tbody>{rows.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c}>{String(r[c])}</td>)}</tr>)}</tbody>
              </table>
            </>
          )}
          {check.evidence.query && (<><div className="dh">SQL Plumb ran</div><pre className="code">{check.evidence.query}</pre></>)}
        </div>
      )}
    </div>
  );
}
