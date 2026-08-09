"""
Synthetic EWS-style account-owner-verification response generator.

Given an account-owner-verification request (account + identity), produces a
fully synthetic response modeled on Early Warning Services conventions:
an account status code, participant-reported condition codes, per-field
match indicators, and the owner of record. EWS has not shared a spec yet, so
the schema is invented -- see docs/references/ews-account-verification.md,
which is the document to reconcile when the real spec arrives.

Design notes
------------
- Same two generation modes as the GIACT generator (response_generator.py):
  * match_flag=None -- every value is a random draw; nothing from the request
    is required to appear in the response (ad-hoc requests).
  * match_flag 0 / 1 / 2 (complete / partial / no match) -- the response is
    produced in the requested category from the request itself, reusing the
    GIACT generator's partial-plan machinery (which field buckets deviate).
- Match indicators use an EWS-style vocabulary: "Y" match, "N" no match,
  "C" conditional/close match (the partial bucket), "U" unable to verify
  (the field was absent from the request -- GIACT's None analogue).
- Business vs personal follows the repo convention: a businessName on the
  identity makes it a business record (businessNameMatch, EIN-style taxId,
  no DOB/ID indicators); firstName+lastName make it personal. The response
  always carries an explicit recordType, per the team's requirement to
  differentiate the two.
- The participant (the institution reporting on the account) is a pure
  function of the routing number (data.reference.bank_name_for), never a
  random draw: the same routing number must not surface under two
  different bank names across responses.
"""

from __future__ import annotations

import random
from datetime import datetime
from typing import Any, Dict, List, Optional

from faker import Faker

from data import match_flags
from data import reference as refdata
from data import scenarios
from response_generator import (COMPLETE_MATCH, MATCH_FLAGS, NO_MATCH,
                                PARTIAL_MATCH, STATE_ABBRS, _partial_flag_plan,
                                _past_datetime, _perturb_address, _phone_parts)

fake = Faker("en_US")

#: Indicator values: Y match, N no match, C conditional/close, U not evaluated.
INDICATOR_VALUES = ("Y", "N", "C", "U")

#: Buckets whose partial deviation is severe enough to read as N rather than C,
#: mirroring the GIACT CA/CI table where name and TaxId mismatches grade
#: RiskAlert while address/phone/id grade AcceptWithRisk.
_SEVERE_BUCKETS = frozenset({"name", "tax"})

#: Condition codes that plausibly accompany each non-clean account status.
_CONDITIONS_BY_STATUS = {
    "02": ("P1", "P6"),
    "03": ("P3", "P5"),
    "04": ("P4",),
    "06": ("P1", "P6"),
    "07": ("P2",),
}

#: Indicator name -> the partial-plan bucket that deviates it. Email has no
#: bucket: like the GIACT flag identity, it deviates only on a full no-match.
_PERSONAL_INDICATORS = {
    "nameMatch": "name",
    "taxIdMatch": "tax",
    "addressMatch": "address",
    "phoneMatch": "phone",
    "emailMatch": None,
    "dateOfBirthMatch": "id",
    "idNumberMatch": "id",
}
_BUSINESS_INDICATORS = {
    "businessNameMatch": "name",
    "taxIdMatch": "tax",
    "addressMatch": "address",
    "phoneMatch": "phone",
    "emailMatch": None,
}
ALL_INDICATORS = ("nameMatch", "businessNameMatch", "taxIdMatch",
                  "addressMatch", "phoneMatch", "emailMatch",
                  "dateOfBirthMatch", "idNumberMatch")


def _giact_cased(identity: Dict[str, Any]) -> Dict[str, Any]:
    """The EWS identity in the GIACT Customer casing, so the GIACT partial
    plan and comparison helpers apply unchanged."""
    return {
        "FirstName": identity.get("firstName"),
        "LastName": identity.get("lastName"),
        "BusinessName": identity.get("businessName"),
        "AddressLine1": identity.get("addressLine1"),
        "City": identity.get("city"),
        "State": identity.get("state"),
        "ZipCode": identity.get("zipCode"),
        "PhoneNumber": identity.get("phoneNumber"),
        "TaxId": identity.get("taxId"),
        "DateOfBirth": identity.get("dateOfBirth"),
        "DlNumber": identity.get("idNumber"),
        "EmailAddress": identity.get("emailAddress"),
    }


