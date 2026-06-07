"""Read-only Snowflake access with mandatory guardrails.

Every statement Plumb issues goes through SnowflakeSession.execute, which:
- refuses anything that is not a read (assert_read_only, fail closed),
- carries QUERY_TAG = 'plumb_qc:{run_id}',
- runs under the session STATEMENT_TIMEOUT_IN_SECONDS,
- caps fetched rows at the ruleset's max_result_rows.

Auth is key-pair (snowflake_jwt), externalbrowser SSO, or OAuth. Secrets
come from the OS keychain (keyring) or environment variables, never from
config files or source.

Read-only policy (ADR-0003): a statement is allowed only if it is a single
SELECT-rooted read (including WITH and set operations) or an EXPLAIN of
one. Everything else is refused, including SHOW and DESCRIBE (metadata
checks read INFORMATION_SCHEMA via SELECT), and including anything sqlglot
cannot parse, because an unparseable statement cannot be proven safe.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import keyring
import keyring.errors
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from plumb.config.models import ConnectionProfile

QUERY_TAG_PREFIX = "plumb_qc"

KEYRING_SERVICE = "plumb"
ENV_PRIVATE_KEY_PASSPHRASE = "PLUMB_PRIVATE_KEY_PASSPHRASE"
ENV_OAUTH_TOKEN = "PLUMB_OAUTH_TOKEN"


class ReadOnlyViolation(Exception):
    """A statement that is not provably a read was refused."""


class AuthConfigError(Exception):
    """Auth material is missing or unusable. Never contains a secret."""


class SnowflakeConnectError(Exception):
    """Session lifecycle misuse or connection failure."""


_ALLOWED_ROOTS: tuple[type[exp.Expression], ...] = (
    exp.Select,
    exp.SetOperation,
    exp.Subquery,
)

# Belt and braces under the root allowlist: refuse these anywhere in the
# tree. Command also catches sqlglot's fallback for unsupported syntax.
_FORBIDDEN_NODE_NAMES = (
    "Insert",
    "Update",
    "Delete",
    "Merge",
    "Create",
    "Drop",
    "Alter",
    "Command",
    "TruncateTable",
    "Copy",
    "Grant",
    "Use",
    "Set",
    "Transaction",
    "Commit",
    "Rollback",
    "LoadData",
)
_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    node_type
    for node_type in (getattr(exp, name, None) for name in _FORBIDDEN_NODE_NAMES)
    if node_type is not None
)

_EXPLAIN_PREFIX = re.compile(
    r"^\s*EXPLAIN(\s+USING\s+(TABULAR|JSON|TEXT))?\s+", re.IGNORECASE
)


def assert_read_only(sql: str) -> None:
    """Raise ReadOnlyViolation unless sql is a single read statement.

    Fail closed: empty, comment-only, multi-statement, unparseable, and
    anything not rooted in a SELECT (or EXPLAIN of one) is refused.
    """
    if not sql or not sql.strip():
        raise ReadOnlyViolation("refusing empty SQL")

    text = sql
    explain_match = _EXPLAIN_PREFIX.match(text)
    if explain_match:
        text = text[explain_match.end():]

    try:
        parsed = sqlglot.parse(text, read="snowflake")
    except ParseError as exc:
        raise ReadOnlyViolation(
            f"refusing SQL that could not be parsed as a read: {exc}"
        ) from exc

    statements = [s for s in parsed if s is not None]
    if not statements:
        raise ReadOnlyViolation("refusing empty or comment-only SQL")
    if len(statements) > 1:
        raise ReadOnlyViolation(
            "multi-statement SQL is not allowed; execute one read per call"
        )

    statement = statements[0]
    if not isinstance(statement, _ALLOWED_ROOTS):
        raise ReadOnlyViolation(
            f"refusing non-read statement ({type(statement).__name__}); "
            "only a single SELECT read, or EXPLAIN of one, is allowed"
        )
    for node in statement.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise ReadOnlyViolation(
                f"refusing statement containing a non-read operation "
                f"({type(node).__name__})"
            )


def build_query_tag(run_id: str) -> str:
    return f"{QUERY_TAG_PREFIX}:{run_id}"


def _keyring_secret(entry: str) -> str | None:
    try:
        return keyring.get_password(KEYRING_SERVICE, entry)
    except keyring.errors.KeyringError:
        return None


def build_connect_kwargs(
    profile: ConnectionProfile,
    *,
    run_id: str,
    statement_timeout_s: int,
) -> dict[str, Any]:
    """Pure assembly of connector kwargs. Query tag, timeout, dedicated
    warehouse, and role are set here so no session can exist without them.
    Never includes a password."""
    kwargs: dict[str, Any] = {
        "account": profile.account,
        "user": profile.user,
        "role": profile.role,
        "warehouse": profile.warehouse,
        "session_parameters": {
            "QUERY_TAG": build_query_tag(run_id),
            "STATEMENT_TIMEOUT_IN_SECONDS": statement_timeout_s,
        },
    }

    if profile.authenticator == "snowflake_jwt":
        if not profile.private_key_path:
            raise AuthConfigError("snowflake_jwt requires private_key_path")
        key_path = Path(profile.private_key_path).expanduser()
        if not key_path.exists():
            raise AuthConfigError(f"private key file not found: {key_path}")
        kwargs["authenticator"] = "SNOWFLAKE_JWT"
        kwargs["private_key_file"] = str(key_path)
        passphrase = os.environ.get(ENV_PRIVATE_KEY_PASSPHRASE) or _keyring_secret(
            f"private_key_passphrase:{profile.account}:{profile.user}"
        )
        if passphrase:
            kwargs["private_key_file_pwd"] = passphrase
    elif profile.authenticator == "externalbrowser":
        kwargs["authenticator"] = "externalbrowser"
    elif profile.authenticator == "oauth":
        token = os.environ.get(ENV_OAUTH_TOKEN) or _keyring_secret(
            f"oauth_token:{profile.account}:{profile.user}"
        )
        if not token:
            raise AuthConfigError(
                f"no OAuth token found; set {ENV_OAUTH_TOKEN} or store one in "
                f"the OS keychain under service {KEYRING_SERVICE!r}"
            )
        kwargs["authenticator"] = "oauth"
        kwargs["token"] = token
    return kwargs


@dataclass
class QueryResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False
    query_id: str | None = None


def _default_connection_factory(**kwargs: Any) -> Any:
    # Imported lazily so the guard and kwargs assembly stay testable and
    # importable without the driver loaded.
    import snowflake.connector

    return snowflake.connector.connect(**kwargs)


class SnowflakeSession:
    """The only path to Snowflake. Owns the guardrails end to end."""

    def __init__(
        self,
        profile: ConnectionProfile,
        *,
        run_id: str,
        statement_timeout_s: int = 120,
        max_result_rows: int = 100_000,
        connection_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.profile = profile
        self.run_id = run_id
        self.query_tag = build_query_tag(run_id)
        self.statement_timeout_s = statement_timeout_s
        self.max_result_rows = max_result_rows
        self._factory = connection_factory or _default_connection_factory
        self._conn: Any | None = None

    def open(self) -> "SnowflakeSession":
        if self._conn is not None:
            return self
        kwargs = build_connect_kwargs(
            self.profile,
            run_id=self.run_id,
            statement_timeout_s=self.statement_timeout_s,
        )
        try:
            self._conn = self._factory(**kwargs)
        except (AuthConfigError, ReadOnlyViolation):
            raise
        except Exception as exc:
            raise SnowflakeConnectError(f"could not connect to Snowflake: {exc}") from exc
        return self

    def execute(self, sql: str, params: Any = None) -> QueryResult:
        """Run one read. The guard runs before any cursor is created."""
        assert_read_only(sql)
        if self._conn is None:
            raise SnowflakeConnectError("session is not open; call open() first")
        cursor = self._conn.cursor()
        try:
            cursor.execute(sql, params)
            raw_rows = cursor.fetchmany(self.max_result_rows + 1)
            truncated = len(raw_rows) > self.max_result_rows
            if truncated:
                raw_rows = raw_rows[: self.max_result_rows]
            columns = [col[0] for col in (cursor.description or [])]
            rows = [dict(zip(columns, row, strict=True)) for row in raw_rows]
            query_id = getattr(cursor, "sfqid", None)
            return QueryResult(rows=rows, truncated=truncated, query_id=query_id)
        finally:
            cursor.close()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "SnowflakeSession":
        return self.open()

    def __exit__(self, *exc_info: Any) -> None:
        self.close()
