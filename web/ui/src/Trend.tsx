import { useEffect, useState } from "react";
import { fetchTrend } from "./api";
import type { Trend } from "./types";

function ago(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function BuildTrend({ target, onSelect }: {
  target: string; onSelect?: (id: string) => void;
}) {
  const [trend, setTrend] = useState<Trend | null>(null);
  useEffect(() => { fetchTrend(target).then(setTrend).catch(() => setTrend(null)); }, [target]);
  if (!trend || trend.points.length < 1) return null;

  const pts = trend.points;
  const headline = pts.length <= 1
    ? "First run for this build. Trend builds as you re-run."
    : `Ready or better in ${trend.ready_or_better} of the last ${pts.length} runs.`;

  return (
    <div className="trend">
      <div className="trend-head">
        <h4>Build trend</h4>
        <span className="trend-headline">{headline}</span>
      </div>
      <div className="spark">
        {pts.map((p, i) => {
          const total = p.passed + p.failed;
          const ratio = total ? p.passed / total : 1;
          const h = 24 + Math.round(ratio * 32);
          const last = i === pts.length - 1;
          return (
            <button key={p.run_id} className={`bar tv-${p.verdict} ${last ? "current" : ""}`}
              style={{ height: `${h}px` }}
              title={`${p.verdict} · ${p.passed} passed, ${p.failed} failed · ${ago(p.timestamp)}`}
              onClick={() => onSelect?.(p.run_id)} />
          );
        })}
      </div>
      <div className="spark-axis"><span>older</span><span>now</span></div>
    </div>
  );
}
