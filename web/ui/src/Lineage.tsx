import { useEffect, useMemo, useState } from "react";
import { fetchLineage } from "./api";
import { useEscape } from "./ui";
import type { CheckResult, LineageGraph, LineageNode, RunResult } from "./types";

// Checks whose failure has a place on the map, and where to highlight.
const STRUCTURAL = new Set([
  "S-STAT-001", "S-STAT-002", "S-STAT-003", "S-STAT-008", "S-STAT-010",
  "D-GRAIN-001", "D-GRAIN-002", "D-DUP-001", "D-RECON-001", "D-DISTINCT-001",
]);
const OUTPUT_CHECKS = new Set([
  "D-GRAIN-001", "D-GRAIN-002", "D-DUP-001", "D-RECON-001", "D-DISTINCT-001",
]);

function relatedTo(checkId: string, graph: LineageGraph) {
  const edges = new Set<number>();
  const nodes = new Set<string>();
  graph.edges.forEach((e, i) => {
    if (checkId === "S-STAT-002" && e.risk) edges.add(i);
    if ((checkId === "S-STAT-010" || checkId === "S-STAT-008") && e.relation.includes("join")) edges.add(i);
  });
  for (const n of graph.nodes) {
    if (checkId === "S-STAT-001" && n.flags.includes("SELECT *")) nodes.add(n.id);
    if (OUTPUT_CHECKS.has(checkId) && n.kind === "output") nodes.add(n.id);
  }
  return { edges, nodes };
}

const NODE_W = 178;
const NODE_H = 64;
const X_GAP = 260;
const Y_GAP = 104;
const MARGIN = 48;

// Column-thread mode geometry.
const COL_W = 200;
const COL_HEADER_H = 40;
const COL_ROW_H = 19;
const COL_PAD_B = 12;
const COL_X_GAP = 300;
const COL_GAP = 30;
const COL_MAX_ROWS = 14;

const KIND_LABEL: Record<string, string> = {
  table: "Table", cte: "CTE", subquery: "Subquery", output: "Result", values: "Inline",
};

interface Placed { node: LineageNode; x: number; y: number; }
interface ColPlaced extends Placed { h: number; rows: string[] }

// Longest-path layering (Kahn), shared by both layouts. Returns nodes grouped
// by layer, left to right.
function computeLayers(graph: LineageGraph): LineageNode[][] {
  const incoming = new Map<string, number>();
  const outAdj = new Map<string, string[]>();
  for (const n of graph.nodes) { incoming.set(n.id, 0); outAdj.set(n.id, []); }
  for (const e of graph.edges) {
    if (!incoming.has(e.target) || !outAdj.has(e.source)) continue;
    incoming.set(e.target, (incoming.get(e.target) ?? 0) + 1);
    outAdj.get(e.source)!.push(e.target);
  }
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
  return [...byLayer.keys()].sort((a, b) => a - b).map((l) => byLayer.get(l)!);
}

function layout(graph: LineageGraph) {
  const layers = computeLayers(graph);
  const maxRows = Math.max(1, ...layers.map((c) => c.length));
  const placed: Record<string, Placed> = {};
  layers.forEach((col, l) => {
    const offset = ((maxRows - col.length) * Y_GAP) / 2;
    col.forEach((node, i) => {
      placed[node.id] = { node, x: MARGIN + l * X_GAP, y: MARGIN + offset + i * Y_GAP };
    });
  });
  const width = MARGIN * 2 + Math.max(0, layers.length - 1) * X_GAP + NODE_W;
  const height = MARGIN * 2 + (maxRows - 1) * Y_GAP + NODE_H;
  return { placed, width, height };
}

