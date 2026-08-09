"""
demo_scenarios.py

Request/response bundles for the client-demo scenarios, grouped by transfer
type (see engine/waterfall.py: classify_transfer_type() for how a request's
type itself is determined -- domestic vs. international vs. onboarding,
from swift_code/routing_number/account_number presence, no internal tag
read).

Within a transfer type, scenario classification is driven by the explicit
`demo_scenario` key present in the request dictionary.

Domestic (EWS -> LSEG, falls through on no-data/outage)
    1. Direct match         -- EWS answers, single source
    2. Virtual account      -- account_number starts with "VA-" -> EWS has
                               no data, falls through to LSEG
    3. EWS outage           -- EWS call fails outright, falls through to LSEG
    4. Random (all vendors) -- EWS, LSEG, AND Ekata are all called
                               (engine/waterfall.py's call_all mode) so
                               every vendor's own response/score is visible
                               side by side. Still terminates at EWS (the
                               first success) for canonical/composite
                               purposes.

International (LSEG only, no fallback)
    5. Match                -- LSEG has the record

Onboarding (Ekata only -- no account/routing number exists yet)
    6. Match                -- Ekata's own phone/email/address checks
                               corroborate the applicant
"""

from __future__ import annotations
import random
from datetime import date
from typing import Any

from engine.waterfall import classify_transfer_type, DOMESTIC, INTERNATIONAL, ONBOARDING

# --- Domestic sub-case marker ---
VIRTUAL_ACCOUNT_PREFIX = "VA-"

# --- International sub-case marker -- which BIC the request carries ---
INTERNATIONAL_BIC_MATCH = "DEUTGB2LXXX"     # fictional, Deutsche Bank London-style BIC

DOMESTIC_DIRECT = "domestic_direct"
VIRTUAL_ACCOUNT_NO_EWS = "virtual_account_no_ews"
EWS_OUTAGE = "ews_outage"
DOMESTIC_RANDOM = "domestic_random"
INTERNATIONAL_MATCH = "international_match"
ONBOARDING_MATCH = "onboarding_match"


def classify_scenario(request: dict) -> str:
    """Full scenario classification -- explicitly via 'demo_scenario' key if present,
    otherwise falling back to field pattern rules."""
    # Explicit scenario mapping key takes precedence
    if "demo_scenario" in request:
        return request["demo_scenario"]

    transfer_type = classify_transfer_type(request)

    if transfer_type == DOMESTIC:
        account_number = (request.get("account_number") or "").strip().upper()
        if account_number.startswith(VIRTUAL_ACCOUNT_PREFIX):
            return VIRTUAL_ACCOUNT_NO_EWS
        return DOMESTIC_DIRECT

    if transfer_type == INTERNATIONAL:
        return INTERNATIONAL_MATCH

    return ONBOARDING_MATCH


# ---------------------------------------------------------------------------
# Local random-identity generator
# ---------------------------------------------------------------------------
FIRST_NAMES = [
    "Michael", "Elena", "Priya", "Taylor", "Grace", "Daniel", "Sofia", "Marcus",
    "Nina", "Owen", "Ava", "Lucas", "Maya", "Ethan", "Chloe", "Ravi",
    "Isabella", "Noah", "Zoe", "Aiden",
]
LAST_NAMES = [
    "Torres", "Whitfield", "Natarajan", "Brooks", "Kimani", "Bergman", "Reyes",
    "Okafor", "Larsen", "Chen", "Patel", "Morales", "Bianchi", "Novak",
    "Fitzgerald", "Delgado",
]
US_LOCATIONS = [
    # (city, state, zip, area_code)
    ("Minneapolis", "MN", "55401", "612"),
    ("Austin", "TX", "78701", "512"),
    ("Denver", "CO", "80202", "303"),
    ("Columbus", "OH", "43215", "614"),
    ("Seattle", "WA", "98101", "206"),
    ("Charlotte", "NC", "28202", "704"),
    ("Portland", "OR", "97201", "503"),
    ("Nashville", "TN", "37203", "615"),
]
US_STREET_NAMES = [
    "Wexford Lane", "Harborview Plaza", "Larkspur Court", "Foundry Row",
    "Meridian Ave", "Birchwood Drive", "Summit Ridge Trail", "Cedar Hollow Way",
    "Riverside Terrace", "Maple Crest Blvd",
]
UK_LOCATIONS = [
    # (city, postcode)
    ("London", "E14 5AB"),
    ("Manchester", "M1 4BT"),
    ("Edinburgh", "EH1 2NG"),
    ("Birmingham", "B1 2JP"),
]
UK_STREET_NAMES = ["Canary Wharf", "Deansgate", "Princes Street", "Colmore Row"]
EMAIL_DOMAINS = ["example.com"]  # keep on example.com -- obviously-fake, demo-safe


