import { type ReactNode, useEffect, useState } from "react";
import {
  deleteSnowflake, deleteTableau, fetchSnowflakeSettings, fetchTableauSettings,
  saveSnowflakeSettings, saveTableauSettings, testSnowflake, testTableau,
} from "./api";
import { Modal } from "./ui";
import type { SnowflakeSettings, TableauSettings, TestResult } from "./types";

const AUTH_OPTIONS = [
  { value: "snowflake_jwt", label: "Key-pair (JWT)" },
  { value: "externalbrowser", label: "SSO (browser)" },
  { value: "oauth", label: "OAuth token" },
];

const err = (e: unknown) => String(e instanceof Error ? e.message : e);

function Field({ label, wide, children }: { label: string; wide?: boolean; children: ReactNode }) {
  return (
    <label className={`set-field ${wide ? "wide" : ""}`}>
      <span>{label}</span>
      {children}
    </label>
  );
}

export function Settings({ open, onClose, onSaved }: {
  open: boolean; onClose: () => void; onSaved: () => void;
}) {
  return (
    <Modal open={open} onClose={onClose} title="Connections">
      <p className="set-note">
        Credentials stay on this machine: connection details in <code>~/.plumb</code>, and
        secrets (key passphrase, OAuth token, Tableau token) in your OS keychain. They are sent
        only to your own Snowflake or Tableau, never anywhere else.
      </p>
      <SnowflakeForm open={open} onSaved={onSaved} />
      <TableauForm open={open} />
    </Modal>
  );
}

