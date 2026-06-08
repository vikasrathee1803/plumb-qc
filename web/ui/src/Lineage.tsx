import { useEffect, useMemo, useState } from "react";
import { fetchLineage } from "./api";
import { useEscape } from "./ui";
import type { LineageGraph, LineageNode } from "./types";

const NODE_W = 168;
const NODE_H = 56;
const X_GAP = 250;
const Y_GAP = 92;
const MARGIN = 48;

const KIND_LABEL: Record<string, string> = {
  table: "Table", cte: "CTE", subquery: "Subquery", output: "Result", values: "Inline",
};

interface Placed { node: LineageNode; x: number; y: number; }

function layout(graph: LineageGraph) {
  const incoming = new Map<string, number>();
  const outAdj = new Map<string, string[]>();
  for (const n of graph.nodes) { incoming.set(n.id, 0); outAdj.set(n.id, []); }
  for (const e of graph.edges) {
    if (!incoming.has(e.target) || !outAdj.has(e.source)) continue;
    incoming.set(e.target, (incoming.get(e.target) ?? 0) + 1);
    outAdj.get(e.source)!.push(e.target);
  }
  // longest-path layering via Kahn's topological order
  const layer = new Map<string, number>();
  const indeg = new Map(incoming);
  const queue = graph.nodes.filter((n) => (indeg.get(n.id) ?? 0) === 0).map((n) => n.id);
  for (const id of queue) layer.set(id, 0);
  let guard = 0;
  while (queue.length && guard++ < 10000) {
    const u = queue.shift()!;
    for (const v of outAdj.get(u) ?? []) {
      layer.set(v, Math.max(layer.get(v) ?? 0, (layer.get(u) ?? 0) + 1));
      indeg.set(v, (indeg.get(v) ?? 0) - 1);
      if ((indeg.get(v) ?? 0) === 0) queue.push(v);
    }
  }
  for (const n of graph.nodes) if (!layer.has(n.id)) layer.set(n.id, 0);

  const byLayer = new Map<number, LineageNode[]>();
  for (const n of graph.nodes) {
    const l = layer.get(n.id) ?? 0;
    let col = byLayer.get(l);
    if (!col) { col = []; byLayer.set(l, col); }
    col.push(n);
  }
  const layers = [...byLayer.keys()].sort((a, b) => a - b);
  const maxRows = Math.max(1, ...layers.map((l) => byLayer.get(l)!.length));

  const placed: Record<string, Placed> = {};
  for (const l of layers) {
    const col = byLayer.get(l)!;
    const offset = ((maxRows - col.length) * Y_GAP) / 2;
    col.forEach((node, i) => {
      placed[node.id] = { node, x: MARGIN + l * X_GAP, y: MARGIN + offset + i * Y_GAP };
    });
  }
  const width = MARGIN * 2 + (Math.max(0, layers.length - 1)) * X_GAP + NODE_W;
  const height = MARGIN * 2 + (maxRows - 1) * Y_GAP + NODE_H;
  return { placed, width, height };
}

