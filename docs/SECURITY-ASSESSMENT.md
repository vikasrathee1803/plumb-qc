# Plumb security assessment (financial services readiness)

Date: 2026-06-08. Scope: the Plumb codebase at this revision (engine, web API,
CLI, AI assist, portable build). Audience: enterprise infosec and compliance.

This is a grounded review: every finding cites the code it is based on. It
covers data flow, secrets, access control, auditability, supply chain, and
data at rest, and maps findings to SOX, PCI-DSS, SOC 2, and GDPR/CCPA concerns.

## 1. Overall posture

Plumb is local-first and read-only by design, and the architecture makes the
strong guarantees structural rather than aspirational. The recent move of AI
assist in-database (Snowflake Cortex) removes the only third-party data
egress. The main gaps for a regulated deployment are operational: complete and
centralized audit, least-privilege Snowflake role, content-based PII handling,
and supply-chain assurance for the portable build. None are architectural
blockers; all are addressable.

Recommendation in one line: deploy Plumb under a dedicated least-privilege,
read-only Snowflake role and a dedicated warehouse (not ACCOUNTADMIN), and
close the audit and supply-chain items below before production use.

## 2. Strengths (controls that already hold)

- Read-only by construction. Every statement goes through one path
  (`SnowflakeSession.execute`) that calls `assert_read_only` first and fails
  closed: only a single SELECT-rooted read (or EXPLAIN of one) is allowed; an
  allowlist of root types plus a denylist of write/DDL/DML nodes, and any
  unparseable statement, are refused. See `plumb/connect/snowflake.py:93-133`.
- No password authentication. Key-pair (JWT), externalbrowser SSO, or OAuth
  only. `ConnectionProfile` rejects any field whose name contains "password"
  (`plumb/config/models.py:186-197`); secrets come from the OS keychain or
  environment, never config or source (`plumb/connect/snowflake.py:140-193`).
- No secrets in source or config. The connection file holds only account,
  user, role, warehouse, and a private-key file path; key material and
  passphrases live in keyring/env. `.p8/.pem/.key` and `.plumb/` are
  gitignored.
- Snowflake-side auditability and cost control. Every query carries
  `QUERY_TAG = plumb_qc:{run_id}` on a dedicated warehouse, under a statement
  timeout, with a fetched-row cap (`snowflake.py:136-165, 247-264`). Snowflake
  QUERY_HISTORY/ACCESS_HISTORY therefore records every access, attributable to
  a run id.
- PII redaction on by default. Evidence samples are capped (20 rows) and
  redacted before storage or rendering; an aggregate-only mode produces no row
  samples at all (`plumb/config/models.py:25-37`, `plumb/engine/evidence.py`).
- No third-party data egress for AI. Assist runs in-database via Snowflake
  Cortex (`plumb/ai/client.py`); the model call is a single SELECT, so no data
  leaves Snowflake, and AI never sets a status, severity, or verdict.
- Report output is XSS-safe. The HTML report uses Jinja autoescaping with no
  `|safe` filters, so evidence values cannot inject script
  (`plumb/report/html.py:20-23`, `report.html.j2`).
- Input hardening. Report ids are validated against a uuid pattern before any
  filesystem access (path-traversal guard); SQL inputs and workbook uploads are
  size-capped (`web/api/app.py`).
- Fail-loud configuration. All config models forbid unknown fields (pydantic
  `extra="forbid"`), so a malformed or tampered ruleset is rejected, not
  silently misread.

## 3. Findings

Severity uses Critical / High / Medium / Low.

### H-1 (High): No authentication or authorization on the local web API

The web app binds `127.0.0.1` by default (`plumb/cli.py:181`), which is correct,
but there is no auth on any endpoint, and loopback is shared across all sessions
on a multi-user Windows host (Citrix/RDP, common in finance). Another logged-in
user on the same host could reach `127.0.0.1:8777` and issue reads using the
running user's Snowflake session configuration. The `--host` option also lets a
user bind `0.0.0.0` with no auth, exposing it to the network.

Recommendation: keep loopback the default and refuse (or loudly warn on)
non-loopback binds; require a per-launch bearer token (generated at startup,
printed to the console, sent by the SPA) so only the launching user can drive
the API; document Plumb as a single-user tool.

### H-2 (High): Web runs are not recorded in the local audit trail

`write_audit_record` is called only by the CLI; the web surface (the primary UI)
does not write an audit record. The local who/when/what trail is therefore
incomplete for the main access path. Mitigant: Snowflake ACCESS_HISTORY carries
the `plumb_qc:{run_id}` tag for live runs, so the system-of-record for data
access still exists Snowflake-side; static-only runs touch no data.

Recommendation: write an audit record on every web run, and forward the audit
trail to the enterprise SIEM/central sink (see M-2).

