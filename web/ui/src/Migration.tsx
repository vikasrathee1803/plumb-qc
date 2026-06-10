import { useEffect, useState } from "react";
import {
  fetchDemoFile, fetchParityDemo, runParity,
  type ParityDemoInfo, type ParityRunResponse,
} from "./api";
import { MapBuilder } from "./MapBuilder";
import { Report } from "./Report";
import { Segmented, SwitchRow } from "./ui";
import type { Connection } from "./types";

type Mode = "snapshot" | "check" | "run";

const MODE_HELP: Record<Mode, string> = {
  snapshot: "Measure the legacy side and save one baseline per workbook source.",
  check: "Measure the migrated side and compare it against the saved snapshots.",
  run: "Both phases back to back: snapshot the legacy side, then check the target.",
};

const PHASE_LABEL = ["Snapshot (legacy side)", "Check (target side)"];

// Migration parity from the browser (single workbook). The same run_parity
// pipeline as `plumb parity` - upload the workbook (and optionally the
// old->new map), pick a phase, run. Wave-scale runs stay on the CLI
// (`plumb parity estate`), which handles manifests and roll-up reports.
export function MigrationView({ conn }: { conn: Connection }) {
  const [file, setFile] = useState<File | null>(null);
  const [mapFile, setMapFile] = useState<File | null>(null);
  const [mode, setMode] = useState<Mode>("snapshot");
  const [live, setLive] = useState(conn.configured);
  const [postSwap, setPostSwap] = useState(false);
  const [resp, setResp] = useState<ParityRunResponse | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [demo, setDemo] = useState<ParityDemoInfo | null>(null);
  const [builderOpen, setBuilderOpen] = useState(false);

  useEffect(() => { fetchParityDemo().then(setDemo).catch(() => undefined); }, []);

  // The demo loads the bundled assets into the SAME inputs a real run uses,
  // so the team learns the actual flow: snapshot, check (parity proven),
  // then swap in the drift map and watch the check go BLOCKED.
  async function loadDemo(kind: "identity" | "drift") {
    setError("");
    try {
      if (!file || kind === "identity") {
        setFile(await fetchDemoFile("/api/parity/demo/workbook", demo?.workbook ?? "demo-workbook.twb"));
      }
      setMapFile(await fetchDemoFile(`/api/parity/demo/map?kind=${kind}`, `${kind}-map.yml`));
      setMode(kind === "identity" ? "snapshot" : "check");
      setResp(null);
    } catch (e) { setError(String(e instanceof Error ? e.message : e)); }
  }

  // Both-live needs a session for each phase; a static snapshot writes no
  // baselines, so the API refuses run+static - keep the UI ahead of that.
  const effectiveLive = mode === "run" ? true : live;
  const runBlocked = mode === "run" && !conn.configured;

  async function run() {
    if (!file) { setError("Choose the workbook (.twb or .twbx) first."); return; }
    setBusy(true); setError(""); setResp(null);
    try {
      setResp(await runParity(file, mapFile, {
        mode, static_only: !effectiveLive, post_swap: mode === "check" && postSwap,
      }));
    } catch (e) { setError(String(e instanceof Error ? e.message : e)); }
    finally { setBusy(false); }
  }

  return (
    <>
      {demo?.available && (
        <div className="panel" style={{ marginBottom: 14 }}>
          <div className="dgroup-label" style={{ marginTop: 0 }}>Try the demo (2 minutes)</div>
          <div className="note" style={{ marginTop: 4 }}>
            A four-source workbook over the demo warehouse, live.{" "}
            <b>1.</b> Load the demo, run <b>Snapshot</b> — the legacy numbers are saved.{" "}
            <b>2.</b> Switch to <b>Check</b>, run — parity proven (READY: row counts,
            aggregates, nulls, distinct keys, grain, and per-row fingerprints).{" "}
            <b>3.</b> Load the drift map, run Check — one source is silently re-pointed
            at the wrong view and the check goes <b>BLOCKED</b>, naming exactly what
            drifted while the healthy sources still pass.{" "}
            <b>4.</b> Ready for your own workbook? Upload it and use{" "}
            <b>Build one from the workbook</b> to author the map without writing YAML.
          </div>
          <div className="toolbtns" style={{ marginTop: 8 }}>
            <button className="ghost" onClick={() => loadDemo("identity")}>Load the demo</button>
            <button className="ghost" onClick={() => loadDemo("drift")}>Load the drift map</button>
          </div>
        </div>
      )}
      <div className="panel">
        <div style={{ display: "flex", justifyContent: "center", marginBottom: 12 }}>
          <Segmented value={mode} onChange={setMode} options={[
            { value: "snapshot", label: "1 · Snapshot" },
            { value: "check", label: "2 · Check" },
            { value: "run", label: "Both" },
          ]} />
        </div>
        <div className="note" style={{ textAlign: "center", marginTop: 0 }}>{MODE_HELP[mode]}</div>

        <span className="lab" style={{ display: "block", margin: "12px 0 7px" }}>Tableau workbook</span>
        <label className="drop">
          {file ? <><strong>{file.name}</strong><div className="note">click to choose a different file</div></>
            : <><strong>Choose a .twb or .twbx</strong><div className="note">its Snowflake sources become the objects to prove</div></>}
          <input type="file" accept=".twb,.twbx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>

        <span className="lab" style={{ display: "block", margin: "12px 0 7px" }}>
          Migration map <span style={{ opacity: 0.6 }}>(optional)</span>
          <button className="lab-link" onClick={() => setBuilderOpen(true)} disabled={!file}>
            Build one from the workbook
          </button>
        </span>
        <label className="drop">
          {mapFile ? <><strong>{mapFile.name}</strong><div className="note">click to choose a different map</div></>
            : <><strong>galaxy-map.yml</strong><div className="note">old→new renames, keys, grain, tolerances · identity when omitted</div></>}
          <input type="file" accept=".yml,.yaml" onChange={(e) => setMapFile(e.target.files?.[0] ?? null)} />
        </label>

        <div className="toggles" style={{ marginTop: 12 }}>
          <SwitchRow checked={effectiveLive} onChange={setLive}
            disabled={!conn.configured || mode === "run"}
            label={conn.configured
              ? (mode === "run" ? "Live against Snowflake (required for both-live)" : "Run live against Snowflake")
              : "Live (no connection)"} />
          {mode === "check" && (
            <SwitchRow checked={postSwap} onChange={setPostSwap}
              label="Workbook is already swapped (apply the map inverted)" />
          )}
        </div>
        <button className="run" onClick={run} disabled={busy || runBlocked}>
          {busy ? <><span className="spin" />Running</>
            : mode === "snapshot" ? "Snapshot the legacy side"
            : mode === "check" ? "Check against snapshots"
            : "Snapshot, then check"}
        </button>
        {runBlocked && <p className="error">Both-live needs a configured Snowflake connection (gear icon).</p>}
        {error && <p className="error">{error}</p>}
      </div>

      {resp ? (
        <>
          {resp.results.map((r, i) => (
            <div key={r.run_id}>
              {resp.results.length > 1 && (
                <div className="dgroup-label" style={{ margin: "14px 0 6px" }}>{PHASE_LABEL[i]}</div>
              )}
              <Report result={r} />
            </div>
          ))}
          {resp.stopped_after_snapshot && (
            <p className="error">
              Check phase skipped: the snapshot phase is BLOCKED - fix the legacy capture first.
            </p>
          )}
        </>
      ) : (
        <div className="empty">
          Prove a re-pointed workbook shows the same numbers. Snapshot while the legacy
          side exists, swap with Tableau Autopilot, then check. Whole migration waves:
          <span className="mono"> plumb parity estate</span> (CLI).
        </div>
      )}

      <MapBuilder open={builderOpen} onClose={() => setBuilderOpen(false)}
        workbook={file} onUse={setMapFile} />
    </>
  );
}