def _rand_digits(n: int) -> str:
    return "".join(random.choice("0123456789") for _ in range(n))


def _random_unique_id() -> str:
    return f"ORQ-{_rand_digits(7)}"


def _random_ssn() -> str:
    return f"{_rand_digits(3)}-{_rand_digits(2)}-{_rand_digits(4)}"


def _random_dob(min_age: int = 22, max_age: int = 68) -> str:
    year = date.today().year - random.randint(min_age, max_age)
    month = random.randint(1, 12)
    day = random.randint(1, 28)  # avoid month-length edge cases
    return f"{year:04d}-{month:02d}-{day:02d}"


def _random_routing_number() -> str:
    """9 random digits for routing numbers."""
    return _rand_digits(9)


def _jitter_int(base: int, spread: int, lo: int = 0, hi: int = 100) -> int:
    """Small random jitter around a baseline score, clamped so it can't
    cross out of the decision band the scenario is meant to demonstrate."""
    return max(lo, min(hi, base + random.randint(-spread, spread)))


def _jitter_float(base: float, spread: float, lo: float = 0.0, hi: float = 1.0, decimals: int = 2) -> float:
    value = base + random.uniform(-spread, spread)
    return round(max(lo, min(hi, value)), decimals)


def _random_us_person(min_age: int = 22, max_age: int = 68) -> dict[str, Any]:
    first, last = random.choice(FIRST_NAMES), random.choice(LAST_NAMES)
    city, state, zip_code, area_code = random.choice(US_LOCATIONS)
    return {
        "first_name": first, "last_name": last, "name": f"{first} {last}",
        "ssn": _random_ssn(), "dob": _random_dob(min_age, max_age),
        "address": {
            "address_line1": f"{random.randint(10, 9999)} {random.choice(US_STREET_NAMES)}",
            "city": city, "state": state, "zip_code": zip_code, "country": "US",
        },
        "phone": f"{area_code}{_rand_digits(7)}",
        "email": f"{first.lower()}.{last.lower()}@{random.choice(EMAIL_DOMAINS)}",
    }


def _random_uk_person(min_age: int = 25, max_age: int = 60) -> dict[str, Any]:
    first, last = random.choice(FIRST_NAMES), random.choice(LAST_NAMES)
    city, postcode = random.choice(UK_LOCATIONS)
    return {
        "first_name": first, "last_name": last, "name": f"{first} {last}",
        "dob": _random_dob(min_age, max_age),
        "address": {
            "address_line1": f"{random.randint(1, 99)} {random.choice(UK_STREET_NAMES)}",
            "city": city, "state": "", "zip_code": postcode, "country": "GB",
        },
        "phone": f"+44{_rand_digits(10)}",
        "email": f"{first.lower()}.{last.lower()}@{random.choice(EMAIL_DOMAINS)}",
    }


# ---------------------------------------------------------------------------
# Request builders -- every request carries an explicit `demo_scenario` key
# and randomized routing numbers.
# ---------------------------------------------------------------------------