### M-1 (Medium): PII redaction is column-name based, not content based

Redaction matches column names against patterns (email, phone, ssn, address,
name, dob, passport, license, ip) in `plumb/config/models.py:68-77` and
`plumb/engine/evidence.py`. A column named `memo`, `comments`, `account_no`, or
a card-number column that does not match a pattern is not redacted, and there is
no PAN/IBAN/account-number content detector.

Recommendation: add content-based detectors (Luhn/PAN, IBAN, national-id
formats) and let infosec set the policy centrally; default finance profiles to
`aggregate_only` so no row samples are persisted unless explicitly enabled.

### M-2 (Medium): Audit log is local and not tamper-evident

`~/.plumb/audit.jsonl` is local and user-writable, so it can be edited or
deleted (`plumb/engine/audit.py`). For SOX-style audit integrity this is
insufficient on its own.

Recommendation: forward audit events to an append-only central sink (SIEM, or
object storage with object-lock/WORM); treat Snowflake ACCESS_HISTORY as the
authoritative data-access record.

### M-3 (Medium): Supply-chain assurance for the portable build

Dependency versions are pinned in `pyproject.toml` (good), but not hash-pinned,
there is no SBOM, and the portable zip freezes roughly sixty third-party
packages with no in-place update mechanism (`scripts/build_portable.py`).

Recommendation: install with `pip --require-hashes`; generate an SBOM
(CycloneDX); add a dependency CVE scan (e.g. pip-audit) to CI; document a
rebuild/patch cadence for the portable bundle and a way to verify its integrity
(signed zip / published hash).

### M-4 (Medium): Data at rest is not encrypted by the application

Reports, run history, and connection metadata persist under `~/.plumb`
unencrypted (`plumb/config/loader.py:27-30`, `web/api/app.py` report store).
Reports can contain redacted evidence rows. The control today is OS disk
encryption.

Recommendation: require full-disk encryption (BitLocker/FileVault) as a
documented deployment control; restrict `~/.plumb` file permissions to the
user; offer `aggregate_only` to avoid persisting any row samples; define a
retention/cleanup policy for `~/.plumb/reports`.

### L-1 (Low): API error messages echo exception detail

Connection and parse failures return the underlying exception text in the HTTP
response (for example `_open_session`, `/api/lineage`). On a single-user
loopback API this is low risk, but combined with H-1 on a shared host it is
information disclosure.

Recommendation: return generic messages to the client and keep detail in local
logs.

### L-2 (Low): The read-only guarantee should be enforced by RBAC, not only the app

The AST guard is strong and fail-closed, but the authoritative control in a
regulated environment is a least-privilege Snowflake role. The demo currently
connects as ACCOUNTADMIN, which has full write/DDL privileges.

Recommendation (highest-value control): provision a dedicated `PLUMB_QC` role
with SELECT-only grants on the in-scope schemas plus USAGE on a dedicated
`PLUMB_WH`, and never run Plumb with an administrative role. This makes a write
impossible at the database, independent of the app.

### L-3 (Low): No rate limiting on the web API

Beyond input size caps there is no request rate limit. Low risk for a local,
single-user tool; Snowflake statement timeout and row cap bound query cost.

## 4. Compliance mapping

- SOX (integrity of financial reporting data): read-only guarantee,
  deterministic verdicts, and Snowflake ACCESS_HISTORY are strong. Gaps:
  audit completeness and tamper-evidence (H-2, M-2).
- PCI-DSS (if cardholder data is in scope): content-based PAN handling (M-1),
  data-at-rest (M-4), and least-privilege role (L-2).
- SOC 2 (security, confidentiality, availability): access control on the API
  (H-1), centralized logging (H-2, M-2), and vulnerability management / SBOM
  (M-3).
- GDPR / CCPA (personal data): redaction-on-by-default is good; gaps are
  content-based detection (M-1) and retention for persisted reports (M-4).
- Third-party processors / data residency: with Cortex, model inference runs
  inside Snowflake, so there is no external LLM sub-processor and no data
  egress. This is a strong position; record it in the data-flow inventory.

## 5. Prioritized remediation before production

1. Provision and require a dedicated least-privilege, read-only Snowflake role
   and warehouse; forbid administrative roles (L-2). Highest value, lowest
   effort, database-enforced.
2. Add a startup bearer token and refuse non-loopback binds without it (H-1).
3. Write an audit record on every web run and forward audit to the central
   sink/SIEM (H-2, M-2).
4. Add content-based PII detection and a finance default of aggregate-only
   (M-1); document data-at-rest and retention (M-4).
5. Hash-pin dependencies, generate an SBOM, add a CVE scan, and define a
   portable patch/verify process (M-3).
6. Generic external error messages (L-1).

Items 1 to 4 are the set most likely to be raised by an infosec/compliance
review; none require architectural change.
