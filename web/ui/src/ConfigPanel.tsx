import { useState } from "react";
import type { CatalogCheck, CheckState, ParamHint } from "./types";

const FAMILY_ORDER = ["static", "metadata", "assertions", "regression", "performance"];

export function paramToInput(value: unknown, hint: ParamHint): string {
  if (value === undefined || value === null) return "";
  if (hint.type === "list" && Array.isArray(value)) return value.join(", ");
  return String(value);
}

export function inputToParam(raw: string, hint: ParamHint): unknown {
  const t = raw.trim();
  if (t === "") return undefined;
  if (hint.type === "list") return t.split(",").map((s) => s.trim()).filter(Boolean);
  if (hint.type === "int") return parseInt(t, 10);
  if (hint.type === "float") return parseFloat(t);
  if (hint.type === "bool") return t.toLowerCase() === "true";
  return t;
}

export function ConfigPanel({
  catalog,
  state,
  setState,
}: {
  catalog: CatalogCheck[];
  state: Record<string, CheckState>;
  setState: (s: Record<string, CheckState>) => void;
}) {
  const [open, setOpen] = useState(false);

  const sqlCatalog = catalog.filter((c) => !c.family.startsWith("tableau"));
  const enabledCount = Object.values(state).filter((s) => s.enabled).length;

  function toggle(id: string, enabled: boolean) {
    setState({ ...state, [id]: { ...state[id], id, enabled } });
  }
  function setParam(id: string, name: string, raw: string, hint: ParamHint) {
    const cur = state[id] ?? { id, enabled: true, params: {} };
    const params = { ...cur.params };
    const v = inputToParam(raw, hint);
    if (v === undefined) delete params[name];
    else params[name] = v;
    setState({ ...state, [id]: { ...cur, id, params } });
  }
  function setAll(enabled: boolean) {
    const next: Record<string, CheckState> = {};
    for (const c of sqlCatalog) next[c.id] = { ...(state[c.id] ?? { id: c.id, params: {} }), id: c.id, enabled };
    setState({ ...state, ...next });
  }

  const byFamily: Record<string, CatalogCheck[]> = {};
  for (const c of sqlCatalog) (byFamily[c.family] ??= []).push(c);
  const families = Object.keys(byFamily).sort(
    (a, b) => (FAMILY_ORDER.indexOf(a) + 99) % 99 - ((FAMILY_ORDER.indexOf(b) + 99) % 99)
  );

  return (
    <div className="card pad" style={{ marginTop: 16 }}>
      <div className="section-head" onClick={() => setOpen(!open)}>
        <span className={`caret ${open ? "open" : ""}`}>▸</span>
        <h3>Configure checks</h3>
        <span className="muted">{enabledCount} of {sqlCatalog.length} enabled</span>
      </div>

      {open && (
        <>
          <div className="cfg-toolbar">
            <button className="btn-ghost" onClick={() => setAll(true)}>Enable all</button>
            <button className="btn-ghost" onClick={() => setAll(false)}>Disable all</button>
            <span className="muted">toggle checks and edit their params; what you see runs</span>
          </div>
          {families.map((fam) => (
            <div className="cfg-family" key={fam}>
              <div className="fam-label">{fam}</div>
              {byFamily[fam].map((c) => {
                const st = state[c.id] ?? { id: c.id, enabled: false, params: {} };
                return (
                  <div className="cfg-check" key={c.id}>
                    <div className="row1">
                      <label className="toggle">
                        <input type="checkbox" checked={st.enabled}
                               onChange={(e) => toggle(c.id, e.target.checked)} />
                      </label>
                      <span className="cid">{c.id}</span>
                      <span className="sev">{c.default_severity}</span>
                      <span className="cdesc">{c.description}</span>
                    </div>
                    {st.enabled && c.params.length > 0 && (
                      <div className="cfg-params">
                        {c.params.map((p) => (
                          <label key={p.name}>
                            {p.name}{p.required ? " *" : ""}
                            <input
                              type="text"
                              placeholder={p.type === "list" ? "comma, separated" : p.type}
                              value={paramToInput(st.params[p.name], p)}
                              onChange={(e) => setParam(c.id, p.name, e.target.value, p)}
                            />
                          </label>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </>
      )}
    </div>
  );
}
