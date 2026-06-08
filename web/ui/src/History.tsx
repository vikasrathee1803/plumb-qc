import type { HistoryRun } from "./types";

function ago(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function RecentRuns({ runs, onSelect }: {
  runs: HistoryRun[]; onSelect: (id: string) => void;
}) {
  if (!runs.length) return null;
  return (
    <div className="recent">
      <span className="recent-label">Recent</span>
      <div className="recent-row">
        {runs.slice(0, 12).map((r) => (
          <button key={r.run_id} className="recent-chip" onClick={() => onSelect(r.run_id)}
            title={`${r.target} · ${r.verdict} · ${r.checks} checks`}>
            <span className={`rdot d-${r.verdict}`} />
            <span className="rname">{r.target}</span>
            <span className="rtime">{ago(r.timestamp)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
