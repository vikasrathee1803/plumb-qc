import { useEffect, useState } from "react";
import { fetchHistory } from "./api";
import { Modal } from "./ui";
import type { HistoryRun } from "./types";

const VERDICT_LABEL: Record<string, string> = {
  BLOCKED: "Blocked", REVIEW: "Review", READY_WITH_NOTES: "Ready, notes", READY: "Ready",
};

function ago(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function RecentRuns({ runs, total, onSelect, onShowAll }: {
  runs: HistoryRun[]; total: number; onSelect: (id: string) => void; onShowAll: () => void;
}) {
  if (!runs.length) return null;
  return (
    <div className="recent">
      <span className="recent-label">Recent</span>
      <div className="recent-row">
        {runs.slice(0, 3).map((r) => (
          <button key={r.run_id} className="recent-chip" onClick={() => onSelect(r.run_id)}
            title={`${r.target} · ${r.verdict} · ${r.checks} checks`}>
            <span className={`rdot d-${r.verdict}`} />
            <span className="rname">{r.target}</span>
            <span className="rtime">{ago(r.timestamp)}</span>
          </button>
        ))}
        <button className="recent-chip all" onClick={onShowAll}>
          All runs{total > 0 ? ` (${total})` : ""}
        </button>
      </div>
    </div>
  );
}

export function HistoryModal({ open, onClose, onSelect }: {
  open: boolean; onClose: () => void; onSelect: (id: string) => void;
}) {
  const [q, setQ] = useState("");
  const [runs, setRuns] = useState<HistoryRun[]>([]);
  useEffect(() => {
    if (open) fetchHistory({ limit: 1000 }).then((d) => setRuns(d.runs)).catch(() => setRuns([]));
  }, [open]);

  const needle = q.trim().toLowerCase();
  const filtered = needle
    ? runs.filter((r) => r.target.toLowerCase().includes(needle) || r.verdict.toLowerCase().includes(needle))
    : runs;

  const search = (
    <input className="hist-search" type="text" placeholder="Search by build or verdict"
      value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
  );

  return (
    <Modal open={open} onClose={onClose} title="Run history" head={search}>
      {filtered.length === 0
        ? <div className="empty">{runs.length ? "No runs match." : "No runs yet."}</div>
        : (
          <div className="hist-list">
            {filtered.map((r) => (
              <button key={r.run_id} className="hist-row" onClick={() => { onSelect(r.run_id); onClose(); }}>
                <span className={`rdot d-${r.verdict}`} />
                <span className="hist-target">{r.target}</span>
                <span className={`hist-verdict hv-${r.verdict}`}>{VERDICT_LABEL[r.verdict] ?? r.verdict}</span>
                <span className="hist-meta">{r.type} · {r.checks} checks</span>
                <span className="hist-time">{ago(r.timestamp)}</span>
              </button>
            ))}
          </div>
        )}
    </Modal>
  );
}