export function LineageMap({ open, onClose, sql }: {
  open: boolean; onClose: () => void; sql: string;
}) {
  useEscape(open, onClose);
  const [graph, setGraph] = useState<LineageGraph | null>(null);
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState(1);
  const [hover, setHover] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setGraph(null); setError(""); setZoom(1); setHover(null);
    fetchLineage(sql).then(setGraph).catch((e) => setError(String(e.message ?? e)));
  }, [open, sql]);

  const lay = useMemo(() => (graph ? layout(graph) : null), [graph]);
  const canvasW = lay ? Math.max(lay.width, 640) : 640;
  const canvasH = lay ? Math.max(lay.height, 200) : 200;

  const neighbors = useMemo(() => {
    const set = new Set<string>();
    if (!hover || !graph) return set;
    set.add(hover);
    for (const e of graph.edges) {
      if (e.source === hover) set.add(e.target);
      if (e.target === hover) set.add(e.source);
    }
    return set;
  }, [hover, graph]);

  const counts = useMemo(() => {
    const c = { table: 0, cte: 0, subquery: 0, risk: graph?.risks.length ?? 0 };
    for (const n of graph?.nodes ?? []) {
      if (n.kind === "table") c.table++;
      else if (n.kind === "cte") c.cte++;
      else if (n.kind === "subquery") c.subquery++;
    }
    return c;
  }, [graph]);

  const summary = graph
    ? [
        `${counts.table} table${counts.table === 1 ? "" : "s"}`,
        counts.cte ? `${counts.cte} CTE${counts.cte === 1 ? "" : "s"}` : "",
        counts.subquery ? `${counts.subquery} subquer${counts.subquery === 1 ? "y" : "ies"}` : "",
        counts.risk ? `${counts.risk} risk${counts.risk === 1 ? "" : "s"}` : "",
      ].filter(Boolean).join(" · ")
    : "";

  return (
    <div className={`mapov ${open ? "open" : ""}`} role="dialog" aria-hidden={!open}>
      <div className="map-head">
        <div>
          <h2>Query map</h2>
          <div className="map-sub">{summary || "How your SQL flows: sources into joins into the result."}</div>
        </div>
        <span className="spacer" />
        <div className="legend">
          <span className="lg"><i className="k-table" />Table</span>
          <span className="lg"><i className="k-cte" />CTE</span>
          <span className="lg"><i className="k-subquery" />Subquery</span>
          <span className="lg"><i className="k-output" />Result</span>
          <span className="lg"><i className="k-risk" />Fan-out risk</span>
        </div>
        <div className="zoom">
          <button className="zbtn" aria-label="Zoom out" onClick={() => setZoom((z) => Math.max(0.5, z - 0.15))}>−</button>
          <button className="zbtn zval" onClick={() => setZoom(1)} title="Reset zoom">{Math.round(zoom * 100)}%</button>
          <button className="zbtn" aria-label="Zoom in" onClick={() => setZoom((z) => Math.min(2, z + 0.15))}>+</button>
        </div>
        <button className="done" onClick={onClose}>Close</button>
      </div>

      {graph && graph.risks.length > 0 && (
        <div className="map-risks">
          {graph.risks.map((r, i) => <span className="map-risk" key={i}>{r}</span>)}
        </div>
      )}

      <div className="map-canvas">
        {error && <div className="empty">{error}</div>}
        {!graph && !error && <div className="empty">Mapping your query…</div>}
        {graph && lay && (
          <svg width={canvasW * zoom} height={canvasH * zoom}
            viewBox={`0 0 ${canvasW} ${canvasH}`} className="map-svg">
            <defs>
              <marker id="arrow" markerWidth="9" markerHeight="9" refX="7" refY="3"
                orient="auto" markerUnits="strokeWidth">
                <path d="M0,0 L6,3 L0,6 Z" className="arrowhead" />
              </marker>
              <marker id="arrow-risk" markerWidth="9" markerHeight="9" refX="7" refY="3"
                orient="auto" markerUnits="strokeWidth">
                <path d="M0,0 L6,3 L0,6 Z" className="arrowhead-risk" />
              </marker>
            </defs>
            {graph.edges.map((e, i) => {
              const s = lay.placed[e.source]; const t = lay.placed[e.target];
              if (!s || !t) return null;
              const sx = s.x + NODE_W, sy = s.y + NODE_H / 2;
              const tx = t.x, ty = t.y + NODE_H / 2;
              const dx = Math.max(40, (tx - sx) / 2);
              const mx = (sx + tx) / 2, my = (sy + ty) / 2;
              const label = e.relation === "from" ? "" : e.relation;
              const touch = hover ? (e.source === hover || e.target === hover ? "hot" : "dim") : "";
              return (
                <g key={i} className={`edge ${e.risk ? "risk" : ""} ${touch}`}>
                  <path d={`M${sx},${sy} C${sx + dx},${sy} ${tx - dx},${ty} ${tx},${ty}`}
                    markerEnd={`url(#${e.risk ? "arrow-risk" : "arrow"})`}>
                    <title>{e.on ?? e.relation}</title>
                  </path>
                  {(label || e.risk) && (
                    <text x={mx} y={my - 6} className="edge-label" textAnchor="middle">
                      {e.risk ? "no key" : label}
                    </text>
                  )}
                </g>
              );
            })}
            {Object.values(lay.placed).map(({ node, x, y }) => (
              <Node key={node.id} node={node} x={x} y={y}
                dim={hover != null && !neighbors.has(node.id)}
                onHover={setHover} />
            ))}
          </svg>
        )}
      </div>
    </div>
  );
}

function Node({ node, x, y, dim, onHover }: {
  node: LineageNode; x: number; y: number; dim: boolean; onHover: (id: string | null) => void;
}) {
  const hasFlag = node.flags.length > 0;
  return (
    <g className={`node ${dim ? "dim" : ""}`} transform={`translate(${x},${y})`}
      onMouseEnter={() => onHover(node.id)} onMouseLeave={() => onHover(null)}>
      <rect width={NODE_W} height={NODE_H} rx={13} className={`nbox k-${node.kind}`} />
      <rect width={5} height={NODE_H} rx={2} className={`naccent k-${node.kind}`} />
      <text x={18} y={22} className="nkind">{KIND_LABEL[node.kind] ?? node.kind}</text>
      <text x={18} y={40} className="nlabel">{trim(node.label, 18)}</text>
      {hasFlag && (
        <>
          <circle cx={NODE_W - 16} cy={18} r={4} className="nflag" />
          <title>{node.flags.join(", ")}</title>
        </>
      )}
    </g>
  );
}

function trim(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
