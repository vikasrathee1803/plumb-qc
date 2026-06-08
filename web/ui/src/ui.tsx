import { useEffect, type ReactNode } from "react";

export function useEscape(active: boolean, onClose: () => void) {
  useEffect(() => {
    if (!active) return;
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [active, onClose]);
}

export function Switch({ checked, onChange, disabled }: {
  checked: boolean; onChange: (v: boolean) => void; disabled?: boolean;
}) {
  return (
    <span className="sw">
      <input type="checkbox" checked={checked} disabled={disabled}
        onChange={(e) => onChange(e.target.checked)} />
      <span className="track"><span className="knob" /></span>
    </span>
  );
}

export function SwitchRow({ checked, onChange, label, disabled }: {
  checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean;
}) {
  return (
    <label className={`swrow ${disabled ? "disabled" : ""}`}>
      <Switch checked={checked} onChange={onChange} disabled={disabled} />
      {label}
    </label>
  );
}

export function Segmented<T extends string>({ value, onChange, options, full }: {
  value: T; onChange: (v: T) => void; options: { value: T; label: string }[]; full?: boolean;
}) {
  return (
    <div className={`seg ${full ? "full" : ""}`}>
      {options.map((o) => (
        <button key={o.value} className={value === o.value ? "on" : ""}
          onClick={() => onChange(o.value)}>{o.label}</button>
      ))}
    </div>
  );
}

export function Drawer({ open, onClose, title, children }: {
  open: boolean; onClose: () => void; title: string; children: ReactNode;
}) {
  useEscape(open, onClose);
  return (
    <>
      <div className={`scrim ${open ? "open" : ""}`} onClick={onClose} />
      <aside className={`drawer ${open ? "open" : ""}`} role="dialog" aria-hidden={!open}>
        <div className="drawer-head">
          <h2>{title}</h2>
          <span className="spacer" />
          <button className="done" onClick={onClose}>Done</button>
        </div>
        <div className="drawer-body">{children}</div>
      </aside>
    </>
  );
}
