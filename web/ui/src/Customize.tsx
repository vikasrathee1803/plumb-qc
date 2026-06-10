import { paramToInput, inputToParam } from "./paramio";
import { Switch } from "./ui";
import type { CatalogCheck, CheckState, CustomCheck, ParamHint } from "./types";

const FAMILY_ORDER = [
  "static", "metadata", "assertions", "regression", "performance", "tableau_static",
  "tableau_live", "migration_parity",
];
const FAMILY_LABEL: Record<string, string> = {
  static: "Static analysis", metadata: "Schema & metadata", assertions: "Data assertions",
  regression: "Regression vs baseline", performance: "Performance & cost", tableau_static: "Tableau",
  tableau_live: "Tableau (live)", migration_parity: "Migration parity",
};
const SEVERITIES = ["BLOCKER", "HIGH", "MEDIUM", "LOW", "INFO"];

export function ChecksEditor({ catalog, state, setState }: {
  catalog: CatalogCheck[];
  state: Record<string, CheckState>;
  setState: (s: Record<string, CheckState>) => void;
}) {
  function toggle(id: string, enabled: boolean) {
    setState({ ...state, [id]: { ...(state[id] ?? { id, params: {} }), id, enabled } });
  }
  function setParam(id: string, name: string, raw: string, hint: ParamHint) {
    const cur = state[id] ?? { id, enabled: true, params: {} };
    const params = { ...cur.params };
    const v = inputToParam(raw, hint);
    if (v === undefined) delete params[name]; else params[name] = v;
    setState({ ...state, [id]: { ...cur, id, params } });
  }
  function setAll(enabled: boolean) {
    const next = { ...state };
    for (const c of catalog) next[c.id] = { ...(state[c.id] ?? { id: c.id, params: {} }), id: c.id, enabled };
    setState(next);
  }

  const byFamily: Record<string, CatalogCheck[]> = {};
  for (const c of catalog) (byFamily[c.family] ??= []).push(c);
  const families = Object.keys(byFamily).sort((a, b) => FAMILY_ORDER.indexOf(a) - FAMILY_ORDER.indexOf(b));

  return (
    <>
      <div className="toolbtns">
        <button className="ghost" onClick={() => setAll(true)}>Turn all on</button>
        <button className="ghost" onClick={() => setAll(false)}>Turn all off</button>
      </div>
      {families.map((fam) => (
        <div key={fam}>
          <div className="dgroup-label">{FAMILY_LABEL[fam] ?? fam}</div>
          {byFamily[fam].map((c) => {
            const st = state[c.id] ?? { id: c.id, enabled: false, params: {} };
            return (
              <div className="ck" key={c.id}>
                <div className="ck-main">
                  <div className="ck-text">
                    <div className="ck-name">{c.description || c.name}</div>
                    <div className="ck-desc"><span className="ck-id">{c.id}</span> · {c.default_severity}</div>
                  </div>
                  <Switch checked={st.enabled} onChange={(v) => toggle(c.id, v)} />
                </div>
                {st.enabled && c.params.length > 0 && (
                  <div className="ck-params">
                    {c.params.map((p) => (
                      <label key={p.name} className={p.type === "sql" ? "wide" : ""}>
                        {p.name}{p.required ? " *" : ""}
                        <input type="text"
                          placeholder={p.type === "list" ? "comma, separated" : p.type}
                          value={paramToInput(st.params[p.name], p)}
                          onChange={(e) => setParam(c.id, p.name, e.target.value, p)} />
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
  );
}

export function CustomChecksEditor({ checks, setChecks }: {
  checks: CustomCheck[]; setChecks: (c: CustomCheck[]) => void;
}) {
  function update(i: number, patch: Partial<CustomCheck>) {
    setChecks(checks.map((c, idx) => (idx === i ? { ...c, ...patch } : c)));
  }
  function add() {
    setChecks([...checks, { name: "", severity: "MEDIUM", sql: "SELECT * FROM {{ target }} WHERE " }]);
  }
  function remove(i: number) { setChecks(checks.filter((_, idx) => idx !== i)); }

  return (
    <>
      <div className="dgroup-label">Your own checks</div>
      <div className="note" style={{ marginTop: 0, marginBottom: 12 }}>
        Write a SQL query that returns rows you do <b>not</b> want. If it returns any, the check fails.
        Use <span className="mono">{"{{ target }}"}</span> for the build under test.
      </div>
      {checks.map((c, i) => (
        <div className="custom-card" key={i}>
          <div className="custom-row">
            <input type="text" placeholder="check name (e.g. amounts are non-negative)"
              value={c.name} onChange={(e) => update(i, { name: e.target.value })} />
            <select value={c.severity} onChange={(e) => update(i, { severity: e.target.value })}>
              {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <button className="rm" title="remove" onClick={() => remove(i)}>×</button>
          </div>
          <textarea rows={2} value={c.sql} spellCheck={false}
            onChange={(e) => update(i, { sql: e.target.value })} />
        </div>
      ))}
      <button className="add-custom" onClick={add}>+ Add a custom check</button>
    </>
  );
}
