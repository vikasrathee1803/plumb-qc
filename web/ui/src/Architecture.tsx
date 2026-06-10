import { useEscape } from "./ui";
import type { About } from "./types";

const FAMILY_LABEL: Record<string, string> = {
  static: "Static", metadata: "Metadata", assertions: "Assertions",
  regression: "Regression", performance: "Performance", tableau_static: "Tableau",
  tableau_live: "Tableau live", migration_parity: "Migration parity",
};
const VERDICT_LABEL: Record<string, string> = {
  BLOCKED: "Blocked", REVIEW: "Review", READY_WITH_NOTES: "Ready, notes", READY: "Ready",
};

export function Architecture({ open, onClose, about }: {
  open: boolean; onClose: () => void; about: About | null;
}) {
  useEscape(open, onClose);
  return (
    <>
      <div className={`scrim ${open ? "open" : ""}`} onClick={onClose} />
      <div className={`arch ${open ? "open" : ""}`} role="dialog" aria-hidden={!open}>
        <div className="arch-head">
          <div>
            <h2>How Plumb works</h2>
            <div className="arch-sub">The QC engine, end to end, deterministic and read-only.</div>
          </div>
          <button className="done" onClick={onClose}>Close</button>
        </div>

        <div className="arch-body">
          {about && (
            <div className="livebar">
              <span className="live-dot" /> running now
              <span className="sep" />
              <b>v{about.version}</b>
              <span className="sep" />
              <b>{about.total_checks}</b> checks registered
              <span className="sep" />
              {about.connection.configured
                ? <>Snowflake <b>{about.connection.account}</b></>
                : <>no Snowflake connection</>}
              <span className="sep" />
              AI assist {about.ai_ready ? <b className="ok">ready</b> : <span className="off">off</span>}
            </div>
          )}

          <div className="flow">
            <Stage n="1" title="Your build" tag="input"
              detail="A Snowflake SQL query, or a Tableau .twb / .twbx workbook." />
            <Stage n="2" title="Read-only access" tag="lock"
              detail="The connect guard refuses anything that is not a read. Every query carries the plumb_qc:{run_id} tag, runs on the dedicated PLUMB_WH warehouse, and respects a statement timeout and a row cap. Tableau workbooks are parsed locally, no server access.">
              <div className="badges">
                <span className="badge ok">read-only guard</span>
                <span className="badge">plumb_qc tag</span>
                <span className="badge">PLUMB_WH</span>
                <span className="badge">timeout + row cap</span>
              </div>
            </Stage>
            <Stage n="3" title="Check engine" tag="engine"
              detail="Each check self-registers and runs against its family. Adding a check is dropping a function in a module; the runner never changes.">
              <div className="fampills">
                {(about?.families ?? []).map((f) => (
                  <span className="fampill" key={f.family}>
                    {FAMILY_LABEL[f.family] ?? f.family}<b>{f.count}</b>
                  </span>
                ))}
              </div>
            </Stage>
            <Stage n="4" title="Deterministic verdict + coverage" tag="verdict"
              detail="Severity gates pick exactly one of four tiers. Coverage lists which families ran and which were skipped, ranked by risk, so a green result never hides a gap.">
              <div className="ladder">
                {(about?.verdict_tiers ?? ["BLOCKED", "REVIEW", "READY_WITH_NOTES", "READY"]).map((t) => (
                  <span className={`tier v-${t}`} key={t}>{VERDICT_LABEL[t] ?? t}</span>
                ))}
              </div>
              <div className="ai-branch">
                <span className="b">AI assist</span> reads already-decided results to explain a failure.
                It can never change a status or the verdict.
              </div>
            </Stage>
            <Stage n="5" title="One result, many surfaces" tag="report" last
              detail="The same RunResult renders here, as a self-contained HTML report, JSON, and JUnit XML. The CLI maps the verdict to an exit code that gates CI.">
              <div className="badges">
                <span className="badge">Web</span>
                <span className="badge">HTML</span>
                <span className="badge">JSON</span>
                <span className="badge">JUnit</span>
                <span className="badge">CLI exit code</span>
              </div>
            </Stage>
          </div>

          {about && (
            <div className="invariants">
              {about.invariants.map((inv, i) => (
                <div className="inv" key={i}><span className="tick">✓</span>{inv}</div>
              ))}
            </div>
          )}

          {about && about.stack.length > 0 && (
            <div className="stack-sec">
              <div className="stack-title">Built with</div>
              <div className="stack-grid">
                {about.stack.map((g) => (
                  <div className="stack-group" key={g.group}>
                    <div className="stack-glabel">{g.group}</div>
                    <div className="stack-items">
                      {g.items.map((it) => (
                        <span className="techpill" key={it.name}>
                          {it.name}<span className="ver">{it.version}</span>
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
              <div className="stack-foot">
                Local-first - pip / pipx installable, version-pinned, with a Dockerfile for the CI gate.
                One engine behind the CLI, this web UI, and the report writers.
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function Stage({ n, title, detail, tag, children, last }: {
  n: string; title: string; detail: string; tag: string;
  children?: React.ReactNode; last?: boolean;
}) {
  return (
    <div className={`stage-row ${last ? "last" : ""}`}>
      <div className="rail"><span className="num">{n}</span></div>
      <div className="stage-card">
        <div className="stage-top"><h3>{title}</h3><span className={`tagchip t-${tag}`}>{tag}</span></div>
        <p className="stage-detail">{detail}</p>
        {children}
      </div>
    </div>
  );
}