// Column-thread layout: variable-height nodes that list their columns, plus a
// map from (nodeId, column) to the absolute y of that column's row, so threads
// can connect the real column rows across hops.
function layoutColumns(graph: LineageGraph) {
  const layers = computeLayers(graph);
  const rowsOf = (n: LineageNode) => n.columns.slice(0, COL_MAX_ROWS);
  const heightOf = (n: LineageNode) => {
    const r = rowsOf(n).length;
    return r ? COL_HEADER_H + r * COL_ROW_H + COL_PAD_B : COL_HEADER_H + COL_PAD_B;
  };
  const layerHeight = (col: LineageNode[]) =>
    col.reduce((s, n) => s + heightOf(n), 0) + COL_GAP * Math.max(0, col.length - 1);
  const maxH = Math.max(1, ...layers.map(layerHeight));

  const placed: Record<string, ColPlaced> = {};
  const colY = new Map<string, Map<string, number>>();
  layers.forEach((col, l) => {
    let y = MARGIN + (maxH - layerHeight(col)) / 2;
    const x = MARGIN + l * COL_X_GAP;
    for (const node of col) {
      const rows = rowsOf(node);
      placed[node.id] = { node, x, y, h: heightOf(node), rows };
      const ys = new Map<string, number>();
      rows.forEach((c, i) => ys.set(c, y + COL_HEADER_H + i * COL_ROW_H + COL_ROW_H / 2));
      colY.set(node.id, ys);
      y += heightOf(node) + COL_GAP;
    }
  });
  const width = MARGIN * 2 + Math.max(0, layers.length - 1) * COL_X_GAP + COL_W;
  const height = MARGIN * 2 + maxH;
  return { placed, colY, width, height };
}

// Transitively highlight a column's whole lineage: every thread reachable from
// (nodeId, col) following links in both directions. This is the full-depth
// trace, e.g. result.revenue back to the source table column.
function traceColumn(graph: LineageGraph, nodeId: string, col: string) {
  const SEP = "␟";
  const key = (id: string, c: string) => id + SEP + c;
  const seen = new Set<string>([key(nodeId, col)]);
  const stack: string[] = [key(nodeId, col)];
  const threads = new Set<string>();
  const pairs: [string, string][] = [[nodeId, col]];
  while (stack.length) {
    const cur = stack.pop()!;
    graph.edges.forEach((e, ei2) => e.columns.forEach((l, li) => {
      const a = key(e.source, l.from_col);
      const b = key(e.target, l.to_col);
      if (cur === a || cur === b) {
        threads.add(ei2 + ":" + li);
        const ends: [string, string, string][] = [
          [a, e.source, l.from_col], [b, e.target, l.to_col],
        ];
        for (const [nx, id, c] of ends) {
          if (!seen.has(nx)) { seen.add(nx); stack.push(nx); pairs.push([id, c]); }
        }
      }
    }));
  }
  return { threads, endpoints: seen, key, pairs };
}

