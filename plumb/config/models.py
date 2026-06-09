"""Pydantic v2 models for everything Plumb reads from YAML.

All models forbid unknown fields so a typo in a ruleset fails loudly
instead of being silently ignored. The ConnectionProfile additionally
refuses any password field by design: auth is key-pair, externalbrowser
SSO, or OAuth only.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from plumb.engine.models import Severity


class Defaults(BaseModel):
    """Run guardrails. statement_timeout_s and max_result_rows are applied
    to every session; they are cost controls, not suggestions."""

    model_config = ConfigDict(extra="forbid")

    fail_on: Literal["READY_WITH_NOTES", "REVIEW", "BLOCKED"] = "REVIEW"
    max_result_rows: int = Field(default=100_000, gt=0)
    statement_timeout_s: int = Field(default=120, gt=0)
    evidence_sample_rows: int = Field(default=20, ge=0)
    redact_pii: bool = True
    aggregate_only: bool = False


class Naming(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table_regex: str | None = None
    tableau_field_regex: str | None = None

    @field_validator("table_regex", "tableau_field_regex")
    @classmethod
    def _must_compile(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"invalid regular expression {value!r}: {exc}") from exc
        return value


class Thresholds(BaseModel):
    """Known thresholds are typed; check-specific thresholds may be added
    by rulesets without a model change, so extras are allowed here."""

    model_config = ConfigDict(extra="allow")

    null_rate_default: float = Field(default=0.0, ge=0.0, le=1.0)
    freshness_sla_hours_default: float = Field(default=24.0, gt=0)


# Column-name patterns treated as PII in evidence samples by default.
# Rulesets and profiles can extend this list.
DEFAULT_PII_COLUMN_PATTERNS: tuple[str, ...] = (
    r"(?i)email",
    r"(?i)phone",
    r"(?i)\bssn\b|social_security",
    r"(?i)address",
    r"(?i)(first|last|full)_?name",
    r"(?i)birth|\bdob\b",
    r"(?i)passport|driver_?license",
    r"(?i)\bip_?address\b",
)


class CheckSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)


class Ruleset(BaseModel):
    """The central, versioned standard. Pinned by the local tool so all
    analysts check against the same rules."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1)
    defaults: Defaults = Field(default_factory=Defaults)
    naming: Naming = Field(default_factory=Naming)
    deprecated_objects: list[str] = Field(default_factory=list)
    certified_sources: list[str] = Field(default_factory=list)
    severity_overrides: dict[str, Severity] = Field(default_factory=dict)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    pii_column_patterns: list[str] = Field(
        default_factory=lambda: list(DEFAULT_PII_COLUMN_PATTERNS)
    )
    checks: list[CheckSpec] = Field(default_factory=list)

    @field_validator("pii_column_patterns")
    @classmethod
    def _pii_patterns_compile(cls, value: list[str]) -> list[str]:
        for pattern in value:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(
                    f"invalid PII column pattern {pattern!r}: {exc}"
                ) from exc
        return value

    @field_validator("version")
    @classmethod
    def _version_has_no_whitespace(cls, value: str) -> str:
        if value != value.strip() or any(c.isspace() for c in value):
            raise ValueError("ruleset version must not contain whitespace")
        return value

    @model_validator(mode="after")
    def _check_ids_unique(self) -> "Ruleset":
        seen: set[str] = set()
        for spec in self.checks:
            if spec.id in seen:
                raise ValueError(f"duplicate check id in ruleset: {spec.id!r}")
            seen.add(spec.id)
        return self


class Profile(BaseModel):
    """A team overlay on the base ruleset (for example finance.yml).
    Everything is optional; whatever is present overrides or extends the
    base. Merge semantics live in config.loader.resolve_profile:
    scalars override, deprecated_objects and certified_sources extend,
    severity_overrides and thresholds merge by key, checks merge by id."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    defaults: dict[str, Any] = Field(default_factory=dict)
    naming: dict[str, Any] = Field(default_factory=dict)
    deprecated_objects: list[str] = Field(default_factory=list)
    certified_sources: list[str] = Field(default_factory=list)
    severity_overrides: dict[str, Severity] = Field(default_factory=dict)
    thresholds: dict[str, Any] = Field(default_factory=dict)
    pii_column_patterns: list[str] = Field(default_factory=list)
    checks: list[CheckSpec] = Field(default_factory=list)


class BaselineStoreConfig(BaseModel):
    """Where baselines live. 'local' is per-machine; 'shared' points at a
    team location (network share or mounted object store). Configured in
    ~/.plumb/baselines.yml or via PLUMB_BASELINE_DIR. Never a Snowflake
    write target; see ADR-0012."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["local", "shared"] = "local"
    path: str | None = None

    @model_validator(mode="after")
    def _shared_needs_path(self) -> "BaselineStoreConfig":
        if self.kind == "shared" and not self.path:
            raise ValueError("a shared baseline store requires a path")
        return self


class ConnectionProfile(BaseModel):
    """Local connection settings, never in the rules repo. Password auth
    is rejected by name so it can never sneak in via config."""

    model_config = ConfigDict(extra="forbid")

    account: str = Field(min_length=1)
    user: str = Field(min_length=1)
    authenticator: Literal["snowflake_jwt", "externalbrowser", "oauth", "pat"]
    private_key_path: str | None = None
    role: str = Field(min_length=1)
    warehouse: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _refuse_password(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key in data:
                if "password" in str(key).lower():
                    raise ValueError(
                        "password auth is not supported; use key-pair "
                        "(snowflake_jwt), externalbrowser SSO, oauth, or a "
                        "programmatic access token (pat), with secrets in the "
                        "OS keychain or environment"
                    )
        return data

    @model_validator(mode="after")
    def _key_pair_needs_key_path(self) -> "ConnectionProfile":
        if self.authenticator == "snowflake_jwt" and not self.private_key_path:
            raise ValueError(
                "authenticator snowflake_jwt requires private_key_path"
            )
        return self


class TableauConnection(BaseModel):
    """Tableau Server / Cloud connection. Auth is a Personal Access Token or a
    Connected App (JWT); the token value or app secret lives in the OS keychain,
    never in this file. Password auth is not supported."""

    model_config = ConfigDict(extra="forbid")

    server: str = Field(min_length=1)  # https://10ax.online.tableau.com or your server URL
    site: str = ""  # site content url; empty string is the default site
    auth: Literal["pat", "connected_app"] = "pat"
    pat_name: str | None = None  # PAT: the token name (secret value in keychain)
    client_id: str | None = None  # Connected App: client id
    secret_id: str | None = None  # Connected App: secret id (secret value in keychain)
    username: str | None = None  # Connected App: the user to act as (JWT sub)

    @model_validator(mode="before")
    @classmethod
    def _refuse_password(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key in data:
                if "password" in str(key).lower():
                    raise ValueError(
                        "password auth is not supported for Tableau; use a "
                        "Personal Access Token or a Connected App"
                    )
        return data

    @model_validator(mode="after")
    def _auth_fields(self) -> "TableauConnection":
        if self.auth == "pat" and not self.pat_name:
            raise ValueError("pat auth requires pat_name")
        if self.auth == "connected_app" and not (
            self.client_id and self.secret_id and self.username
        ):
            raise ValueError(
                "connected_app auth requires client_id, secret_id, and username"
            )
        return self