def build_domestic_direct_request() -> dict[str, Any]:
    person = _random_us_person()
    return {
        "demo_scenario": DOMESTIC_DIRECT,
        "unique_id": _random_unique_id(),
        "first_name": person["first_name"], "last_name": person["last_name"], "name": person["name"],
        "ssn": person["ssn"], "dob": person["dob"],
        "routing_number": _random_routing_number(), "account_number": _rand_digits(10),
        "account_type": random.choice(["Checking", "Savings"]),
        "address": person["address"],
        "phone": person["phone"], "email": person["email"],
    }


def build_virtual_account_request() -> dict[str, Any]:
    person = _random_us_person()
    return {
        "demo_scenario": VIRTUAL_ACCOUNT_NO_EWS,
        "unique_id": _random_unique_id(),
        "first_name": person["first_name"], "last_name": person["last_name"], "name": person["name"],
        "ssn": person["ssn"], "dob": person["dob"],
        "routing_number": _random_routing_number(),
        "account_number": f"{VIRTUAL_ACCOUNT_PREFIX}{_rand_digits(9)}",
        "account_type": random.choice(["Checking", "Savings"]),
        "address": person["address"],
        "phone": person["phone"], "email": person["email"],
    }


def build_ews_outage_request() -> dict[str, Any]:
    person = _random_us_person()
    return {
        "demo_scenario": EWS_OUTAGE,
        "unique_id": _random_unique_id(),
        "first_name": person["first_name"], "last_name": person["last_name"], "name": person["name"],
        "ssn": person["ssn"], "dob": person["dob"],
        "routing_number": _random_routing_number(),
        "account_number": _rand_digits(10),
        "account_type": random.choice(["Checking", "Savings"]),
        "address": person["address"],
        "phone": person["phone"], "email": person["email"],
    }


def build_domestic_random_request() -> dict[str, Any]:
    person = _random_us_person()
    return {
        "demo_scenario": DOMESTIC_RANDOM,
        "unique_id": _random_unique_id(),
        "first_name": person["first_name"], "last_name": person["last_name"], "name": person["name"],
        "ssn": person["ssn"], "dob": person["dob"],
        "routing_number": _random_routing_number(),
        "account_number": _rand_digits(10),
        "account_type": random.choice(["Checking", "Savings"]),
        "address": person["address"],
        "phone": person["phone"], "email": person["email"],
    }


def build_international_match_request() -> dict[str, Any]:
    person = _random_uk_person()
    return {
        "demo_scenario": INTERNATIONAL_MATCH,
        "unique_id": _random_unique_id(),
        "first_name": person["first_name"], "last_name": person["last_name"], "name": person["name"],
        "dob": person["dob"],
        "swift_code": INTERNATIONAL_BIC_MATCH,
        "account_number": f"GB29NWBK{_rand_digits(14)}",  # IBAN-style, cosmetic
        "account_type": "Checking",
        "address": person["address"],
        "phone": person["phone"], "email": person["email"],
    }


def build_onboarding_request() -> dict[str, Any]:
    person = _random_us_person(min_age=10, max_age=150)
    return {
        "demo_scenario": ONBOARDING_MATCH,
        "unique_id": _random_unique_id(),
        "first_name": person["first_name"], "last_name": person["last_name"], "name": person["name"],
        "ssn": person["ssn"], "dob": person["dob"],
        "address": person["address"],
        "phone": person["phone"], "email": person["email"],
    }


# ---------------------------------------------------------------------------
# Canned vendor responses per scenario
# ---------------------------------------------------------------------------