export function LineageMap({ open, onClose, sql, result }: {
  open: boolean; onClose: () => void; sql: string; result?: RunResult | null;
}) {
  useEscape(open, onClose);
  const [graph, setGraph] = useState<LineageGraph | null>(null);
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState(1);
  const [hover, setHover] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [cols, setCols] = useState(false);
  const [hotCol, setHotCol] = useState<{ id: string; col: string } | null>(null);
  const [pinned, setPinned] = useState<string | null>(null);  // the block clicked into

  useEffect(() => {
    if (!open) return;
    setGraph(null); setError(""); setZoom(1); setHover(null); setSelected(null);
    setHotCol(null); setPinned(null);
    fetchLineage(sql).then(setGraph).catch((e) => setError(String(e.message ?? e)));
  }, [open, sql]);

  // Click a block -> drop to column level focused on it. Its lineage (the block
  // plus everything it joins to) stays lit; the rest dims.
  function pinBlock(id: string) {
    setPinned((p) => (p === id ? null : id));
    setCols(true);
  }
  const pinnedRelated = useMemo(() => {
    if (!pinned || !graph) return null;
    const set = new Set<string>([pinned]);
    for (const e of graph.edges) {
      if (e.source === pinned) set.add(e.target);
      if (e.target === pinned) set.add(e.source);
    }
    return set;
  }, [pinned, graph]);

  const findings: CheckResult[] = (result?.checks ?? []).filter(
    (c) => STRUCTURAL.has(c.id) && (c.status === "FAIL" || c.status === "ERROR" || c.status === "WARN")
  );
  const hasCrossFinding = findings.some((c) => c.id === "S-STAT-002");
  const related = useMemo(
    () => (selected && graph ? relatedTo(selected, graph) : { edges: new Set<number>(), nodes: new Set<string>() }),
    [selected, graph]
  );

  const [tip, setTip] = useState<{ x: number; y: number; lines: string[]; risk?: boolean } | null>(null);
  const adj = useMemo(() => {
    const inc = new Map<string, string[]>();
    const out = new Map<string, string[]>();
    const lbl = new Map((graph?.nodes ?? []).map((n) => [n.id, n.label]));
    for (const e of graph?.edges ?? []) {
      out.set(e.source, [...(out.get(e.source) ?? []), lbl.get(e.target) ?? e.target]);
      inc.set(e.target, [...(inc.get(e.target) ?? []), lbl.get(e.source) ?? e.source]);
    }
    return { inc, out };
  }, [graph]);

  function nodeTip(n: LineageNode): string[] {
    const lines = [`${KIND_LABEL[n.kind] ?? n.kind} · ${n.detail || n.label}`];
    const reads = adj.inc.get(n.id);
    const feeds = adj.out.get(n.id);
    if (reads?.length) lines.push(`reads ${reads.join(", ")}`);
    if (feeds?.length) lines.push(`feeds ${feeds.join(", ")}`);
    if (n.columns.length) {
      const shown = n.columns.slice(0, 8).join(", ");
      const more = n.columns.length > 8 ? ` +${n.columns.length - 8}` : "";
      lines.push(`columns: ${shown}${more}`);
    }
    for (const c of n.calculations.slice(0, 5)) lines.push(`ƒ ${c}`);
    for (const f of n.filters.slice(0, 4)) lines.push(`where ${f}`);
    if (n.flags.length) lines.push(`flag: ${n.flags.join(", ")}`);
    return lines;
  }

  function showNodeTip(n: LineageNode, e: { clientX: number; clientY: number }) {
    if (!selected) setHover(n.id);
    setTip({ x: e.clientX, y: e.clientY, lines: nodeTip(n), risk: n.flags.length > 0 });
  }

  function colTipLines(nodeId: string, col: string): string[] {
    const lbl = (id: string) => graph?.nodes.find((n) => n.id === id)?.label ?? id;
    const up: string[] = []; const down: string[] = [];
    for (const e of graph?.edges ?? []) {
      for (const l of e.columns) {
        if (e.target === nodeId && l.to_col === col) up.push(`${lbl(e.source)}.${l.from_col}`);
        if (e.source === nodeId && l.from_col === col) down.push(`${lbl(e.target)}.${l.to_col}`);
      }
    }
    const lines: string[] = [];
    if (up.length) lines.push(`from ${up.join(", ")}`);
    if (down.length) lines.push(`into ${down.join(", ")}`);
    if (!up.length && !down.length) lines.push("no traced lineage");
    return lines;
  }

  function nodeCalc(nodeId: string, col: string): string | null {
    const n = graph?.nodes.find((x) => x.id === nodeId);
    return n?.calculations.find((c) => c.startsWith(`${col} = `)) ?? null;
  }
  const labelOf = (id: string) => graph?.nodes.find((n) => n.id === id)?.label ?? id;

  function showColTip(nodeId: string, label: string, col: string, e: { clientX: number; clientY: number }) {
    setHotCol({ id: nodeId, col });
    const lines = [`${label}.${col}`];
    const own = nodeCalc(nodeId, col);
    if (own) lines.push(`= ${own.split(" = ").slice(1).join(" = ")}`);
    lines.push(...colTipLines(nodeId, col));
    // Calculations applied anywhere along this metric's lineage, deepest hops
    // included, so a metric shows how it was actually derived across layers.
    if (graph) {
      const chain: string[] = [];
      for (const [id, c] of traceColumn(graph, nodeId, col).pairs) {
        if (id === nodeId && c === col) continue;
        const calc = nodeCalc(id, c);
        if (calc) chain.push(`${labelOf(id)}.${calc}`);
      }
      if (chain.length) { lines.push("derivation:"); lines.push(...chain); }
    }
    setTip({ x: e.clientX, y: e.clientY, lines });
  }

  const lay = useMemo(() => (graph ? layout(graph) : null), [graph]);
  const clay = useMemo(() => (graph && cols ? layoutColumns(graph) : null), [graph, cols]);
  const trace = useMemo(
    () => (graph && hotCol ? traceColumn(graph, hotCol.id, hotCol.col) : null),
    [graph, hotCol]
  );
  const baseW = cols && clay ? clay.width : lay?.width ?? 640;
  const baseH = cols && clay ? clay.height : lay?.height ?? 200;
  const canvasW = Math.max(baseW, 640);
  const canvasH = Math.max(baseH, 200);

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
        <button className={`coltoggle ${cols ? "on" : ""}`} onClick={() => setCols((v) => !v)}
          title="Trace columns end to end">Columns</button>
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

      {findings.length > 0 && (
        <div className="map-findings">
          <span className="mf-label">Findings on this build</span>
          {findings.map((c) => (
            <div key={c.id} className={`mf-card s-${c.status} ${selected === c.id ? "on" : ""}`}
              onClick={() => setSelected(selected === c.id ? null : c.id)}>
              <span className={`statuspill s-${c.status}`}>{c.status}</span>
              <span className="mf-id mono">{c.id}</span>
              <span className="mf-obs">{c.observed}</span>
            </div>
          ))}
        </div>
      )}
      {selected && findings.find((c) => c.id === selected)?.remediation && (
        <div className="mf-detail">
          {findings.find((c) => c.id === selected)!.remediation}
        </div>
      )}

      <div className="map-canvas">
        {error && <div className="empty">{error}</div>}
        {!graph && !error && <div className="empty">Mapping your query…</div>}
        {graph && (cols ? clay : lay) && (
          <svg width={canvasW * zoom} height={canvasH * zoom}
            viewBox={`0 0 ${canvasW} ${canvasH}`} className={`map-svg ${cols ? "colmode" : ""}`}
            onClick={() => setPinned(null)}>
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

            {cols && clay && graph.edges.flatMap((e, ei) => e.columns.map((l, li) => {
              const sy = clay.colY.get(e.source)?.get(l.from_col);
              const ty = clay.colY.get(e.target)?.get(l.to_col);
              const s = clay.placed[e.source]; const t = clay.placed[e.target];
              if (sy == null || ty == null || !s || !t) return null;
              const sx = s.x + COL_W, tx = t.x;
              const dx = Math.max(40, (tx - sx) / 2);
              const d = `M${sx},${sy} C${sx + dx},${sy} ${tx - dx},${ty} ${tx},${ty}`;
              let hot = false, dim = false;
              if (trace != null) {
                hot = trace.threads.has(`${ei}:${li}`); dim = !hot;
              } else if (pinned != null) {
                hot = e.source === pinned || e.target === pinned; dim = !hot;
              }
              return (
                <path key={`${ei}:${li}`} d={d}
                  className={`thread ${e.risk ? "risk" : ""} ${hot ? "hot" : ""} ${dim ? "dim" : ""}`} />
              );
            }))}

            {cols && clay && Object.values(clay.placed).map((cp) => (
              <ColumnNode key={cp.node.id} cp={cp} endpoints={trace?.endpoints ?? null} traceKey={trace?.key}
                selected={pinned === cp.node.id}
                dim={pinnedRelated != null && !pinnedRelated.has(cp.node.id)}
                onSelect={() => setPinned((p) => (p === cp.node.id ? null : cp.node.id))}
                onCol={(col, ev) => showColTip(cp.node.id, cp.node.label, col, ev)}
                onMove={(ev) => setTip((tp) => (tp ? { ...tp, x: ev.clientX, y: ev.clientY } : tp))}
                onLeave={() => { setHotCol(null); setTip(null); }} />
            ))}

            {!cols && lay && graph.edges.map((e, i) => {
              const s = lay.placed[e.source]; const t = lay.placed[e.target];
              if (!s || !t) return null;
              const sx = s.x + NODE_W, sy = s.y + NODE_H / 2;
              const tx = t.x, ty = t.y + NODE_H / 2;
              const dx = Math.max(40, (tx - sx) / 2);
              const mx = (sx + tx) / 2, my = (sy + ty) / 2;
              const label = e.relation === "from" ? "" : e.relation;
              let state = "";
              if (selected) state = related.edges.has(i) ? "hot" : "dim";
              else if (hover) state = e.source === hover || e.target === hover ? "hot" : "dim";
              const clickable = e.risk && hasCrossFinding;
              const edgeLabel = e.risk
                ? (hasCrossFinding ? "no key · S-STAT-002" : "no key")
                : label;
              const tipLines = [
                e.relation === "from" ? "feeds result" : e.relation.toUpperCase(),
                ...(e.on ? [e.on] : []),
                ...(e.risk ? ["No join key: every left row pairs with every right row, multiplying the result."] : []),
                ...(clickable ? ["Flagged by S-STAT-002 · click to trace"] : []),
              ];
              const pathD = `M${sx},${sy} C${sx + dx},${sy} ${tx - dx},${ty} ${tx},${ty}`;
              return (
                <g key={i} className={`edge ${e.risk ? "risk" : ""} ${state} ${clickable ? "clickable" : ""}`}
                  onClick={clickable ? () => setSelected("S-STAT-002") : undefined}
                  onMouseEnter={(ev) => setTip({ x: ev.clientX, y: ev.clientY, lines: tipLines, risk: e.risk })}
                  onMouseMove={(ev) => setTip((t) => (t ? { ...t, x: ev.clientX, y: ev.clientY } : t))}
                  onMouseLeave={() => setTip(null)}>
                  <path className="edge-hit" d={pathD} />
                  <path className="edge-line" d={pathD} markerEnd={`url(#${e.risk ? "arrow-risk" : "arrow"})`} />
                  <path className="flowline" d={pathD} />
                  {(edgeLabel || e.risk) && (
                    <text x={mx} y={my - 6} className="edge-label" textAnchor="middle">
                      {edgeLabel}
                    </text>
                  )}
                </g>
              );
            })}
            {!cols && lay && Object.values(lay.placed).map(({ node, x, y }) => {
              let dim = false; let linked = false;
              if (selected) { linked = related.nodes.has(node.id); dim = !linked; }
              else if (hover != null) dim = !neighbors.has(node.id);
              return (
                <Node key={node.id} node={node} x={x} y={y} dim={dim} linked={linked}
                  onSelect={() => pinBlock(node.id)}
                  onEnter={(ev) => showNodeTip(node, ev)}
                  onMove={(ev) => setTip((t) => (t ? { ...t, x: ev.clientX, y: ev.clientY } : t))}
                  onLeave={() => { setHover(null); setTip(null); }} />
              );
            })}
          </svg>
        )}
      </div>

      {tip && (
        <div className={`maptip ${tip.risk ? "risk" : ""}`}
          style={{ left: Math.min(tip.x + 16, window.innerWidth - 280), top: tip.y + 16 }}>
          {tip.lines.map((l, i) => (
            <div key={i} className={i === 0 ? "tt-head" : "tt-line"}>{l}</div>
          ))}
        </div>
      )}
    </div>
  );
}