function SnowflakeForm({ open, onSaved }: { open: boolean; onSaved: () => void }) {
  const [cur, setCur] = useState<SnowflakeSettings | null>(null);
  const [f, setF] = useState({
    account: "", user: "", authenticator: "snowflake_jwt", private_key_path: "",
    role: "", warehouse: "", passphrase: "", oauth_token: "",
  });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [test, setTest] = useState<TestResult | null>(null);

  function load() {
    fetchSnowflakeSettings().then((d) => {
      setCur(d);
      if (d.configured) {
        setF((p) => ({
          ...p, account: d.account ?? "", user: d.user ?? "",
          authenticator: d.authenticator ?? "snowflake_jwt", private_key_path: d.private_key_path ?? "",
          role: d.role ?? "", warehouse: d.warehouse ?? "", passphrase: "", oauth_token: "",
        }));
      }
    }).catch(() => undefined);
  }
  useEffect(() => { if (open) load(); }, [open]);

  const set = (k: string, v: string) => setF((p) => ({ ...p, [k]: v }));

  async function save() {
    setBusy(true); setMsg(null); setTest(null);
    try {
      const body: Record<string, unknown> = {
        account: f.account, user: f.user, authenticator: f.authenticator,
        role: f.role, warehouse: f.warehouse,
      };
      if (f.authenticator === "snowflake_jwt") {
        body.private_key_path = f.private_key_path;
        if (f.passphrase) body.passphrase = f.passphrase;
      }
      if (f.authenticator === "oauth" && f.oauth_token) body.oauth_token = f.oauth_token;
      await saveSnowflakeSettings(body);
      setMsg({ ok: true, text: "Saved to ~/.plumb. Secrets stored in your keychain." });
      load(); onSaved();
    } catch (e) { setMsg({ ok: false, text: err(e) }); }
    finally { setBusy(false); }
  }
  async function runTest() {
    setBusy(true); setTest(null);
    try { setTest(await testSnowflake()); }
    catch (e) { setTest({ ok: false, error: err(e) }); }
    finally { setBusy(false); }
  }
  async function remove() {
    setBusy(true);
    try { await deleteSnowflake(); setMsg({ ok: true, text: "Removed." }); setTest(null); load(); onSaved(); }
    catch (e) { setMsg({ ok: false, text: err(e) }); }
    finally { setBusy(false); }
  }

  return (
    <section className="set-card">
      <div className="set-head">
        <h3>Snowflake</h3>
        {cur?.configured && <span className="set-status on">configured</span>}
      </div>
      {cur?.privileged_role && (
        <div className="set-warn">⚠ {cur.role} is an administrative role. Prefer a dedicated
          SELECT-only role (scripts/snowflake_setup.sql).</div>
      )}
      <div className="set-grid">
        <Field label="Account"><input value={f.account} placeholder="abc12345.us-east-1"
          onChange={(e) => set("account", e.target.value)} /></Field>
        <Field label="User"><input value={f.user} onChange={(e) => set("user", e.target.value)} /></Field>
        <Field label="Auth method"><select value={f.authenticator}
          onChange={(e) => set("authenticator", e.target.value)}>
          {AUTH_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select></Field>
        <Field label="Role"><input value={f.role} placeholder="PLUMB_QC"
          onChange={(e) => set("role", e.target.value)} /></Field>
        <Field label="Warehouse"><input value={f.warehouse} placeholder="PLUMB_WH"
          onChange={(e) => set("warehouse", e.target.value)} /></Field>
        {f.authenticator === "snowflake_jwt" && (
          <>
            <Field label="Private key path" wide><input value={f.private_key_path}
              placeholder="~/.plumb/keys/plumb_rsa_key.p8"
              onChange={(e) => set("private_key_path", e.target.value)} /></Field>
            <Field label={cur?.has_passphrase ? "Key passphrase (set; blank keeps it)" : "Key passphrase (optional)"}>
              <input type="password" value={f.passphrase} placeholder={cur?.has_passphrase ? "••••••••" : ""}
                onChange={(e) => set("passphrase", e.target.value)} /></Field>
          </>
        )}
        {f.authenticator === "oauth" && (
          <Field label={cur?.has_oauth_token ? "OAuth token (set; blank keeps it)" : "OAuth token"} wide>
            <input type="password" value={f.oauth_token} placeholder={cur?.has_oauth_token ? "••••••••" : ""}
              onChange={(e) => set("oauth_token", e.target.value)} /></Field>
        )}
      </div>
      <div className="set-actions">
        <button className="run small" onClick={save} disabled={busy}>Save</button>
        <button className="linkbtn" onClick={runTest} disabled={busy || !cur?.configured}>Test connection</button>
        {cur?.configured && <button className="linkbtn danger" onClick={remove} disabled={busy}>Remove</button>}
      </div>
      {msg && <div className={`set-msg ${msg.ok ? "ok" : "err"}`}>{msg.text}</div>}
      {test && (
        <div className={`set-msg ${test.ok ? "ok" : "err"}`}>
          {test.ok ? `Connected as ${test.role} on ${test.warehouse}.` : `Failed: ${test.error}`}
        </div>
      )}
    </section>
  );
}

function TableauForm({ open }: { open: boolean }) {
  const [cur, setCur] = useState<TableauSettings | null>(null);
  const [f, setF] = useState({ server: "", site: "", pat_name: "", secret: "" });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [test, setTest] = useState<TestResult | null>(null);

  function load() {
    fetchTableauSettings().then((d) => {
      setCur(d);
      if (d.configured) setF((p) => ({ ...p, server: d.server ?? "", site: d.site ?? "", pat_name: d.pat_name ?? "", secret: "" }));
    }).catch(() => undefined);
  }
  useEffect(() => { if (open) load(); }, [open]);
  const set = (k: string, v: string) => setF((p) => ({ ...p, [k]: v }));

  async function save() {
    setBusy(true); setMsg(null); setTest(null);
    try {
      const body: Record<string, unknown> = { server: f.server, site: f.site, auth: "pat", pat_name: f.pat_name };
      if (f.secret) body.secret = f.secret;
      await saveTableauSettings(body);
      setMsg({ ok: true, text: "Saved. Token stored in your keychain." });
      load();
    } catch (e) { setMsg({ ok: false, text: err(e) }); }
    finally { setBusy(false); }
  }
  async function runTest() {
    setBusy(true); setTest(null);
    try { setTest(await testTableau()); }
    catch (e) { setTest({ ok: false, error: err(e) }); }
    finally { setBusy(false); }
  }
  async function remove() {
    setBusy(true);
    try { await deleteTableau(); setMsg({ ok: true, text: "Removed." }); setTest(null); load(); }
    catch (e) { setMsg({ ok: false, text: err(e) }); }
    finally { setBusy(false); }
  }

  return (
    <section className="set-card">
      <div className="set-head">
        <h3>Tableau Server / Cloud</h3>
        {cur?.configured && <span className="set-status on">configured</span>}
      </div>
      <p className="set-sub">For live pulls and the CLI. Web checks also accept a .twb/.twbx upload
        with no connection. Auth is a Personal Access Token.</p>
      <div className="set-grid">
        <Field label="Server URL" wide><input value={f.server} placeholder="https://10ax.online.tableau.com"
          onChange={(e) => set("server", e.target.value)} /></Field>
        <Field label="Site (content URL)"><input value={f.site} placeholder="leave blank for default"
          onChange={(e) => set("site", e.target.value)} /></Field>
        <Field label="Token name"><input value={f.pat_name} placeholder="plumb-qc"
          onChange={(e) => set("pat_name", e.target.value)} /></Field>
        <Field label={cur?.has_secret ? "Token secret (set; blank keeps it)" : "Token secret"} wide>
          <input type="password" value={f.secret} placeholder={cur?.has_secret ? "••••••••" : ""}
            onChange={(e) => set("secret", e.target.value)} /></Field>
      </div>
      <div className="set-actions">
        <button className="run small" onClick={save} disabled={busy}>Save</button>
        <button className="linkbtn" onClick={runTest} disabled={busy || !cur?.configured}>Test connection</button>
        {cur?.configured && <button className="linkbtn danger" onClick={remove} disabled={busy}>Remove</button>}
      </div>
      {msg && <div className={`set-msg ${msg.ok ? "ok" : "err"}`}>{msg.text}</div>}
      {test && (
        <div className={`set-msg ${test.ok ? "ok" : "err"}`}>
          {test.ok ? `Signed in to site "${test.site}".` : `Failed: ${test.error}`}
        </div>
      )}
    </section>
  );
}
