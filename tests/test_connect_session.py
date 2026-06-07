"""Tests for session guardrails: query tag, timeout, warehouse, role,
row cap, auth path assembly, and that no secret or password can appear."""

from pathlib import Path
from typing import Any

import pytest

import plumb.connect.snowflake as snow
from plumb.config.models import ConnectionProfile
from plumb.connect.snowflake import (
    AuthConfigError,
    ReadOnlyViolation,
    SnowflakeConnectError,
    SnowflakeSession,
    build_connect_kwargs,
    build_query_tag,
)


def make_profile(authenticator: str = "externalbrowser", **overrides: Any) -> ConnectionProfile:
    data: dict[str, Any] = {
        "account": "myorg-account",
        "user": "VIKAS",
        "authenticator": authenticator,
        "role": "PLUMB_QC_ROLE",
        "warehouse": "PLUMB_WH",
    }
    data.update(overrides)
    return ConnectionProfile.model_validate(data)


class FakeCursor:
    def __init__(self, rows: list[tuple], columns: list[str]) -> None:
        self._rows = rows
        self.description = [(c, None, None, None, None, None, None) for c in columns]
        self.executed: list[tuple[str, Any]] = []
        self.closed = False
        self.sfqid = "fake-query-id"

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))

    def fetchmany(self, n: int) -> list[tuple]:
        return self._rows[:n]

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


def make_session(
    rows: list[tuple] | None = None,
    columns: list[str] | None = None,
    max_result_rows: int = 100,
) -> tuple[SnowflakeSession, FakeCursor, dict]:
    cursor = FakeCursor(rows or [], columns or [])
    connection = FakeConnection(cursor)
    captured_kwargs: dict = {}

    def factory(**kwargs: Any) -> FakeConnection:
        captured_kwargs.update(kwargs)
        return connection

    session = SnowflakeSession(
        make_profile(),
        run_id="run-123",
        statement_timeout_s=120,
        max_result_rows=max_result_rows,
        connection_factory=factory,
    )
    return session, cursor, captured_kwargs


class TestConnectKwargs:
    def test_every_session_carries_tag_timeout_warehouse_role(self) -> None:
        kwargs = build_connect_kwargs(
            make_profile(), run_id="abc-123", statement_timeout_s=99
        )
        assert kwargs["session_parameters"]["QUERY_TAG"] == "plumb_qc:abc-123"
        assert kwargs["session_parameters"]["STATEMENT_TIMEOUT_IN_SECONDS"] == 99
        assert kwargs["warehouse"] == "PLUMB_WH"
        assert kwargs["role"] == "PLUMB_QC_ROLE"

    def test_no_password_key_ever_present(self) -> None:
        kwargs = build_connect_kwargs(
            make_profile(), run_id="abc", statement_timeout_s=10
        )
        assert not any("password" in key.lower() for key in kwargs)

    def test_key_pair_auth(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        key_file = tmp_path / "plumb_rsa_key.p8"
        key_file.write_text("not a real key", encoding="utf-8")
        monkeypatch.delenv(snow.ENV_PRIVATE_KEY_PASSPHRASE, raising=False)
        monkeypatch.setattr(snow, "_keyring_secret", lambda entry: None)
        kwargs = build_connect_kwargs(
            make_profile("snowflake_jwt", private_key_path=str(key_file)),
            run_id="abc",
            statement_timeout_s=10,
        )
        assert kwargs["authenticator"] == "SNOWFLAKE_JWT"
        assert kwargs["private_key_file"] == str(key_file)
        assert "private_key_file_pwd" not in kwargs

    def test_key_pair_passphrase_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        key_file = tmp_path / "k.p8"
        key_file.write_text("x", encoding="utf-8")
        monkeypatch.setenv(snow.ENV_PRIVATE_KEY_PASSPHRASE, "shhh")
        kwargs = build_connect_kwargs(
            make_profile("snowflake_jwt", private_key_path=str(key_file)),
            run_id="abc",
            statement_timeout_s=10,
        )
        assert kwargs["private_key_file_pwd"] == "shhh"

    def test_key_pair_missing_key_file_is_clear_error(self) -> None:
        with pytest.raises(AuthConfigError, match="not found"):
            build_connect_kwargs(
                make_profile("snowflake_jwt", private_key_path="C:/nope/missing.p8"),
                run_id="abc",
                statement_timeout_s=10,
            )

    def test_externalbrowser_auth(self) -> None:
        kwargs = build_connect_kwargs(
            make_profile("externalbrowser"), run_id="abc", statement_timeout_s=10
        )
        assert kwargs["authenticator"] == "externalbrowser"
        assert "token" not in kwargs

    def test_oauth_token_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(snow.ENV_OAUTH_TOKEN, "tok-123")
        kwargs = build_connect_kwargs(
            make_profile("oauth"), run_id="abc", statement_timeout_s=10
        )
        assert kwargs["authenticator"] == "oauth"
        assert kwargs["token"] == "tok-123"

    def test_oauth_missing_token_is_clear_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(snow.ENV_OAUTH_TOKEN, raising=False)
        monkeypatch.setattr(snow, "_keyring_secret", lambda entry: None)
        with pytest.raises(AuthConfigError, match="no OAuth token found"):
            build_connect_kwargs(
                make_profile("oauth"), run_id="abc", statement_timeout_s=10
            )


class TestSessionExecute:
    def test_select_returns_named_rows(self) -> None:
        session, cursor, _ = make_session(
            rows=[(1, "a"), (2, "b")], columns=["ID", "NAME"]
        )
        with session:
            result = session.execute("SELECT id, name FROM t")
        assert result.rows == [{"ID": 1, "NAME": "a"}, {"ID": 2, "NAME": "b"}]
        assert result.truncated is False
        assert result.query_id == "fake-query-id"
        assert cursor.closed is True

    def test_row_cap_truncates_and_flags(self) -> None:
        session, _, _ = make_session(
            rows=[(i,) for i in range(10)], columns=["ID"], max_result_rows=3
        )
        with session:
            result = session.execute("SELECT id FROM t")
        assert len(result.rows) == 3
        assert result.truncated is True

    def test_non_read_refused_before_any_cursor_activity(self) -> None:
        session, cursor, _ = make_session()
        with session:
            with pytest.raises(ReadOnlyViolation):
                session.execute("DROP TABLE t")
        assert cursor.executed == []

    def test_execute_before_open_raises(self) -> None:
        session, _, _ = make_session()
        with pytest.raises(SnowflakeConnectError, match="not open"):
            session.execute("SELECT 1")

    def test_session_carries_query_tag_to_connection(self) -> None:
        session, _, captured = make_session()
        with session:
            pass
        assert captured["session_parameters"]["QUERY_TAG"] == build_query_tag("run-123")
        assert captured["session_parameters"]["STATEMENT_TIMEOUT_IN_SECONDS"] == 120
        assert captured["warehouse"] == "PLUMB_WH"

    def test_close_closes_connection(self) -> None:
        session, _, _ = make_session()
        session.open()
        connection = session._conn
        session.close()
        assert connection.closed is True
        assert session._conn is None

    def test_connect_failure_wrapped_clearly(self) -> None:
        def exploding_factory(**kwargs: Any) -> Any:
            raise RuntimeError("network unreachable")

        session = SnowflakeSession(
            make_profile(),
            run_id="r",
            connection_factory=exploding_factory,
        )
        with pytest.raises(SnowflakeConnectError, match="could not connect"):
            session.open()