function Node({ node, x, y, dim, linked, onSelect, onEnter, onMove, onLeave }: {
  node: LineageNode; x: number; y: number; dim: boolean; linked: boolean;
  onSelect: () => void;
  onEnter: (e: { clientX: number; clientY: number }) => void;
  onMove: (e: { clientX: number; clientY: number }) => void;
  onLeave: () => void;
}) {
  const hasFlag = node.flags.length > 0;
  const badge = nodeBadge(node);
  return (
    <g className={`node clickable ${dim ? "dim" : ""} ${linked ? "linked" : ""}`}
      transform={`translate(${x},${y})`}
      onClick={(ev) => { ev.stopPropagation(); onSelect(); }}
      onMouseEnter={onEnter} onMouseMove={onMove} onMouseLeave={onLeave}>
      <rect width={NODE_W} height={NODE_H} rx={14} className={`nbox k-${node.kind}`} />
      <rect width={5} height={NODE_H} rx={2} className={`naccent k-${node.kind}`} />
      <text x={18} y={23} className="nkind">{KIND_LABEL[node.kind] ?? node.kind}</text>
      <text x={18} y={41} className="nlabel">{trim(node.label, 18)}</text>
      {badge && <text x={18} y={56} className="nbadge">{badge}</text>}
      {hasFlag && <circle cx={NODE_W - 16} cy={18} r={4} className="nflag" />}
    </g>
  );
}

