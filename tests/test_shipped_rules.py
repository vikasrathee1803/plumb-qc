"""The default ruleset and team profiles that ship in rules/ must always
load, validate, and resolve. A broken shipped standard is a release blocker."""

from pathlib import Path

import pytest

from plumb.config.loader import load_profile, load_ruleset, resolve_profile

RULES_DIR = Path(__file__).parent.parent / "rules"


def test_default_ruleset_loads() -> None:
    ruleset = load_ruleset(RULES_DIR / "plumb.yml", enforce_pin=False)
    assert ruleset.version == "2026.06.0"
    assert ruleset.defaults.fail_on == "REVIEW"
    assert ruleset.defaults.redact_pii is True
    enabled = [c.id for c in ruleset.checks if c.enabled]
    # static, metadata, regression, and performance run out of the box
    assert "S-STAT-002" in enabled
    assert "S-META-001" in enabled
    assert "R-DIFF-001" in enabled
    assert "P-PROF-001" in enabled
    # checks that need declared params are off until configured
    disabled = [c.id for c in ruleset.checks if not c.enabled]
    assert "D-GRAIN-001" in disabled
    assert "D-RECON-001" in disabled


@pytest.mark.parametrize("profile_name", ["finance", "marketing"])
def test_shipped_profiles_resolve(profile_name: str) -> None:
    base = load_ruleset(RULES_DIR / "plumb.yml", enforce_pin=False)
    profile = load_profile(RULES_DIR / "profiles" / f"{profile_name}.yml")
    resolved = resolve_profile(base, profile)
    assert resolved.version == base.version


def test_finance_is_stricter_than_base() -> None:
    base = load_ruleset(RULES_DIR / "plumb.yml", enforce_pin=False)
    profile = load_profile(RULES_DIR / "profiles" / "finance.yml")
    resolved = resolve_profile(base, profile)
    assert resolved.defaults.fail_on == "READY_WITH_NOTES"
    assert resolved.defaults.aggregate_only is True
    assert resolved.defaults.evidence_sample_rows == 0
