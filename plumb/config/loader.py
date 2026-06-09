"""Load, validate, and resolve Plumb configuration.

A malformed ruleset fails loudly with a readable pydantic message and a
non-zero exit at the CLI; it never runs partial checks silently. Profile
resolution merges a team overlay onto the base ruleset and re-validates
the merged result, so a profile can never produce an invalid ruleset.

Version pinning (ADR-0004): the active pin lives in ~/.plumb/rules.pin.
When a pin exists, loading a ruleset whose version differs is an error.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from plumb.config.models import (
    BaselineStoreConfig,
    ConnectionProfile,
    Profile,
    Ruleset,
    TableauConnection,
)

PLUMB_HOME = Path.home() / ".plumb"
PIN_FILE = PLUMB_HOME / "rules.pin"
# Overridable so the settings page and tests never clobber a real profile.
CONNECTION_FILE = Path(os.environ.get("PLUMB_CONNECTION_FILE") or (PLUMB_HOME / "connection.yml"))
TABLEAU_FILE = Path(os.environ.get("PLUMB_TABLEAU_FILE") or (PLUMB_HOME / "tableau.yml"))
BASELINES_CONFIG_FILE = PLUMB_HOME / "baselines.yml"
ENV_BASELINE_DIR = "PLUMB_BASELINE_DIR"


class ConfigError(Exception):
    """Any configuration problem the user must fix. The CLI maps this to
    exit code 3 and prints the message via Rich."""


def _format_validation_error(source: Path | str, exc: ValidationError) -> str:
    lines = [f"invalid configuration in {source}:"]
    for err in exc.errors():
        location = ".".join(str(part) for part in err["loc"]) or "(root)"
        lines.append(f"  {location}: {err['msg']}")
    return "\n".join(lines)


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"configuration file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse YAML in {path}: {exc}") from exc
    if raw is None:
        raise ConfigError(f"configuration file is empty: {path}")
    if not isinstance(raw, dict):
        raise ConfigError(
            f"configuration root must be a mapping, got "
            f"{type(raw).__name__}: {path}"
        )
    return raw


def load_ruleset(
    path: Path,
    *,
    enforce_pin: bool = True,
    pin_file: Path | None = None,
) -> Ruleset:
    raw = load_yaml_mapping(path)
    try:
        ruleset = Ruleset.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(path, exc)) from exc
    if enforce_pin:
        pinned = read_pin(pin_file)
        if pinned is not None and ruleset.version != pinned:
            raise ConfigError(
                f"ruleset version {ruleset.version!r} does not match the "
                f"pinned version {pinned!r}; run 'plumb rules pin "
                f"{ruleset.version}' to repin or fetch the pinned ruleset"
            )
    return ruleset


def load_profile(path: Path) -> Profile:
    raw = load_yaml_mapping(path)
    if "name" not in raw:
        raw = {**raw, "name": path.stem}
    try:
        return Profile.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(path, exc)) from exc


def resolve_profile(base: Ruleset, profile: Profile) -> Ruleset:
    """Merge a profile overlay onto the base ruleset and re-validate.

    Semantics: defaults and naming merge per field. deprecated_objects and
    certified_sources extend the base lists (dedupe, base order first).
    severity_overrides and thresholds merge by key, profile wins. checks
    merge by id: a profile entry replaces the base entry wholesale, new
    ids are appended in profile order."""
    merged = base.model_dump(mode="python")

    merged["defaults"].update(profile.defaults)
    merged["naming"].update(profile.naming)

    for list_field in (
        "deprecated_objects",
        "certified_sources",
        "pii_column_patterns",
        "sandbox_patterns",
        "raw_layer_patterns",
        "integration_layer_patterns",
    ):
        base_items: list[str] = merged[list_field]
        extra = [x for x in getattr(profile, list_field) if x not in base_items]
        merged[list_field] = base_items + extra

    merged["severity_overrides"].update(
        {k: v.value for k, v in profile.severity_overrides.items()}
    )
    merged["thresholds"].update(profile.thresholds)

    checks_by_id = {spec["id"]: spec for spec in merged["checks"]}
    order = [spec["id"] for spec in merged["checks"]]
    for spec in profile.checks:
        dumped = spec.model_dump(mode="python")
        if spec.id not in checks_by_id:
            order.append(spec.id)
        checks_by_id[spec.id] = dumped
    merged["checks"] = [checks_by_id[check_id] for check_id in order]

    try:
        return Ruleset.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(
            _format_validation_error(f"profile {profile.name!r}", exc)
        ) from exc


def load_connection_profile(path: Path | None = None) -> ConnectionProfile:
    target = path or CONNECTION_FILE
    raw = load_yaml_mapping(target)
    try:
        return ConnectionProfile.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(target, exc)) from exc


def load_tableau_connection(path: Path | None = None) -> TableauConnection:
    target = path or TABLEAU_FILE
    raw = load_yaml_mapping(target)
    try:
        return TableauConnection.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(target, exc)) from exc


def load_baseline_store_config(path: Path | None = None) -> BaselineStoreConfig:
    """Resolve the baseline store config. PLUMB_BASELINE_DIR (a shared path)
    wins; then ~/.plumb/baselines.yml; otherwise the local default."""
    import os

    env_dir = os.environ.get(ENV_BASELINE_DIR)
    if env_dir:
        return BaselineStoreConfig(kind="shared", path=env_dir)
    target = path or BASELINES_CONFIG_FILE
    if not target.exists():
        return BaselineStoreConfig()
    raw = load_yaml_mapping(target)
    try:
        return BaselineStoreConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(target, exc)) from exc


def read_pin(pin_file: Path | None = None) -> str | None:
    target = pin_file or PIN_FILE
    if not target.exists():
        return None
    content = target.read_text(encoding="utf-8").strip()
    return content or None


def write_pin(version: str, pin_file: Path | None = None) -> None:
    if not version or any(c.isspace() for c in version):
        raise ConfigError(f"invalid ruleset version to pin: {version!r}")
    target = pin_file or PIN_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(version + "\n", encoding="utf-8")