def _indicator_present(identity: Dict[str, Any], name: str) -> bool:
    """Was the input behind an indicator supplied? Absent input -> "U"."""
    return bool({
        "nameMatch": identity.get("firstName") or identity.get("lastName"),
        "businessNameMatch": identity.get("businessName"),
        "taxIdMatch": identity.get("taxId"),
        "addressMatch": identity.get("addressLine1"),
        "phoneMatch": identity.get("phoneNumber"),
        "emailMatch": identity.get("emailAddress"),
        "dateOfBirthMatch": identity.get("dateOfBirth"),
        "idNumberMatch": identity.get("idNumber"),
    }[name])


def _flag_indicators(identity: Dict[str, Any], flag: int, is_business: bool,
                     plan: List[str]) -> Dict[str, Optional[str]]:
    """The per-field match picture implied by the flag. Inapplicable
    record-type fields are null; absent inputs are "U"."""
    applicable = _BUSINESS_INDICATORS if is_business else _PERSONAL_INDICATORS
    indicators: Dict[str, Optional[str]] = {}
    for name in ALL_INDICATORS:
        bucket = applicable.get(name)
        if name not in applicable:
            indicators[name] = None
        elif not _indicator_present(identity, name):
            indicators[name] = "U"
        elif flag == NO_MATCH:
            indicators[name] = "N"
        elif bucket is not None and bucket in plan:
            indicators[name] = "N" if bucket in _SEVERE_BUCKETS else "C"
        else:
            indicators[name] = "Y"
    return indicators


def _flag_owner(identity: Dict[str, Any], flag: int, is_business: bool,
                plan: List[str]) -> Optional[Dict[str, Any]]:
    """The owner of record the response echoes: COMPLETE copies the request
    (gaps filled synthetically), PARTIAL perturbs the planned buckets,
    NO_MATCH returns null -- no record was found for the account."""
    if flag == NO_MATCH:
        return None

    first = identity.get("firstName") or fake.first_name()
    last = identity.get("lastName") or fake.last_name()
    if "name" in plan and not is_business:
        last = fake.last_name()  # keep the first name -> visible overlap
    business_name = identity.get("businessName")
    if is_business and "name" in plan:
        business_name = f"{fake.last_name().upper()} HOLDINGS LLC"

    address = {
        "AddressLine1": identity.get("addressLine1") or fake.street_address(),
        "City": identity.get("city") or fake.city(),
        "State": identity.get("state") or random.choice(STATE_ABBRS),
        "ZipCode": (identity.get("zipCode") or fake.postcode())[:5],
    }
    if "address" in plan:
        address = _perturb_address(address)

    phone = identity.get("phoneNumber") or _phone_parts()["PhoneNumber"]
    if "phone" in plan:
        phone = _phone_parts()["PhoneNumber"]

    return {
        "name": None if is_business else f"{first} {last}".upper(),
        "businessName": (business_name or "").upper() or None
        if is_business else None,
        "address": {
            "line1": address["AddressLine1"],
            "city": address["City"],
            "state": address["State"],
            "postalCode": address["ZipCode"],
        },
        "phoneNumber": phone,
        "accountAddedDate": _past_datetime(),
        "accountLastActivityDate": _past_datetime(365),
    }


def _conditions_for(status_code: str,
                    flag: Optional[int]) -> List[Dict[str, str]]:
    """Condition codes consistent with the account status. A clean status
    carries none; "05" (no information) has nothing to report against."""
    pool = _CONDITIONS_BY_STATUS.get(status_code, ())
    if not pool or (flag == COMPLETE_MATCH):
        return []
    codes = random.sample(pool, k=min(len(pool), random.randint(1, 2)))
    return [{"code": c, "description": scenarios.EWS_CONDITION_CODES[c]}
            for c in sorted(codes)]


