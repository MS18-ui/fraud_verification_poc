"""
canonical_mapper.py

Maps RAW vendor payloads (as returned unmodified by the MCP vendor-gateway
tools: giact_verify, ekata_identity_check, ews_check) into the single
canonical schema defined in schemas/canonical_schema.json.

This is the ONLY place in the codebase that should know vendor-specific
field names. Rules (rules/*.yaml) and the ML fraud model read exclusively
from the canonical dict this module produces.

Design rule: every derived boolean/field here should be simple and
auditable (a one-line comparison), so a reviewer can trace any canonical
field back to the exact raw field(s) it came from via `_provenance`.
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Optional


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().upper()


def _same_name(first_a, last_a, first_b, last_b) -> bool:
    return _norm(first_a) == _norm(first_b) and _norm(last_a) == _norm(last_b)


def _same_address(sub: dict, line1: str, zip_code: str) -> bool:
    return _norm(sub.get("address_line1")) == _norm(line1) and _norm(sub.get("zip_code")) == _norm(zip_code)


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _days_since(dt_str: Optional[str], reference: datetime) -> Optional[int]:
    dt = _parse_dt(dt_str)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (reference - dt).days


def map_giact(giact_result: dict, submitted: dict, reference_time: datetime) -> dict:
    """
    giact_result: the raw `PostInquiryResult` object from a giact_verify call.
    submitted: the request fields we sent (first_name, last_name, address_line1,
               city, state, zip_code, phone, account_type, ...).
    """
    matched_people = giact_result.get("MatchedPersonData") or []
    person = matched_people[0] if matched_people else {}
    address_records = person.get("AddressRecords") or []
    alert_msgs = giact_result.get("ConsumerAlertMessages") or []
    ofac_matches = giact_result.get("OfacListPotentialMatches") or []

    record_found = bool(matched_people)
    name_match = record_found and _same_name(
        submitted.get("first_name"), submitted.get("last_name"),
        person.get("FirstName"), person.get("LastName"),
    )

    # Find the submitted address among this person's address history.
    address_match_status = "not_found"
    for rec in address_records:
        if _same_address(submitted, rec.get("AddressLine1"), rec.get("ZipCode")):
            status = (rec.get("Status") or "").lower()
            if status == "current":
                address_match_status = "current"
            elif status == "previous":
                address_match_status = "previous"
            elif status == "secondprevious":
                address_match_status = "second_previous"
            break  # first match wins; GIACT lists most-recent-first

    known_address_states = sorted({
        rec.get("State") for rec in address_records if rec.get("State")
    })

    recent_previous_90d = sum(
        1 for rec in address_records
        if (rec.get("Status") or "").lower() == "previous"
        and (_days_since(rec.get("DateReported"), reference_time) or 9_999) <= 90
    )

    # Submitted phone's on-file classification (for D3), if it appears in PhoneNumbers.
    submitted_phone_classification = None
    submitted_phone = (submitted.get("phone") or "").strip()
    for ph in (person.get("PhoneNumbers") or []):
        digits = f"{ph.get('AreaCode','')}{ph.get('Exchange','')}{ph.get('Suffix','')}"
        if digits and digits == submitted_phone.replace("-", "").replace(" ", ""):
            submitted_phone_classification = ph.get("Classification")
            break

    giact_alerts = [
        {"source": "giact", "code": a.get("Code"), "type": None, "message": a.get("Details")}
        for a in alert_msgs
    ]

    return {
        "record_found": record_found,
        "name_match": name_match,
        "ofac_hit": len(ofac_matches) > 0,
        "ofac_potential_matches_count": len(ofac_matches),
        "ssn_status": (person.get("SsnStatus") or "").lower() or None,
        "ssn_issue_state": person.get("SsnIssueState"),
        "ssn_issue_start_year": int(person["SsnIssueStartYear"]) if person.get("SsnIssueStartYear") else None,
        "known_address_states": known_address_states,
        "address_match": address_match_status == "current",
        "address_match_status": address_match_status,
        "recent_previous_address_count_90d": recent_previous_90d,
        "consumer_alert": len(alert_msgs) > 0,
        "alerts": giact_alerts,
        "adverse_alert_codes": [a.get("Code") for a in alert_msgs if a.get("Code")],
        "verification_response": giact_result.get("VerificationResponse", "Unknown"),
        "account_verified": giact_result.get("VerificationResponse") == "Pass",
        "response_code": giact_result.get("AccountResponseCode"),
        "account_closed_date": giact_result.get("AccountClosedDate"),
        "funds_confirmation_result": giact_result.get("FundsConfirmationResult"),
        "account_added_date": giact_result.get("AccountAddedDate"),
        "account_last_updated_date": giact_result.get("AccountLastUpdatedDate"),
        "submitted_phone_classification": submitted_phone_classification,
    }


def map_ekata(ekata_response: dict, reference_time: datetime) -> dict:
    """ekata_response: the raw response body from an ekata_identity_check call."""
    phone_checks = ekata_response.get("primary_phone_checks") or {}
    addr_checks = ekata_response.get("primary_address_checks") or {}
    email_checks = ekata_response.get("primary_email_address_checks") or {}
    ip_checks = ekata_response.get("ip_address_checks") or {}
    alerts = ekata_response.get("alerts") or []

    identity_check_score = ekata_response.get("identity_check_score")
    # Low-score safety flag: independent floor below the REVIEW band, tune as needed.
    low_identity_score = identity_check_score is not None and identity_check_score < 150

    ekata_alerts = [
        {"source": "ekata", "code": None, "type": a.get("type"), "message": a.get("message")}
        for a in alerts
    ]

    return {
        "phone_name_match": phone_checks.get("match_to_name") == "Match",
        "email_name_match": email_checks.get("match_to_name") == "Match",
        "address_name_match": addr_checks.get("match_to_name") == "Match",
        "ekata_address_match_to_name": addr_checks.get("match_to_name", "Unknown"),
        "is_commercial_address": bool(addr_checks.get("is_commercial", False)),
        "is_forwarder_address": bool(addr_checks.get("is_forwarder", False)),
        "address_type": addr_checks.get("type"),
        "identity_check_score": identity_check_score,
        "identity_network_score": ekata_response.get("identity_network_score"),
        "low_identity_score": low_identity_score,
        "alerts": ekata_alerts,
        "prepaid_phone": bool(phone_checks.get("is_prepaid", False)),
        "phone_match_to_address": phone_checks.get("match_to_address"),
        "phone_line_type": phone_checks.get("line_type"),
        "email_is_disposable": bool(email_checks.get("is_disposable", False)),
        "email_is_autogenerated": bool(email_checks.get("is_autogenerated", False)),
        "email_first_seen_days": email_checks.get("email_first_seen_days"),
        "mailbox_velocity": email_checks.get("mailbox_velocity"),
        "email_risk_score": email_checks.get("email_risk_score"),
        "ip_proxy_risk": bool(ip_checks.get("proxy_risk", False)),
        "ip_error": ip_checks.get("error"),
        "ip_warnings": ip_checks.get("warnings") or [],
        "distance_from_primary_address_miles": ip_checks.get("distance_from_primary_address"),
        "distance_from_primary_phone_miles": ip_checks.get("distance_from_primary_phone"),
    }


def _yn(value: Optional[str]) -> Optional[bool]:
    """AVS's own Y/N/null convention -- None means "not evaluated", distinct
    from an explicit non-match."""
    if value not in ("Y", "N"):
        return None
    return value == "Y"


def map_ews(ews_response: Optional[dict]) -> dict:
    """
    ews_response: the raw response body from an ews_check call -- the REAL
    AVS response shape (CorrelationId / SuccessResponse wrapper, with
    accountStatus + accountOwnerMatch nested inside), per the client's
    actual sample request/response -- not an invented schema.

    EWS is the waterfall's PRIMARY vendor, so when it's the one that
    actually answered and GIACT wasn't called, its own match data and
    native overallMatchScore drive identity/owner/account fields directly
    (see build_canonical()'s ews_only handling below) rather than
    defaulting to "no GIACT record".

    The real AVS sample has no fraud-history field at all -- unlike the
    earlier invented EWS shape, ews_fraud_history is honestly None here,
    not fabricated.
    """
    empty = {
        "record_found": None, "name_match": None, "address_match": None,
        "overall_match_score": None, "condition_code": None, "condition_code_message": None,
        "account_status": None, "ews_fraud_history": None,
    }
    if not ews_response:
        return empty

    success = ews_response.get("SuccessResponse") or {}
    account_status = (success.get("accountStatus") or {}).get("status")
    owner_match = success.get("accountOwnerMatch")

    if owner_match is None:
        # Real AVS shape for "nothing found" -- SuccessResponse present but
        # accountOwnerMatch absent. No confirmed vendor sample for this
        # exact outcome was provided; this is the most defensible reading
        # of the confirmed shape, not a documented AVS "no data" response.
        return {**empty, "record_found": account_status not in (None, "NOT_FOUND"), "account_status": account_status}

    name_match = _yn(owner_match.get("businessNameMatch"))
    if name_match is None:
        first = _yn(owner_match.get("firstNameMatch"))
        last = _yn(owner_match.get("lastNameMatch"))
        name_match = (first and last) if (first is not None or last is not None) else None

    return {
        "record_found": account_status not in (None, "NOT_FOUND"),
        "name_match": name_match,
        "address_match": _yn(owner_match.get("addressMatch")),
        "overall_match_score": owner_match.get("overallMatchScore"),
        "condition_code": owner_match.get("conditionCode"),
        "condition_code_message": owner_match.get("conditionCodeMessage"),
        "account_status": account_status,
        "ews_fraud_history": None,  # not a field the real AVS response carries
    }


def build_canonical(
    unique_id: str,
    submitted: dict,
    giact_result: Optional[dict],
    ekata_response: Optional[dict],
    ews_response: Optional[dict] = None,
    account_holder_type: str = "person",
    reference_time: Optional[datetime] = None,
    primary_source: Optional[str] = None,
) -> dict:
    """
    Top-level entry point. Any vendor payload may be None if that vendor
    call was skipped (see engine/orchestrator_stub.py: has_min_fields) --
    downstream rules must tolerate missing fields via `exists` checks.

    primary_source: which vendor's data should drive identity/owner/account
    precedence when EWS's own answer is present -- normally inferred (EWS
    wins only when GIACT wasn't called at all), but the waterfall's
    "Random" demo scenario can have EWS, GIACT, AND Ekata all answer in
    the same run (engine/waterfall.py's call_all mode). In that case
    "GIACT wasn't called" is no longer the right signal for whether EWS
    should be credited -- pass the caller's actual terminal vendor
    ("ews" | "giact" | "ekata") instead. Defaults to the old inferred
    behavior when not given, so every existing caller is unaffected.
    """
    reference_time = reference_time or datetime.now(timezone.utc)

    g = map_giact(giact_result, submitted, reference_time) if giact_result else {}
    e = map_ekata(ekata_response, reference_time) if ekata_response else {}
    w = map_ews(ews_response)

    # EWS is the waterfall's primary vendor -- its own match data drives
    # identity/owner/account fields directly whenever EWS is the vendor
    # that actually resolved the case (primary_source == "ews"), even if
    # GIACT also happens to have answered in the same run (e.g. the
    # Random/call-all demo scenario). Falls back to the original signal
    # (GIACT simply wasn't called) when the caller doesn't specify.
    if primary_source is not None:
        ews_only = primary_source == "ews" and ews_response is not None
    else:
        ews_only = giact_result is None and ews_response is not None

    identity_record_found = w["record_found"] if ews_only else g.get("record_found", False)
    identity_name_match = (w["name_match"] or False) if ews_only else g.get("name_match", False)
    owner_address_match = (w["address_match"] or False) if ews_only else g.get("address_match", False)
    owner_address_match_status = (("current" if w["address_match"] else "not_found") if ews_only
                                   else g.get("address_match_status", "not_found"))

    if ews_only:
        # Real AVS has no VerificationResponse-style enum -- derive a
        # comparable verdict from accountStatus + the native overallMatchScore.
        account_verification_response = ("Verified" if w["account_status"] == "OPEN" and (w["overall_match_score"] or 0) >= 70
                                          else "Unknown")
        account_verified = account_verification_response == "Verified"
        account_response_code = w["condition_code"]
    else:
        account_verification_response = g.get("verification_response", "Unknown")
        account_verified = g.get("account_verified", False)
        account_response_code = g.get("response_code")

    name_match_any = bool(identity_name_match) or (
        bool(e.get("phone_name_match")) and bool(e.get("email_name_match"))
    )

    combined_alerts = (g.get("alerts") or []) + (e.get("alerts") or [])

    canonical = {
        "schema_version": "1.0",
        "unique_id": unique_id,

        "identity": {
            "record_found": identity_record_found,
            "name_match": identity_name_match,
            "ofac_hit": g.get("ofac_hit", False),
            "ssn_status": g.get("ssn_status"),
            "identity_check_score": e.get("identity_check_score"),
            "identity_network_score": e.get("identity_network_score"),
            "low_identity_score": e.get("low_identity_score", False),
            "phone_name_match": e.get("phone_name_match", False),
            "email_name_match": e.get("email_name_match", False),
            "address_name_match": e.get("address_name_match", False),
            "name_match_any": name_match_any,
            "alerts": combined_alerts,
            "alerts_count": len(combined_alerts),
        },

        "owner": {
            "name_match": identity_name_match,
            "address_match": owner_address_match,
            "address_match_status": owner_address_match_status,
            "consumer_alert": (False if ews_only else g.get("consumer_alert", False)),
            "ekata_address_name_match": e.get("address_name_match", False),
            "ekata_address_match_to_name": e.get("ekata_address_match_to_name", "Unknown"),
            "is_commercial_address": e.get("is_commercial_address", False),
            "is_forwarder_address": e.get("is_forwarder_address", False),
            "address_type": e.get("address_type"),
            "recent_previous_address_count_90d": g.get("recent_previous_address_count_90d", 0),
        },

        "account": {
            "verification_response": account_verification_response,
            "account_verified": account_verified,
            "response_code": account_response_code,
            "account_closed_date": g.get("account_closed_date"),
            "funds_confirmation_result": g.get("funds_confirmation_result"),
            "account_added_date": g.get("account_added_date"),
            "account_last_updated_date": g.get("account_last_updated_date"),
            "ews_fraud_history": w.get("ews_fraud_history"),
        },

        "supplementary": {
            "ssn_issue_state": g.get("ssn_issue_state"),
            "ssn_issue_start_year": g.get("ssn_issue_start_year"),
            "date_of_birth": submitted.get("date_of_birth"),
            "known_address_states": g.get("known_address_states", []),
            "prepaid_phone": e.get("prepaid_phone", False),
            "phone_match_to_address": e.get("phone_match_to_address"),
            "phone_line_type": e.get("phone_line_type"),
            "submitted_phone_classification": g.get("submitted_phone_classification"),
            "account_holder_type": account_holder_type,
            "email_is_disposable": e.get("email_is_disposable", False),
            "email_is_autogenerated": e.get("email_is_autogenerated", False),
            "email_first_seen_days": e.get("email_first_seen_days"),
            "mailbox_velocity": e.get("mailbox_velocity"),
            "email_risk_score": e.get("email_risk_score"),
            "ip_proxy_risk": e.get("ip_proxy_risk", False),
            "ip_error": e.get("ip_error"),
            "ip_warnings": e.get("ip_warnings", []),
            "distance_from_primary_address_miles": e.get("distance_from_primary_address_miles"),
            "distance_from_primary_phone_miles": e.get("distance_from_primary_phone_miles"),
            "travel_alert_on_file": False,  # set true only if a distinct relocation notice exists on file
            "ofac_potential_matches_count": g.get("ofac_potential_matches_count", 0),
            "adverse_alert_codes": g.get("adverse_alert_codes", []),
        },

        "_provenance": {
            "identity.record_found": "ews" if ews_only else "giact",
            "identity.name_match": "ews" if ews_only else "giact",
            "identity.ofac_hit": "giact",     # EWS/AVS does not screen OFAC -- always GIACT when present
            "identity.ssn_status": "giact",   # EWS/AVS does not return SSN status -- always GIACT when present
            "identity.identity_check_score": "ekata",
            "identity.identity_network_score": "ekata",
            "identity.phone_name_match": "ekata",
            "identity.email_name_match": "ekata",
            "identity.address_name_match": "ekata",
            "owner.address_match": "ews" if ews_only else "giact",
            "owner.address_match_status": "ews" if ews_only else "giact",
            "owner.ekata_address_name_match": "ekata",
            "owner.is_commercial_address": "ekata",
            "account.verification_response": "ews" if ews_only else "giact",
            "account.response_code": "ews" if ews_only else "giact",
            "account.ews_fraud_history": "ews",
        },
    }

    # A field's declared source vendor above may not actually have been
    # called this run (e.g. Ekata's fields when only LSEG/GIACT was
    # reached, such as an international-route match) -- in that case the
    # field is genuinely unset (its None/False default), not really "from"
    # that vendor. Null out the label rather than falsely attributing it,
    # so the Canonical Model annex view doesn't credit a vendor that was
    # never reached. Bridged (single-source) payloads still count as
    # "called" here -- callers that synthesize one vendor's shape from
    # another's data (see engine/waterfall.py) relabel those specific
    # fields themselves afterward.
    called = {"giact": giact_result is not None, "ekata": ekata_response is not None, "ews": ews_response is not None}
    for field, source in canonical["_provenance"].items():
        if source in called and not called[source]:
            canonical["_provenance"][field] = None

    return canonical
