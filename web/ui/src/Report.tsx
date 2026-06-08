import { useState } from "react";
import type { CheckResult, RunResult } from "./types";

const VERDICT_LABEL: Record<string, string> = {
  BLOCKED: "Blocked",
  REVIEW: "Review",
  READY_WITH_NOTES: "Ready with notes",
  READY: "Ready",
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
      <div className="card pad">
        <div className={`verdict-hero v-${result.verdict}`}>
          <span className="vdot" />
          <div>
            <div className="vbig">{VERDICT_LABEL[result.verdict] ?? result.verdict}</div>
            <div className="vmeta">
              <b>{result.target.name}</b> · ruleset {result.ruleset_version}
              {result.profile ? ` · ${result.profile}` : ""}
              {result.environment.query_tag ? ` · ${result.environment.warehouse}` : " · static"}
            </div>
          </div>
          {gaps.length > 0 && (
            <div className="cov-caption">
              limited coverage: {gaps.length} gap{gaps.length > 1 ? "s" : ""} (see below)
            </div>
          )}
        </div>

        <div className="tiles">
          <Tile cls="pass" n={s.passed} l="passed" />
          <Tile cls="fail" n={s.blocker} l="blocker" />
          <Tile cls="fail" n={s.high} l="high" />
          <Tile cls="warn" n={s.medium} l="medium" />
          <Tile cls="warn" n={s.low} l="low" />
          <Tile cls="warn" n={s.warned} l="warned" />
          <Tile cls="err" n={s.errored} l="errored" />
          <Tile cls="skip" n={s.skipped} l="skipped" />
        </div>
      </div>

      <div className="card pad coverage">
        <h4>Coverage</h4>
        <div className="fam-pills">
          {cov.families_run.length === 0 && <span className="muted">no families ran</span>}
          {cov.families_run.map((f) => (
            <span className="fam-pill" key={f}>{f}</span>
          ))}
        </div>
        {gaps.length > 0 && (
          <ul className="gap-list">
            {gaps.map((g, i) => (
              <li key={i}><b>{g.label}</b>: {g.reason}</li>
            ))}
          </ul>
        )}
        <a className="muted" style={{ display: "inline-block", marginTop: 10 }}
           href={`/api/report/${result.run_id}.html`} target="_blank" rel="noreferrer">
          Open full HTML report ↗
        </a>
      </div>

      <div className="card pad">
        <h4 style={{ margin: "0 0 10px" }}>Checks ({result.checks.length})</h4>
        <div className="checks-list">
          {result.checks.map((c) => <CheckRow key={c.id} check={c} />)}
        </div>
      </div>
    </>
  );
}

function Tile({ cls, n, l }: { cls: string; n: number; l: string }) {
  return (
    <div className={`tile ${cls}`}>
      <div className="n">{n ?? 0}</div>
      <div className="l">{l}</div>
    </div>
  );
}

function CheckRow({ check }: { check: CheckResult }) {
  const [open, setOpen] = useState(check.status === "FAIL" || check.status === "ERROR");
  const rows = check.evidence.sample_rows;
  const cols = rows.length ? Object.keys(rows[0]) : [];
  return (
    <div className="crow">
      <div className="crow-head" onClick={() => setOpen(!open)}>
        <span className={`caret ${open ? "open" : ""}`}>▸</span>
        <span className="cid">{check.id}</span>
        <span className="sev">{check.severity}</span>
        <span className="cname">{check.name}</span>
        <span className="cobs">{check.observed}</span>
        <span className={`pill p-${check.status}`}>{check.status}</span>
      </div>
      {open && (
        <div className="crow-detail">
          <div className="kv">
            {check.observed && (<><span className="k">observed</span><span className="v">{check.observed}</span></>)}
            {check.expected && (<><span className="k">expected</span><span className="v">{check.expected}</span></>)}
            <span className="k">family</span><span className="v">{check.family}</span>
          </div>
          {check.remediation && (
            <div className="detail-block">
              <div className="dh">Root cause / fix</div>
              <div className="v" style={{ fontSize: 12.5 }}>{check.remediation}</div>
            </div>
          )}
          {check.ai_explanation && (
            <div className="detail-block">
              <div className="ai-note"><span className="badge">AI</span>{check.ai_explanation}</div>
            </div>
          )}
          {rows.length > 0 && (
            <div className="detail-block">
              <div className="dh">Evidence ({rows.length} sampled, PII redacted)</div>
              <table className="ev-table">
                <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={i}>{cols.map((c) => <td key={c}>{String(r[c])}</td>)}</tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {check.evidence.query && (
            <div className="detail-block">
              <div className="dh">SQL Plumb ran</div>
              <pre className="code">{check.evidence.query}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
