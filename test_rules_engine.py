"""
Run with: pytest tests/test_rules_engine.py -v
(run from the verification-engine/ directory so `engine` is importable)
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.canonical_mapper import build_canonical
from engine.decision import run_decision

SAMPLE_PATH = os.path.join(os.path.dirname(__file__), "sample_data", "partial_match.json")


def _load_sample():
    with open(SAMPLE_PATH) as f:
        return json.load(f)


def _build_canonical_from_sample(sample: dict) -> dict:
    entity = sample["entity"]
    giact_result = sample["vendors"]["giact"]["response"]["PostInquiryResult"]
    ekata_response = sample["vendors"]["ekata"]["response"]

    submitted = {
        "first_name": entity["first_name"],
        "last_name": entity["last_name"],
        "address_line1": entity["address_line1"],
        "city": entity["city"],
        "state": entity["state"],
        "zip_code": entity["zip_code"],
        "phone": entity["phone_number"],
        "date_of_birth": entity["date_of_birth"],
    }

    # Fixed reference time so day-based computed rules are deterministic in tests.
    reference_time = datetime(2026, 7, 23, tzinfo=timezone.utc)

    return build_canonical(
        unique_id=sample["unique_id"],
        submitted=submitted,
        giact_result=giact_result,
        ekata_response=ekata_response,
        ews_response=None,
        account_holder_type="person",
        reference_time=reference_time,
    )


def test_canonical_mapping_matches_expected_fields():
    sample = _load_sample()
    canonical = _build_canonical_from_sample(sample)

    assert canonical["identity"]["record_found"] is True
    assert canonical["identity"]["name_match"] is True
    assert canonical["identity"]["ofac_hit"] is False
    assert canonical["identity"]["ssn_status"] == "clear"
    assert canonical["identity"]["identity_check_score"] == 300
    assert round(canonical["identity"]["identity_network_score"], 3) == 0.303

    # Submitted address (7579 LIBERTY ST) is on file only as "Previous"
    assert canonical["owner"]["address_match"] is False
    assert canonical["owner"]["address_match_status"] == "previous"
    assert canonical["owner"]["is_commercial_address"] is True
    assert canonical["owner"]["address_type"] == "Commercial mail drop"
    assert canonical["owner"]["ekata_address_name_match"] is False

    assert canonical["account"]["verification_response"] == "AcceptWithRisk"
    assert canonical["account"]["response_code"] == "RT03"
    assert canonical["account"]["account_closed_date"] is None


def test_identity_outcome_is_review():
    sample = _load_sample()
    canonical = _build_canonical_from_sample(sample)
    result = run_decision(canonical)
    assert result["primary_outcomes"]["identity"] == "review"
    assert result["primary_rule_ids"]["identity"] == "IDENTITY_REVIEW"


def test_owner_outcome_flags_commercial_mail_drop():
    sample = _load_sample()
    canonical = _build_canonical_from_sample(sample)
    result = run_decision(canonical)
    # Name matches but address is stale AND flagged as a commercial mail drop ->
    # OWNER_FAIL_COMMERCIAL_MAIL_DROP is checked before the stale-address REVIEW rule.
    assert result["primary_outcomes"]["owner"] == "fail"
    assert result["primary_rule_ids"]["owner"] == "OWNER_FAIL_COMMERCIAL_MAIL_DROP"


def test_account_outcome_is_review():
    sample = _load_sample()
    canonical = _build_canonical_from_sample(sample)
    result = run_decision(canonical)
    assert result["primary_outcomes"]["account"] == "review"
    assert result["primary_rule_ids"]["account"] == "ACCOUNT_REVIEW_ACCEPT_WITH_RISK"


def test_overall_decision_is_decline_due_to_owner_fail():
    sample = _load_sample()
    canonical = _build_canonical_from_sample(sample)
    result = run_decision(canonical)
    # identity=review, owner=fail, account=review -> "owner fail" row forces decline
    assert result["overall_decision"] == "decline"


def test_supplementary_flags_include_expected_triggers():
    sample = _load_sample()
    canonical = _build_canonical_from_sample(sample)
    result = run_decision(canonical)
    triggered_ids = {f["id"] for f in result["supplementary_flags"]}
    # E4: email_risk_score 0.43 >= 0.5? No -> should NOT trigger (sanity check threshold works)
    assert "E4_ELEVATED_EMAIL_RISK" not in triggered_ids
    # D1: prepaid phone is True in the sample
    assert "D1_PREPAID_PHONE" in triggered_ids
    # F2: distances are 140/140 miles, both > 100, no travel alert on file -> triggers
    assert "F2_IMPOSSIBLE_TRAVEL" in triggered_ids
    # G1: no OFAC matches in this sample
    assert "G1_OFAC_POTENTIAL_MATCH" not in triggered_ids


def test_labeled_ground_truth_is_fraud_and_engine_declines():
    """Sanity cross-check against the sample's embedded ground-truth label."""
    sample = _load_sample()
    assert sample["labels"]["is_fraud"] == 1
    assert sample["labels"]["typology"] == "identity_theft"

    canonical = _build_canonical_from_sample(sample)
    result = run_decision(canonical)
    assert result["overall_decision"] in ("decline", "manual_review")