def get_demo_response(tool_name: str, request: dict) -> dict[str, Any]:
    case = classify_scenario(request)
    addr = request.get("address") or {}
    first_upper = (request.get("first_name") or "").upper()
    last_upper = (request.get("last_name") or "").upper()
    addr_upper = {
        "AddressLine1": (addr.get("address_line1") or "").upper(),
        "City": (addr.get("city") or "").upper(),
        "State": addr.get("state") or "",
        "ZipCode": addr.get("zip_code") or "",
    }

    if case == DOMESTIC_DIRECT:
        if tool_name == "ews_check":
            return {
                "CorrelationId": f"c1-demo-domestic-{_rand_digits(3)}",
                "SuccessResponse": {
                    "callTypeReceived": "BASIC_OWNERANDSTATUS",
                    "customerID": "USBTreas",
                    "accountNumber": request.get("account_number"),
                    "routingNumber": request.get("routing_number"),
                    "accountStatus": {"status": "OPEN", "additionalDetails": {"status": None, "message": "No issues - per rule set"}},
                    "accountOwnerMatch": {
                        "conditionCode": "000", "conditionCodeMessage": "Normal return-no system errors.",
                        "nameMatch": None, "firstNameMatch": "Y", "lastNameMatch": "Y", "middleNameMatch": None,
                        "businessNameMatch": None, "fullAddressMatch": None, "addressMatch": "Y",
                        "cityMatch": "Y", "stateMatch": "Y", "zipMatch": "Y",
                        "homePhoneMatch": "Y", "workPhoneMatch": None, "ssnMatch": None, "dateOfBirthMatch": None,
                        "IDTypeMatch": None, "IDNumberMatch": None, "IDStateMatch": None,
                        "overallMatchScore": _jitter_int(96, 3, lo=90, hi=99),
                        "signerOwnerFlag": None,
                    },
                },
                "ErrorResponse": None,
                "IsSuccess": True,
                "AVSResponseCode": True,
                "ReasonPhrase": "OK",
            }
        return {}

    if case == VIRTUAL_ACCOUNT_NO_EWS:
        if tool_name == "ews_check":
            return {
                "CorrelationId": f"c1-demo-virtual-{_rand_digits(3)}",
                "SuccessResponse": {
                    "callTypeReceived": "BASIC_OWNERANDSTATUS",
                    "customerID": "USBTreas",
                    "accountNumber": request.get("account_number"),
                    "routingNumber": request.get("routing_number"),
                    "accountStatus": {"status": "NOT_FOUND", "additionalDetails": {"status": None, "message": "No account information located"}},
                    "accountOwnerMatch": None,
                },
                "ErrorResponse": None,
                "IsSuccess": True,
                "AVSResponseCode": False,
                "ReasonPhrase": "No Match",
            }
        if tool_name == "giact_verify":
            return {
                "MatchedPersonData": [{
                    "FirstName": first_upper, "LastName": last_upper,
                    "AddressRecords": [{**addr_upper, "Status": "Current"}],
                }],
                "ConsumerAlertMessages": [],
                "OfacListPotentialMatches": [],
                "VerificationResponse": "Pass",
                "AccountResponseCode": "_1111",
                "CustomerResponseCode": "CA11",
                "FundsConfirmationResult": "Confirmed",
            }
        return {}

    if case == EWS_OUTAGE:
        if tool_name == "giact_verify":
            return {
                "MatchedPersonData": [{
                    "FirstName": first_upper, "LastName": last_upper,
                    "AddressRecords": [{**addr_upper, "Status": "Current"}],
                }],
                "ConsumerAlertMessages": [],
                "OfacListPotentialMatches": [],
                "VerificationResponse": "Pass",
                "AccountResponseCode": "_1111",
                "CustomerResponseCode": "CA11",
                "FundsConfirmationResult": "Confirmed",
            }
        return {}

    if case == DOMESTIC_RANDOM:
        if tool_name == "ews_check":
            return {
                "CorrelationId": f"c1-demo-random-{_rand_digits(3)}",
                "SuccessResponse": {
                    "callTypeReceived": "BASIC_OWNERANDSTATUS",
                    "customerID": "USBTreas",
                    "accountNumber": request.get("account_number"),
                    "routingNumber": request.get("routing_number"),
                    "accountStatus": {"status": "OPEN", "additionalDetails": {"status": None, "message": "No issues - per rule set"}},
                    "accountOwnerMatch": {
                        "conditionCode": "000", "conditionCodeMessage": "Normal return-no system errors.",
                        "nameMatch": None, "firstNameMatch": "Y", "lastNameMatch": "Y", "middleNameMatch": None,
                        "businessNameMatch": None, "fullAddressMatch": None, "addressMatch": "Y",
                        "cityMatch": "Y",
                        "stateMatch": "N",
                        "zipMatch": "Y",
                        "homePhoneMatch": "Y", "workPhoneMatch": None, "ssnMatch": None, "dateOfBirthMatch": None,
                        "IDTypeMatch": None, "IDNumberMatch": None, "IDStateMatch": None,
                        "overallMatchScore": _jitter_int(91, 4, lo=85, hi=97),
                        "signerOwnerFlag": None,
                    },
                },
                "ErrorResponse": None,
                "IsSuccess": True,
                "AVSResponseCode": True,
                "ReasonPhrase": "OK",
            }
        if tool_name == "giact_verify":
            return {
                "MatchedPersonData": [{
                    "FirstName": first_upper, "LastName": last_upper,
                    "AddressRecords": [{**addr_upper, "Status": "Current"}],
                }],
                "ConsumerAlertMessages": [],
                "OfacListPotentialMatches": [],
                "VerificationResponse": "Pass",
                "AccountResponseCode": "_1111",
                "CustomerResponseCode": "CA11",
                "FundsConfirmationResult": "Confirmed",
            }
        if tool_name == "ekata_identity_check":
            return {
                "identity_check_score": _jitter_int(438, 35, lo=380, hi=495),
                "identity_network_score": _jitter_float(0.10, 0.05, lo=0.0, hi=0.3),
                "primary_phone_checks": {
                    "match_to_name": "Match", "is_prepaid": False,
                    "match_to_address": "Match", "line_type": "Mobile",
                },
                "primary_address_checks": {
                    "match_to_name": "Match", "is_commercial": False,
                    "is_forwarder": False, "type": "Residential",
                },
                "primary_email_address_checks": {
                    "match_to_name": "Match", "is_disposable": False,
                    "is_autogenerated": False, "mailbox_velocity": 1,
                    "email_risk_score": _jitter_float(0.06, 0.03, lo=0.0, hi=0.2),
                },
                "ip_address_checks": {"proxy_risk": False, "error": None, "warnings": []},
                "alerts": [],
            }
        return {}

    if case == INTERNATIONAL_MATCH:
        if tool_name == "giact_verify":
            return {
                "MatchedPersonData": [{
                    "FirstName": first_upper, "LastName": last_upper,
                    "AddressRecords": [{
                        "AddressLine1": (addr.get("address_line1") or "").upper(),
                        "City": (addr.get("city") or "").upper(),
                        "State": addr.get("state") or "", "ZipCode": addr.get("zip_code") or "",
                        "Status": "Current",
                    }],
                }],
                "ConsumerAlertMessages": [],
                "OfacListPotentialMatches": [],
                "VerificationResponse": "Pass",
                "AccountResponseCode": "_1111",
                "CustomerResponseCode": "CA11",
                "FundsConfirmationResult": "Confirmed",
            }
        return {}

    if case == ONBOARDING_MATCH:
        if tool_name == "ekata_identity_check":
            return {
                "identity_check_score": _jitter_int(460, 30, lo=400, hi=498),
                "identity_network_score": _jitter_float(0.12, 0.05, lo=0.0, hi=0.3),
                "primary_phone_checks": {
                    "match_to_name": "Match", "is_prepaid": False,
                    "match_to_address": "Match", "line_type": "Mobile",
                },
                "primary_address_checks": {
                    "match_to_name": "Match", "is_commercial": False,
                    "is_forwarder": False, "type": "Residential",
                },
                "primary_email_address_checks": {
                    "match_to_name": "Match", "is_disposable": False,
                    "is_autogenerated": False, "mailbox_velocity": 1,
                    "email_risk_score": _jitter_float(0.05, 0.03, lo=0.0, hi=0.2),
                },
                "ip_address_checks": {"proxy_risk": False, "error": None, "warnings": []},
                "alerts": [],
            }
        return {}

    return {}