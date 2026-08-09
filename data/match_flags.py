"""
Match tiers and per-element match flags, derived from a vendor response.

Each of the two surfaces expresses "did the payee we asked about match the
payee on file?" in its own vocabulary -- GIACT in response *codes*, Ekata in
categorical *match levels*. This module reduces both to one shared vocabulary:

- a set of boolean ``flag_*`` attributes, one per identity element, and
- exactly one ``match_tier`` per vendor: ``complete_match`` / ``partial_match``
  / ``no_match``.

**One verdict per vendor, none across vendors.** A vendor's tier folds all of
that vendor's own signals together (``worst_tier``), so a single response can
never read as both partial and complete. The two vendors are scored
independently and are *expected* to disagree -- GIACT can match on an address
the Ekata file has never seen, and that disagreement is itself a feature.
Nothing here computes a blended cross-vendor verdict.

Tiering is deliberately about **match**, not risk. A payee can match every
element and still score as risky; that shows up as a separate
``flag_low_identity_score``, never as a downgraded tier. Keeping the two apart
is what lets a model use them as independent features.

Thresholds are pinned to the generators' own value sets, not guessed -- see
``EKATA_SCORE_LOW`` and ``EKATA_ADDRESS_MATCH``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from data import scenarios

COMPLETE_MATCH = "complete_match"
PARTIAL_MATCH = "partial_match"
NO_MATCH = "no_match"

#: Best-to-worst. `worst_tier` folds several signals into a single verdict.
TIER_ORDER = (COMPLETE_MATCH, PARTIAL_MATCH, NO_MATCH)

#: Every outcome in `scenarios.ACCOUNT_CODE_OUTCOMES` /
#: `CUSTOMER_CODE_OUTCOMES`, mapped to the tier it implies. `PassNdd` is a pass
#: variant (next-day debit), so it tiers as a complete match.
OUTCOME_TIER: Dict[str, str] = {
    "Pass": COMPLETE_MATCH,
    "PassNdd": COMPLETE_MATCH,
    "AcceptWithRisk": PARTIAL_MATCH,
    "NoData": NO_MATCH,
    "Declined": NO_MATCH,
    "RiskAlert": NO_MATCH,
    "NegativeData": NO_MATCH,
    "RejectItem": NO_MATCH,
    "PrivateBadChecksList": NO_MATCH,
}

#: Ekata match levels that count as agreement. These mirror the "ok" branches
#: of `ekata_response_generator._name_match` / `_addr_match` exactly -- a
#: level the generator only emits on disagreement must never read as a match.
EKATA_NAME_MATCH = frozenset({"Match"})
EKATA_ADDRESS_MATCH = frozenset({"Match", "Zip+4 match", "Postal match"})

#: Ekata's `identity_check_score` runs 0-500 with *higher = safer*. Below this
#: the identity is flagged risky. Independent of the tier -- see the module
#: docstring.
EKATA_SCORE_LOW = 250

#: Miles between the IP and the address beyond which they stop corroborating.
#: `_distance(near=True)` tops out at 25 and `near=False` starts at 50.
IP_LOCAL_MILES = 25


def worst_tier(*tiers: Optional[str]) -> str:
    """The most severe of several tiers. Unknown/None values are ignored."""
    ranked = [TIER_ORDER.index(t) for t in tiers if t in TIER_ORDER]
    return TIER_ORDER[max(ranked)] if ranked else NO_MATCH


def _tier_from_ratio(matched: int, total: int) -> str:
    """Tier from "how many of the elements we could check actually agreed"."""
    if total == 0 or matched == 0:
        return NO_MATCH
    return COMPLETE_MATCH if matched == total else PARTIAL_MATCH


# ── GIACT ───────────────────────────────────────────────────────────────────

def _giact_name_matched(
    customer: Dict[str, Any],
    persons: Sequence[Dict[str, Any]],
    businesses: Sequence[Dict[str, Any]],
) -> bool:
    """Does any matched record carry the submitted name?

    The generators upper-case the echoed identity, so both sides are folded to
    upper case before comparing.
    """
    if customer.get("BusinessName"):
        want = customer["BusinessName"].upper()
        return any(
            (rec.get("BusinessName") or "").upper() == want
            for biz in businesses
            for rec in biz.get("NameRecords") or []
        )
    first = (customer.get("FirstName") or "").upper()
    last = (customer.get("LastName") or "").upper()
    if not (first and last):
        return False
    return any(
        (p.get("FirstName") or "").upper() == first
        and (p.get("LastName") or "").upper() == last
        for p in persons
    )


def giact_flags(inquiry: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    """Match flags + tier for one `PostInquiryResult`.

    `inquiry` is the inner Inquiry dict, `result` the inner PostInquiryResult.

    The tier is the worse of the account verdict (`AccountResponseCode`) and
    the identity verdict (`CustomerResponseCode`), floored to `no_match` when
    gIdentify ran but the bureau returned no record at all -- a code can read
    `Pass` against an empty file, and an empty file is not a match.
    """
    customer = inquiry.get("Customer") or {}
    persons = result.get("MatchedPersonData") or []
    businesses = result.get("MatchedBusinessData") or []
    alerts = result.get("ConsumerAlertMessages") or []
    ofac = result.get("OfacListPotentialMatches") or []
    account_code = result.get("AccountResponseCode")
    customer_code = result.get("CustomerResponseCode")

    # Does any matched record carry the input address as its *Current* address?
    input_addr = (customer.get("AddressLine1") or "").upper()
    address_matched = False
    n_addresses = 0
    for person in persons:
        for addr in person.get("AddressRecords") or []:
            n_addresses += 1
            if (input_addr
                    and addr.get("Status") == "Current"
                    and (addr.get("AddressLine1") or "").upper() == input_addr):
                address_matched = True

    account_tier = OUTCOME_TIER.get(
        scenarios.ACCOUNT_CODE_OUTCOMES.get(account_code or "", "")) \
        if account_code else None
    customer_tier = OUTCOME_TIER.get(
        scenarios.CUSTOMER_CODE_OUTCOMES.get(customer_code or "", "")) \
        if customer_code else None

    record_found = bool(persons or businesses)
    ran_identify = bool(inquiry.get("GIdentifyEnabled"))
    tier = worst_tier(account_tier, customer_tier)
    if ran_identify and not record_found:
        tier = NO_MATCH

    return {
        "match_tier": tier,
        "flag_account_verified": account_tier == COMPLETE_MATCH,
        "flag_identity_verified": customer_tier == COMPLETE_MATCH,
        "flag_record_found": record_found,
        "flag_name_match": _giact_name_matched(customer, persons, businesses),
        "flag_address_match": address_matched,
        "flag_consumer_alert": bool(alerts),
        "flag_ofac_hit": bool(ofac),
        # Kept alongside the flags because the loop above already walked them.
        "_n_addresses": n_addresses,
    }


# ── Ekata ───────────────────────────────────────────────────────────────────

def ekata_flags(response: Dict[str, Any]) -> Dict[str, Any]:
    """Match flags + tier for one `identity_check` response.

    The tier counts how many of the *submitted* elements agree with the name on
    file: a block the caller did not ask for is absent from the response and is
    left out of the denominator rather than counted as a failure.
    """
    phone = response.get("primary_phone_checks") or {}
    address = response.get("primary_address_checks") or {}
    email = response.get("primary_email_address_checks") or {}
    ip = response.get("ip_address_checks") or {}

    checks = {
        "flag_phone_name_match": (
            phone.get("match_to_name") in EKATA_NAME_MATCH
            if response.get("primary_phone_checks") else None),
        "flag_address_name_match": (
            address.get("match_to_name") in EKATA_NAME_MATCH
            if response.get("primary_address_checks") else None),
        "flag_email_name_match": (
            email.get("match_to_name") in EKATA_NAME_MATCH
            if response.get("primary_email_address_checks") else None),
    }
    evaluated = [v for v in checks.values() if v is not None]
    score = response.get("identity_check_score")
    ip_distance = ip.get("distance_from_primary_address")

    return {
        "match_tier": _tier_from_ratio(sum(evaluated), len(evaluated)),
        **checks,
        "flag_phone_address_match":
            phone.get("match_to_address") in EKATA_ADDRESS_MATCH,
        "flag_ip_local": (ip_distance is not None
                          and ip_distance <= IP_LOCAL_MILES),
        "flag_ip_proxy_risk": bool(ip.get("proxy_risk")),
        "flag_email_disposable": bool(email.get("is_disposable")),
        "flag_low_identity_score": score is not None and score < EKATA_SCORE_LOW,
        "flag_alerts": bool(response.get("alerts")),
    }


# ── EWS ─────────────────────────────────────────────────────────────────────

def ews_flags(response: Dict[str, Any]) -> Dict[str, Any]:
    """Match flags + tier for one EWS account-owner-verification response.

    The tier is the worse of the account-status verdict (EWS status codes
    map onto the shared outcome vocabulary, so `OUTCOME_TIER` applies
    unchanged) and the indicator ratio over the evaluated matchIndicators.
    "U"/null indicators were not evaluated and are left out of the
    denominator; any "C" (conditional/close match) caps the ratio at
    partial -- a close match is not a complete one.
    """
    status = (response.get("accountStatus") or {}).get("code")
    indicators = response.get("matchIndicators") or {}
    evaluated = [v for v in indicators.values() if v in ("Y", "N", "C")]
    matched = sum(1 for v in evaluated if v == "Y")

    status_tier = None
    if status in scenarios.EWS_ACCOUNT_STATUS_CODES:
        status_tier = OUTCOME_TIER.get(
            scenarios.EWS_ACCOUNT_STATUS_CODES[status][1])
    ratio_tier = _tier_from_ratio(matched, len(evaluated))
    if "C" in evaluated:
        ratio_tier = worst_tier(ratio_tier, PARTIAL_MATCH)

    name_indicator = (indicators.get("nameMatch")
                      or indicators.get("businessNameMatch"))
    return {
        "match_tier": worst_tier(status_tier, ratio_tier),
        "flag_name_match": name_indicator == "Y",
        "flag_address_match": indicators.get("addressMatch") == "Y",
        "flag_account_open": status == "01",
        "flag_condition_reported": bool(response.get("accountConditions")),
        "flag_record_found": response.get("ownerOfRecord") is not None,
    }
