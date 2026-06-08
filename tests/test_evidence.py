"""Evidence capping and PII redaction. No raw row egress by default."""

from plumb.config.models import Ruleset
from plumb.engine.evidence import REDACTED, evidence_rows_for, prepare_evidence_rows

ROWS = [
    {"ORDER_ID": 1, "CUSTOMER_EMAIL": "a@x.com", "FULL_NAME": "Ann", "AMOUNT": 10},
    {"ORDER_ID": 2, "CUSTOMER_EMAIL": "b@x.com", "FULL_NAME": "Bo", "AMOUNT": 20},
    {"ORDER_ID": 3, "CUSTOMER_EMAIL": "c@x.com", "FULL_NAME": "Cy", "AMOUNT": 30},
]


def test_cap_limits_row_count() -> None:
    out = prepare_evidence_rows(ROWS, sample_cap=2, redact=False, patterns=[])
    assert len(out) == 2


def test_redaction_masks_matching_columns_only() -> None:
    out = prepare_evidence_rows(
        ROWS, sample_cap=10, redact=True, patterns=[r"(?i)email", r"(?i)name"]
    )
    assert out[0]["CUSTOMER_EMAIL"] == REDACTED
    assert out[0]["FULL_NAME"] == REDACTED
    assert out[0]["ORDER_ID"] == 1
    assert out[0]["AMOUNT"] == 10


def test_redaction_off_keeps_values() -> None:
    out = prepare_evidence_rows(ROWS, sample_cap=10, redact=False, patterns=[r"email"])
    assert out[0]["CUSTOMER_EMAIL"] == "a@x.com"


def test_content_redaction_catches_pii_in_unnamed_columns() -> None:
    # No column-name patterns at all: content detectors must still fire.
    rows = [
        {
            "MEMO": "ref 4111 1111 1111 1111 paid",  # a Luhn-valid card number
            "NOTE": "call me at a.person@bank.com",  # an email
            "TAXID": "123-45-6789",  # an SSN
            "IBAN_RAW": "GB82WEST12345698765432",  # an IBAN
            "AMOUNT": 4242,  # not PII, even though it is digits
        }
    ]
    out = prepare_evidence_rows(rows, sample_cap=10, redact=True, patterns=[])
    assert out[0]["MEMO"] == REDACTED
    assert out[0]["NOTE"] == REDACTED
    assert out[0]["TAXID"] == REDACTED
    assert out[0]["IBAN_RAW"] == REDACTED
    assert out[0]["AMOUNT"] == 4242  # plain numbers are not redacted


def test_content_redaction_respects_redact_flag() -> None:
    rows = [{"MEMO": "card 4111 1111 1111 1111"}]
    out = prepare_evidence_rows(rows, sample_cap=10, redact=False, patterns=[])
    assert out[0]["MEMO"] != REDACTED


def test_aggregate_only_suppresses_all_rows() -> None:
    out = prepare_evidence_rows(
        ROWS, sample_cap=10, redact=True, patterns=[r"email"], aggregate_only=True
    )
    assert out == []


def test_default_ruleset_redacts_email_and_name() -> None:
    ruleset = Ruleset(version="1")
    out = evidence_rows_for(ruleset, ROWS)
    assert len(out) == 3
    assert out[0]["CUSTOMER_EMAIL"] == REDACTED
    assert out[0]["FULL_NAME"] == REDACTED
    assert out[0]["ORDER_ID"] == 1


def test_zero_sample_cap_yields_nothing() -> None:
    ruleset = Ruleset(version="1")
    ruleset.defaults.evidence_sample_rows = 0
    out = evidence_rows_for(ruleset, ROWS)
    assert out == []


def test_source_rows_are_not_mutated() -> None:
    original = [{"EMAIL": "a@x.com", "ID": 1}]
    prepare_evidence_rows(original, sample_cap=10, redact=True, patterns=[r"(?i)email"])
    assert original[0]["EMAIL"] == "a@x.com"
