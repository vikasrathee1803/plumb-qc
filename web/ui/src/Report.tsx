import { useState } from "react";
import type { CheckResult, RunResult } from "./types";

const VERDICT_LABEL: Record<string, string> = {
  BLOCKED: "Blocked", REVIEW: "Review", READY_WITH_NOTES: "Ready, with notes", READY: "Ready",
};
const VERDICT_GLYPH: Record<string, string> = {
  BLOCKED: "✕", REVIEW: "!", READY_WITH_NOTES: "✓", READY: "✓",
};

// A calm, human one-liner: what it means and what to do next.
function plainEnglish(result: RunResult): string {
  const failed = result.checks.filter((c) => c.status === "FAIL");
  const blockers = failed.filter((c) => c.severity === "BLOCKER");
  const highs = failed.filter((c) => c.severity === "HIGH");
  const top = (blockers[0] ?? highs[0] ?? failed[0])?.name;
  switch (result.verdict) {
    case "READY":
      return `Good to ship. ${result.summary.passed} checks passed, nothing failed.`;
    case "READY_WITH_NOTES": {
      const notes = (result.summary.medium ?? 0) + (result.summary.low ?? 0) + (result.summary.warned ?? 0);
      return `Safe to ship, with ${notes} advisory item${notes === 1 ? "" : "s"} worth a glance.`;
    }
    case "REVIEW":
      return `Have someone review before shipping. ${highs.length} high-severity issue${highs.length === 1 ? "" : "s"}${top ? `, starting with ${top}.` : "."}`;
    case "BLOCKED":
      return `Not ready to ship. ${blockers.length} blocker${blockers.length === 1 ? "" : "s"}${top ? `: ${top}.` : "."}`;
    default:
      return "";
  }
}

function trustLine(result: RunResult): string {
  const tagged = result.environment.query_tag
    ? `every query tagged ${result.environment.query_tag.split(":")[0]}:* on ${result.environment.warehouse}`
    : "parsed without connecting";
  return `Deterministic and read-only · ${tagged}`;
}

function reportUrl(result: RunResult): string {
  return `${window.location.origin}/api/report/${result.run_id}.html`;
}

// A tidy markdown summary a reviewer can paste into a PR or Slack.
function summaryMarkdown(result: RunResult): string {
  const s = result.summary;
  const failed = (s.blocker ?? 0) + (s.high ?? 0) + (s.medium ?? 0) + (s.low ?? 0);
  const lines: string[] = [
    `**Plumb verdict: ${VERDICT_LABEL[result.verdict] ?? result.verdict}** · ${result.target.name}`,
    plainEnglish(result),
    `${result.checks.length} checks · ${s.passed} passed · ${failed} failed · ${s.warned} warned · ${s.skipped} skipped`,
  ];
  const issues = result.checks.filter((c) => c.status === "FAIL" || c.status === "ERROR");
  if (issues.length) {
    lines.push("");
    for (const c of issues) lines.push(`- ${c.status} ${c.id} (${c.severity}): ${c.observed ?? ""}`);
  }
  const gaps = [
    ...result.coverage.checks_skipped.map((c) => `${c.name}: ${c.reason}`),
    ...result.coverage.families_skipped.map((f) => `${f.family}: ${f.reason}`),
  ];
  if (gaps.length) { lines.push(""); lines.push(`Coverage gaps: ${gaps.join("; ")}`); }
  lines.push("");
  lines.push(`Deterministic, read-only. Full report: ${reportUrl(result)}`);
  return lines.join("\n");
}

function Actions({ result }: { result: RunResult }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    const text = summaryMarkdown(result);
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta); ta.select();
      document.execCommand("copy"); document.body.removeChild(ta);
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  }
  return (
    <div className="actions">
      <button className={`act ${copied ? "done" : ""}`} onClick={copy}>
        {copied ? "Copied to clipboard" : "Copy summary"}
      </button>
      <a className="act" href={reportUrl(result)}
        download={`plumb-${result.target.name}.html`}>Download report</a>
      <a className="act ghost-act" href={reportUrl(result)} target="_blank" rel="noreferrer">Open ↗</a>
    </div>
  );
}

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
        <div className="vmain">
          <div className="vtitle">{VERDICT_LABEL[result.verdict] ?? result.verdict}</div>
          <div className="vsentence">{plainEnglish(result)}</div>
          <div className="vtrust"><span className="shield">⛨</span>{trustLine(result)}</div>
        </div>
      </div>

      <Actions result={result} />

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
