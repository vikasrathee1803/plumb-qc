"""Tests for config loading, validation, profile resolution, and pinning.

The invariant under test: a malformed ruleset fails loudly with a clear
message and never produces a partially valid object.
"""

from pathlib import Path

import pytest

from plumb.config.loader import (
    ConfigError,
    load_connection_profile,
    load_profile,
    load_ruleset,
    read_pin,
    resolve_profile,
    write_pin,
)
from plumb.engine.models import Severity

FIXTURES = Path(__file__).parent / "fixtures" / "rulesets"


class TestLoadRuleset:
    def test_valid_ruleset_loads(self) -> None:
        ruleset = load_ruleset(FIXTURES / "valid.yml", enforce_pin=False)
        assert ruleset.version == "2026.06.0"
        assert ruleset.defaults.fail_on == "REVIEW"
        assert ruleset.defaults.max_result_rows == 100_000
        assert ruleset.defaults.statement_timeout_s == 120
        assert ruleset.severity_overrides["S-STAT-001"] is Severity.HIGH
        assert len(ruleset.checks) == 3
        assert ruleset.checks[0].params["key"] == ["order_id"]

    def test_bad_severity_fails_loudly(self) -> None:
        with pytest.raises(ConfigError) as excinfo:
            load_ruleset(FIXTURES / "malformed_bad_severity.yml", enforce_pin=False)
        message = str(excinfo.value)
        assert "severity_overrides" in message
        assert "invalid configuration" in message

    def test_unknown_field_fails_loudly(self) -> None:
        with pytest.raises(ConfigError) as excinfo:
            load_ruleset(FIXTURES / "malformed_unknown_field.yml", enforce_pin=False)
        assert "defaultz" in str(excinfo.value)

    def test_broken_yaml_fails_loudly(self) -> None:
        with pytest.raises(ConfigError, match="could not parse YAML"):
            load_ruleset(FIXTURES / "malformed_not_yaml.yml", enforce_pin=False)

    def test_missing_file_fails_loudly(self) -> None:
        with pytest.raises(ConfigError, match="not found"):
            load_ruleset(FIXTURES / "does_not_exist.yml", enforce_pin=False)

    def test_empty_file_fails_loudly(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.yml"
        empty.write_text("", encoding="utf-8")
        with pytest.raises(ConfigError, match="empty"):
            load_ruleset(empty, enforce_pin=False)

    def test_duplicate_check_ids_fail(self, tmp_path: Path) -> None:
        dup = tmp_path / "dup.yml"
        dup.write_text(
            'version: "1"\nchecks:\n  - id: D-GRAIN-001\n  - id: D-GRAIN-001\n',
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="duplicate check id"):
            load_ruleset(dup, enforce_pin=False)

    def test_invalid_naming_regex_fails(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad_regex.yml"
        bad.write_text(
            'version: "1"\nnaming:\n  table_regex: "(unclosed"\n',
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="regular expression"):
            load_ruleset(bad, enforce_pin=False)


class TestPinning:
    def test_pin_roundtrip(self, tmp_path: Path) -> None:
        pin = tmp_path / "rules.pin"
        assert read_pin(pin) is None
        write_pin("2026.06.0", pin)
        assert read_pin(pin) == "2026.06.0"

    def test_pin_mismatch_fails(self, tmp_path: Path) -> None:
        pin = tmp_path / "rules.pin"
        write_pin("2025.01.9", pin)
        with pytest.raises(ConfigError, match="does not match the pinned version"):
            load_ruleset(FIXTURES / "valid.yml", pin_file=pin)

    def test_pin_match_loads(self, tmp_path: Path) -> None:
        pin = tmp_path / "rules.pin"
        write_pin("2026.06.0", pin)
        ruleset = load_ruleset(FIXTURES / "valid.yml", pin_file=pin)
        assert ruleset.version == "2026.06.0"

    def test_invalid_pin_version_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="invalid ruleset version"):
            write_pin("has space", tmp_path / "rules.pin")


class TestProfileResolution:
    def _resolved(self):
        base = load_ruleset(FIXTURES / "valid.yml", enforce_pin=False)
        profile = load_profile(FIXTURES / "profile_finance.yml")
        return base, resolve_profile(base, profile)

    def test_profile_name_defaults_to_file_stem(self) -> None:
        profile = load_profile(FIXTURES / "profile_finance.yml")
        assert profile.name == "finance"

    def test_defaults_merge_per_field(self) -> None:
        base, resolved = self._resolved()
        assert resolved.defaults.fail_on == "READY_WITH_NOTES"
        assert resolved.defaults.aggregate_only is True
        assert resolved.defaults.evidence_sample_rows == 0
        # untouched base values survive
        assert resolved.defaults.statement_timeout_s == 120
        assert resolved.defaults.max_result_rows == 100_000

    def test_lists_extend_without_duplicates(self) -> None:
        base, resolved = self._resolved()
        assert resolved.certified_sources == [
            "ANALYTICS.MART.FCT_SALES",
            "ANALYTICS.MART.FCT_GL",
        ]
        assert resolved.deprecated_objects == ["ANALYTICS.LEGACY.V_OLD_SALES"]

    def test_severity_and_threshold_overrides_merge(self) -> None:
        base, resolved = self._resolved()
        assert resolved.severity_overrides["S-STAT-001"] is Severity.HIGH
        assert resolved.severity_overrides["D-NULL-002"] is Severity.HIGH
        assert resolved.thresholds.null_rate_default == 0.001

    def test_checks_merge_by_id(self) -> None:
        base, resolved = self._resolved()
        by_id = {spec.id: spec for spec in resolved.checks}
        # profile replaces the grain key wholesale
        assert by_id["D-GRAIN-001"].params["key"] == ["journal_id", "line_id"]
        # profile disables freshness
        assert by_id["D-FRESH-001"].enabled is False
        # base-only check survives untouched
        assert by_id["D-RECON-001"].params["tolerance_pct"] == 0.001

    def test_resolved_profile_revalidates(self, tmp_path: Path) -> None:
        base = load_ruleset(FIXTURES / "valid.yml", enforce_pin=False)
        bad_profile = tmp_path / "bad.yml"
        bad_profile.write_text(
            "name: bad\ndefaults:\n  statement_timeout_s: -5\n",
            encoding="utf-8",
        )
        profile = load_profile(bad_profile)
        with pytest.raises(ConfigError, match="statement_timeout_s"):
            resolve_profile(base, profile)


class TestConnectionProfile:
    def _write(self, tmp_path: Path, content: str) -> Path:
        path = tmp_path / "connection.yml"
        path.write_text(content, encoding="utf-8")
        return path

    def test_key_pair_profile_loads(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            'account: "myorg-account"\nuser: "VIKAS"\n'
            'authenticator: "snowflake_jwt"\n'
            'private_key_path: "~/.plumb/keys/plumb_rsa_key.p8"\n'
            'role: "PLUMB_QC_ROLE"\nwarehouse: "PLUMB_WH"\n',
        )
        profile = load_connection_profile(path)
        assert profile.authenticator == "snowflake_jwt"
        assert profile.warehouse == "PLUMB_WH"

    def test_password_is_refused_by_name(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            'account: "a"\nuser: "u"\nauthenticator: "externalbrowser"\n'
            'password: "hunter2"\nrole: "r"\nwarehouse: "w"\n',
        )
        with pytest.raises(ConfigError, match="password auth is not supported"):
            load_connection_profile(path)

    def test_jwt_without_key_path_fails(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            'account: "a"\nuser: "u"\nauthenticator: "snowflake_jwt"\n'
            'role: "r"\nwarehouse: "w"\n',
        )
        with pytest.raises(ConfigError, match="private_key_path"):
            load_connection_profile(path)

    def test_unknown_authenticator_fails(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            'account: "a"\nuser: "u"\nauthenticator: "username_password"\n'
            'role: "r"\nwarehouse: "w"\n',
        )
        with pytest.raises(ConfigError, match="authenticator"):
            load_connection_profile(path)
