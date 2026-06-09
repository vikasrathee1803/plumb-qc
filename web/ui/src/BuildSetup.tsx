import { type ReactNode, useEffect, useRef, useState } from "react";
import { fetchColumns } from "./api";
import type { CheckState, ColumnsInfo } from "./types";

// Surfaces the few inputs the column checks need, read straight from the build,
// and pre-fills them so the user never has to remember hidden config. Each input
// maps to one or more checks: a grain key (uniqueness + not-null), a freshness
// timestamp, and amount columns (non-negative).
type State = Record<string, CheckState>;

function setParam(
  next: State, id: string, param: string, value: unknown, enabled: boolean
): void {
  next[id] = { id, enabled, params: { ...(next[id]?.params ?? {}), [param]: value } };
}

export function BuildSetup({ sql, checkState, setCheckState }: {
  sql: string;
  checkState: State;
  setCheckState: (fn: (prev: State) => State) => void;
}) {
  const [info, setInfo] = useState<ColumnsInfo | null>(null);
  const filledFor = useRef<string>("");

  useEffect(() => {
    if (!sql.trim()) { setInfo(null); return; }
    const t = setTimeout(() => {
      fetchColumns(sql).then(setInfo).catch(() => setInfo(null));
    }, 400);
    return () => clearTimeout(t);
  }, [sql]);

  // Pre-fill the inputs from the suggestions once per distinct column set. The
  // fingerprint guard means re-renders within the same build keep the user's
  // edits; a new column set (a different build) re-suggests from scratch.
  useEffect(() => {
    if (!info || info.columns.length === 0) return;
    const fingerprint = info.columns.join(",");
    if (filledFor.current === fingerprint) return;
    filledFor.current = fingerprint;
    const s = info.suggestions;
    setCheckState((prev) => {
      const next: State = { ...prev };
      const keys = s.key ?? [];
      setParam(next, "D-GRAIN-001", "key", keys, keys.length > 0);
      setParam(next, "D-NULL-001", "key", keys, keys.length > 0);
      const ts = (s.timestamp ?? [])[0] ?? "";
      setParam(next, "D-FRESH-001", "event_ts_col", ts, !!ts);
      const amt = s.amount ?? [];
      setParam(next, "D-POS-001", "columns", amt, amt.length > 0);
      return next;
    });
  }, [info, setCheckState]);

  if (!info || info.columns.length === 0) return null;

  const keyCols = (checkState["D-GRAIN-001"]?.params?.key as string[]) ?? [];
  const tsCol = (checkState["D-FRESH-001"]?.params?.event_ts_col as string) ?? "";
  const amtCols = (checkState["D-POS-001"]?.params?.columns as string[]) ?? [];

  const setKey = (cols: string[]) =>
    setCheckState((prev) => {
      const next: State = { ...prev };
      setParam(next, "D-GRAIN-001", "key", cols, cols.length > 0);
      setParam(next, "D-NULL-001", "key", cols, cols.length > 0);
      return next;
    });
  const setTs = (col: string) =>
    setCheckState((prev) => {
      const next: State = { ...prev };
      setParam(next, "D-FRESH-001", "event_ts_col", col, !!col);
      return next;
    });
  const setAmt = (cols: string[]) =>
    setCheckState((prev) => {
      const next: State = { ...prev };
      setParam(next, "D-POS-001", "columns", cols, cols.length > 0);
      return next;
    });

  return (
    <div className="bsetup">
      <div className="bs-head">
        <span className="bs-title">Tune checks to your build</span>
        <span className="bs-sub">{info.columns.length} columns read from your SQL, pre-filled below</span>
      </div>
      <Field label="Unique key" hint="rows should be unique on these (grain + not-null)">
        <ChipMulti options={info.columns} selected={keyCols} onChange={setKey} />
      </Field>
      <Field label="Event timestamp" hint="freshness: how recent the newest row is">
        <ColSelect options={info.columns} value={tsCol} onChange={setTs} />
      </Field>
      <Field label="Amount columns" hint="flagged if any value is negative">
        <ChipMulti options={info.columns} selected={amtCols} onChange={setAmt} />
      </Field>
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint: string; children: ReactNode }) {
  return (
    <div className="bs-field">
      <div className="bs-label">{label}<span className="bs-hint">{hint}</span></div>
      {children}
    </div>
  );
}

function ChipMulti({ options, selected, onChange }: {
  options: string[]; selected: string[]; onChange: (v: string[]) => void;
}) {
  const toggle = (c: string) =>
    onChange(selected.includes(c) ? selected.filter((x) => x !== c) : [...selected, c]);
  return (
    <div className="bs-chips">
      {options.map((c) => (
        <button key={c} type="button"
          className={`bs-chip ${selected.includes(c) ? "on" : ""}`}
          onClick={() => toggle(c)}>{c}</button>
      ))}
    </div>
  );
}

function ColSelect({ options, value, onChange }: {
  options: string[]; value: string; onChange: (v: string) => void;
}) {
  return (
    <select className="bs-select" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">none</option>
      {options.map((c) => <option key={c} value={c}>{c}</option>)}
    </select>
  );
}
