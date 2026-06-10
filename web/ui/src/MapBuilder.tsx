import { useEffect, useState } from "react";
import { buildParityMap, fetchWorkbookSources } from "./api";
import { Drawer, SwitchRow } from "./ui";

// One editable map entry, seeded from a workbook table source. Everything
// is plain text the way an analyst thinks about it; the server validates
// through the REAL ParityMap model, so the rules (3-part new names, unique
// olds, tolerances as fractions <= 1) are enforced with loud messages
// without anyone hand-writing YAML.
interface EntryDraft {
  old: string;
  next: string;
  keys: string;
  grain: string;
  columns: string;
  tolerance: string;
}

function parseList(text: string): string[] {
  return text.split(",").map((s) => s.trim()).filter(Boolean);
}

function parsePairs(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const part of parseList(text)) {
    const [oldCol, newCol] = part.split("=").map((s) => s.trim());
    if (oldCol && newCol) out[oldCol] = newCol;
  }
  return out;
}

export function MapBuilder({ open, onClose, workbook, onUse }: {
  open: boolean;
  onClose: () => void;
  workbook: File | null;
  onUse: (map: File) => void;
}) {
  const [entries, setEntries] = useState<EntryDraft[]>([]);
  const [notes, setNotes] = useState<string[]>([]);
  const [tolerance, setTolerance] = useState("0");
  const [identityFallback, setIdentityFallback] = useState(true);
  const [ignore, setIgnore] = useState("");
  const [yamlText, setYamlText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open || !workbook) return;
    setError(""); setYamlText(""); setEntries([]); setNotes([]);
    fetchWorkbookSources(workbook).then((relations) => {
      const tables = relations.filter((r) => r.kind === "table" && r.fqn);
      setEntries(tables.map((r) => ({
        old: r.fqn as string, next: r.fqn as string,
        keys: "", grain: "", columns: "", tolerance: "",
      })));
      const extra: string[] = [];
      const custom = relations.filter((r) => r.kind === "custom_sql").length;
      if (custom) extra.push(
        `${custom} custom-SQL source${custom === 1 ? "" : "s"}: the SQL runs verbatim on both sides - nothing to map.`
      );
      for (const r of relations.filter((x) => x.kind === "refused")) {
        extra.push(`${r.datasource}: refused (${r.refusal_reason}) - not provable by parity; verify manually.`);
      }
      setNotes(extra);
    }).catch((e) => setError(String(e instanceof Error ? e.message : e)));
  }, [open, workbook]);

  function update(i: number, patch: Partial<EntryDraft>) {
    setEntries(entries.map((e, idx) => (idx === i ? { ...e, ...patch } : e)));
  }

  function payload() {
    return {
      version: 1,
      defaults: {
        tolerance_pct: Number(tolerance) || 0,
        identity_fallback: identityFallback,
      },
      objects: entries.map((e) => ({
        old: e.old,
        new: e.next,
        keys: parseList(e.keys),
        grain: parseList(e.grain),
        columns: parsePairs(e.columns),
        ...(e.tolerance.trim() ? { tolerance_pct: Number(e.tolerance) } : {}),
      })),
      ignore: parseList(ignore),
    };
  }

  async function preview(): Promise<string | null> {
    setBusy(true); setError("");
    try {
      const text = await buildParityMap(payload());
      setYamlText(text);
      return text;
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
      return null;
    } finally { setBusy(false); }
  }

  async function use() {
    const text = yamlText || (await preview());
    if (!text) return;
    onUse(new File([text], "galaxy-map.yml", { type: "text/yaml" }));
    onClose();
  }

  async function download() {
    const text = yamlText || (await preview());
    if (!text) return;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/yaml" }));
    a.download = "galaxy-map.yml";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <Drawer open={open} onClose={onClose} title="Build the migration map">
      {!workbook ? (
        <div className="note">Choose the workbook first - the map starts from its sources.</div>
      ) : (
        <>
          <div className="note" style={{ marginTop: 0 }}>
            One row per Snowflake source found in <b>{workbook.name}</b>. Set the new
            (migrated) name for anything that moves; leave it unchanged for identity.
            Keys unlock distinct-count and row-fingerprint checks; grain proves grouped
            counts; renames map old columns to new ones.
          </div>
          {notes.map((n, i) => <div className="note" key={i}>{n}</div>)}
          {error && <p className="error">{error}</p>}

          {entries.map((e, i) => (
            <div className="custom-card" key={e.old}>
              <div className="dgroup-label" style={{ marginTop: 0 }}>{e.old}</div>
              <div className="ck-params" style={{ marginTop: 6 }}>
                <label className="wide">new (migrated) name
                  <input type="text" value={e.next} placeholder="DB.SCHEMA.TABLE"
                    onChange={(ev) => update(i, { next: ev.target.value })} />
                </label>
                <label>keys
                  <input type="text" value={e.keys} placeholder="CUSTOMER_ID, ..."
                    onChange={(ev) => update(i, { keys: ev.target.value })} />
                </label>
                <label>grain
                  <input type="text" value={e.grain} placeholder="REGION, SEGMENT"
                    onChange={(ev) => update(i, { grain: ev.target.value })} />
                </label>
                <label>column renames
                  <input type="text" value={e.columns} placeholder="OLD_COL=NEW_COL, ..."
                    onChange={(ev) => update(i, { columns: ev.target.value })} />
                </label>
                <label>tolerance (fraction)
                  <input type="text" value={e.tolerance} placeholder="default"
                    onChange={(ev) => update(i, { tolerance: ev.target.value })} />
                </label>
              </div>
            </div>
          ))}

          <div className="dgroup-label">Defaults</div>
          <div className="ck-params" style={{ marginTop: 6 }}>
            <label>tolerance (fraction, 0.01 = 1%)
              <input type="text" value={tolerance} onChange={(e) => setTolerance(e.target.value)} />
            </label>
            <label className="wide">ignore patterns
              <input type="text" value={ignore} placeholder="LEGACY_DB.SCRATCH.*, ..."
                onChange={(e) => setIgnore(e.target.value)} />
            </label>
          </div>
          <SwitchRow checked={identityFallback} onChange={setIdentityFallback}
            label="Unlisted objects compare under their own names (identity)" />

          <div className="toolbtns" style={{ marginTop: 12 }}>
            <button className="ghost" onClick={preview} disabled={busy}>Validate &amp; preview</button>
            <button className="ghost" onClick={download} disabled={busy}>Download .yml</button>
            <button className="run" style={{ width: "auto", padding: "8px 18px" }}
              onClick={use} disabled={busy}>Use this map</button>
          </div>
          {yamlText && <pre className="code" style={{ marginTop: 10 }}>{yamlText}</pre>}
        </>
      )}
    </Drawer>
  );
}