function nodeBadge(n: LineageNode): string {
  const parts: string[] = [];
  if (n.columns.length) parts.push(`${n.columns.length} col${n.columns.length === 1 ? "" : "s"}`);
  if (n.calculations.length) parts.push(`ƒ${n.calculations.length}`);
  if (n.filters.length) parts.push("filtered");
  return parts.join(" · ");
}

function ColumnNode({ cp, endpoints, traceKey, selected, dim, onSelect, onCol, onMove, onLeave }: {
  cp: ColPlaced;
  endpoints: Set<string> | null;
  traceKey?: (id: string, c: string) => string;
  selected: boolean;
  dim: boolean;
  onSelect: () => void;
  onCol: (col: string, e: { clientX: number; clientY: number }) => void;
  onMove: (e: { clientX: number; clientY: number }) => void;
  onLeave: () => void;
}) {
  const { node, x, y, h, rows } = cp;
  const calcNames = new Set(node.calculations.map((c) => c.split(" = ")[0]));
  return (
    <g className={`cnode ${selected ? "selected" : ""} ${dim ? "dim" : ""}`} transform={`translate(${x},${y})`}>
      <rect width={COL_W} height={h} rx={14} className={`nbox k-${node.kind}`} />
      <rect width={5} height={h} rx={2} className={`naccent k-${node.kind}`} />
      <rect className="cnode-head" width={COL_W} height={COL_HEADER_H} rx={14}
        onClick={(ev) => { ev.stopPropagation(); onSelect(); }} />
      <text x={18} y={18} className="nkind">{KIND_LABEL[node.kind] ?? node.kind}</text>
      <text x={18} y={33} className="nlabel">{trim(node.label, 20)}</text>
      {rows.map((c, i) => {
        const ry = COL_HEADER_H + i * COL_ROW_H + COL_ROW_H / 2;
        const on = endpoints != null && traceKey != null && endpoints.has(traceKey(node.id, c));
        const dim = endpoints != null && !on;
        return (
          <g key={c} className={`crow ${on ? "on" : ""} ${dim ? "dim" : ""}`}
            onMouseEnter={(ev) => onCol(c, ev)} onMouseMove={onMove} onMouseLeave={onLeave}>
            <rect x={7} y={ry - COL_ROW_H / 2} width={COL_W - 14} height={COL_ROW_H} className="crow-hit" />
            <circle cx={16} cy={ry} r={2.6} className="cdot" />
            <text x={26} y={ry + 3.6} className="ctext">{calcNames.has(c) ? "ƒ " : ""}{trim(c, 20)}</text>
          </g>
        );
      })}
    </g>
  );
}

function trim(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