def _outcome_label(status_code: str,
                   indicators: Dict[str, Optional[str]]) -> str:
    """verificationOutcome consistent with what data.match_flags.ews_flags
    will derive: the worse of the status-code tier and the indicator ratio."""
    if status_code == "05":
        return "No Data"
    status_tier = match_flags.OUTCOME_TIER[
        scenarios.EWS_ACCOUNT_STATUS_CODES[status_code][1]]
    evaluated = [v for v in indicators.values() if v in ("Y", "N", "C")]
    matched = sum(1 for v in evaluated if v == "Y")
    ratio_tier = match_flags._tier_from_ratio(matched, len(evaluated))
    if evaluated and any(v == "C" for v in evaluated):
        ratio_tier = match_flags.worst_tier(ratio_tier,
                                            match_flags.PARTIAL_MATCH)
    tier = match_flags.worst_tier(status_tier, ratio_tier)
    return {match_flags.COMPLETE_MATCH: "Match",
            match_flags.PARTIAL_MATCH: "Partial Match",
            match_flags.NO_MATCH: "No Match"}[tier]


def _random_indicators(identity: Dict[str, Any],
                       is_business: bool) -> Dict[str, Optional[str]]:
    """Random-mode indicator draw: mostly Y with occasional C/N, like a
    healthy book with the odd stale record."""
    applicable = _BUSINESS_INDICATORS if is_business else _PERSONAL_INDICATORS
    return {
        name: (None if name not in applicable
               else "U" if not _indicator_present(identity, name)
               else random.choices(["Y", "C", "N"], [8, 1, 1])[0])
        for name in ALL_INDICATORS
    }


def generate_account_verification_response(
    request: Dict[str, Any],
    match_flag: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Build a full account-owner-verification response dict.

    `request` is the stored request body: {"account": {...}, "identity":
    {...}} plus optional "matchFlag" and "recordType". `match_flag` selects
    the category the response is generated in (0 complete / 1 partial /
    2 no match); when omitted it is read from the request's matchFlag field,
    and when absent there too all response data is a random draw.
    """
    account = request.get("account") or {}
    identity = request.get("identity") or {}
    if match_flag is None:
        match_flag = request.get("matchFlag")

    is_business = bool(identity.get("businessName"))
    record_type = "Business" if is_business else "Personal"

    if match_flag is not None:
        match_flag = int(match_flag)
        if match_flag not in MATCH_FLAGS:
            raise ValueError(
                f"match_flag must be 0 (complete), 1 (partial), or "
                f"2 (no match); got {match_flag}")
        plan = (_partial_flag_plan(_giact_cased(identity), is_business)
                if match_flag == PARTIAL_MATCH else [])
        status_code = random.choice(
            scenarios.EWS_STATUS_BY_TIER[match_flag])
        indicators = _flag_indicators(identity, match_flag, is_business, plan)
        owner = _flag_owner(identity, match_flag, is_business, plan)
    else:
        status_code = random.choice(
            list(scenarios.EWS_ACCOUNT_STATUS_CODES))
        indicators = _random_indicators(identity, is_business)
        owner = (None if status_code == "05"
                 else _flag_owner(identity, COMPLETE_MATCH, is_business, []))

    status_desc, _ = scenarios.EWS_ACCOUNT_STATUS_CODES[status_code]
    return {
        "responseId": "EWS-" + "".join(
            random.choices("0123456789abcdef", k=12)),
        "createdDate": datetime.now().isoformat(timespec="seconds"),
        "recordType": record_type,
        "accountStatus": {"code": status_code, "description": status_desc},
        "accountConditions": _conditions_for(status_code, match_flag),
        "verificationOutcome": _outcome_label(status_code, indicators),
        "matchIndicators": indicators,
        "ownerOfRecord": owner,
        "participant": {
            "name": refdata.bank_name_for(account.get("routingNumber")),
            "routingNumber": account.get("routingNumber"),
        },
    }
