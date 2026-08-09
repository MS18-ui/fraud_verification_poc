"""
demo_scenarios.py

Fixed, deterministic request/response bundles for the client-demo
scenarios, grouped by transfer type (see engine/waterfall.py:
classify_transfer_type() for how a request's type itself is determined --
domestic vs. international vs. onboarding, from swift_code/routing_number/
account_number presence, no internal tag read).

Within a transfer type, which SPECIFIC scenario a request represents is
ALSO derived purely from its own field values -- never a hidden flag:

  Domestic (EWS -> LSEG, falls through on no-data/outage)
    1. Direct match       -- EWS answers, single source
    2. Virtual account     -- account_number starts with "VA-" -> EWS has
                              no data, falls through to LSEG
    3. EWS outage           -- routing_number == the reserved sentinel
                              below -> EWS call fails outright, falls
                              through to LSEG (automatic, no manual step)
    4. Random (all vendors) -- routing_number == RANDOM_ALL_VENDORS_SENTINEL
                              -> EWS, LSEG, AND Ekata are all called
                              (engine/waterfall.py's call_all mode) so
                              every vendor's own response/score is visible
                              side by side. Still terminates at EWS (the
                              first success) for canonical/composite
                              purposes -- the extra two calls are for
                              transparency, they don't change the decision.

  International (LSEG only, no fallback)
    5. Match                -- swift_code == DOMESTIC_BIC_MATCH -> LSEG
                              has the record
    6. No data              -- swift_code == DOMESTIC_BIC_NO_DATA -> LSEG
                              has nothing -> decline (see pipeline.py)

  Onboarding (Ekata only -- no account/routing number exists yet)
    7. Match                -- Ekata's own phone/email/address checks
                              corroborate the applicant

These are intentionally NOT drawn from the random synthetic generator
suite (data/generators/) -- a live client demo can't depend on a random
draw landing in the right match category every run.

engine/vendor_gateway.py:mcp_call() serves these directly (in DEMO_MODE)
when the caller passes demo_scenario=True -- a plain function argument,
not a field in the request -- ahead of the normal random-generator path.
"""

from __future__ import annotations
from typing import Any

from engine.waterfall import classify_transfer_type, DOMESTIC, INTERNATIONAL, ONBOARDING

# --- Domestic sub-case markers -- real field patterns, not hidden flags ---
VIRTUAL_ACCOUNT_PREFIX = "VA-"
EWS_OUTAGE_SENTINEL_ROUTING_NUMBER = "000000000"  # not a real ABA routing number
RANDOM_ALL_VENDORS_SENTINEL_ROUTING_NUMBER = "123456789"  # not a real ABA routing number

# --- International sub-case marker -- which BIC the request carries ---
INTERNATIONAL_BIC_MATCH = "DEUTGB2LXXX"     # fictional, Deutsche Bank London-style BIC

DOMESTIC_DIRECT = "domestic_direct"
VIRTUAL_ACCOUNT_NO_EWS = "virtual_account_no_ews"
EWS_OUTAGE = "ews_outage"
DOMESTIC_RANDOM = "domestic_random"
INTERNATIONAL_MATCH = "international_match"
ONBOARDING_MATCH = "onboarding_match"


def classify_scenario(request: dict) -> str:
    """Full scenario classification -- transfer type first, then the
    specific sub-case within it, all from the request's own fields."""
    transfer_type = classify_transfer_type(request)

    if transfer_type == DOMESTIC:
        routing_number = (request.get("routing_number") or "").strip()
        account_number = (request.get("account_number") or "").strip().upper()
        if routing_number == EWS_OUTAGE_SENTINEL_ROUTING_NUMBER:
            return EWS_OUTAGE
        if routing_number == RANDOM_ALL_VENDORS_SENTINEL_ROUTING_NUMBER:
            return DOMESTIC_RANDOM
        if account_number.startswith(VIRTUAL_ACCOUNT_PREFIX):
            return VIRTUAL_ACCOUNT_NO_EWS
        return DOMESTIC_DIRECT

    if transfer_type == INTERNATIONAL:
        # Only one international scenario now (match) -- the no-data/
        # decline case was removed on request. The underlying no-fallback
        # decline logic in pipeline.py stays in place (still correct
        # architecture: international genuinely has no fallback route),
        # it just has no demo fixture exercising it anymore.
        return INTERNATIONAL_MATCH

    return ONBOARDING_MATCH


# ---------------------------------------------------------------------------
# Request builders
# ---------------------------------------------------------------------------

def build_domestic_direct_request() -> dict[str, Any]:
    return {
        "unique_id": "ORQ-5887812204",
        "first_name": "Michael", "last_name": "Torres", "name": "Michael Torres",
        "ssn": "512-33-8841", "dob": "1985-04-12",
        "routing_number": "071000013", "account_number": "4471098823",
        "account_type": "Checking",
        "address": {
            "address_line1": "1420 Wexford Lane", "city": "Minneapolis",
            "state": "MN", "zip_code": "55401", "country": "US",
        },
        "phone": "6125557734", "email": "michael.torres@example.com",
    }


def build_virtual_account_request() -> dict[str, Any]:
    return {
        "unique_id": "ORQ-5887819647",
        "first_name": "Elena", "last_name": "Whitfield", "name": "Elena Whitfield",
        "ssn": "488-71-2205", "dob": "1990-09-03",
        "routing_number": "091000019", "account_number": "VA-887702291",
        "account_type": "Checking",
        "address": {
            "address_line1": "88 Harborview Plaza", "city": "Austin",
            "state": "TX", "zip_code": "78701", "country": "US",
        },
        "phone": "5125559981", "email": "elena.whitfield@example.com",
    }


def build_ews_outage_request() -> dict[str, Any]:
    return {
        "unique_id": "ORQ-5887825981",
        "first_name": "Priya", "last_name": "Natarajan", "name": "Priya Natarajan",
        "ssn": "603-29-4471", "dob": "1988-11-22",
        "routing_number": EWS_OUTAGE_SENTINEL_ROUTING_NUMBER,
        "account_number": "8820147765",
        "account_type": "Checking",
        "address": {
            "address_line1": "212 Larkspur Court", "city": "Denver",
            "state": "CO", "zip_code": "80202", "country": "US",
        },
        "phone": "3035557712", "email": "priya.natarajan@example.com",
    }


def build_domestic_random_request() -> dict[str, Any]:
    """EWS, LSEG, and Ekata are all called (engine/waterfall.py's call_all
    mode) -- terminates at EWS (the first success) for canonical/composite
    purposes, same as any other EWS-answering domestic case; the other two
    calls exist purely to show each vendor's own response side by side."""
    return {
        "unique_id": "ORQ-5887851193",
        "first_name": "Taylor", "last_name": "Brooks", "name": "Taylor Brooks",
        "ssn": "521-44-7793", "dob": "1991-07-14",
        "routing_number": RANDOM_ALL_VENDORS_SENTINEL_ROUTING_NUMBER,
        "account_number": "5540098217",
        "account_type": "Checking",
        "address": {
            "address_line1": "64 Foundry Row", "city": "Columbus",
            "state": "OH", "zip_code": "43215", "country": "US",
        },
        "phone": "6145557719", "email": "taylor.brooks@example.com",
    }


def build_international_match_request() -> dict[str, Any]:
    return {
        "unique_id": "ORQ-5887831402",
        "first_name": "Sofia", "last_name": "Bergman", "name": "Sofia Bergman",
        "dob": "1987-06-18",
        "swift_code": INTERNATIONAL_BIC_MATCH,
        "account_number": "GB29NWBK60161331926819",  # IBAN-style
        "account_type": "Checking",
        "address": {
            "address_line1": "14 Canary Wharf", "city": "London",
            "state": "", "zip_code": "E14 5AB", "country": "GB",
        },
        "phone": "+442071838750", "email": "sofia.bergman@example.com",
    }


def build_onboarding_request() -> dict[str, Any]:
    """No routing_number, no account_number, no swift_code -- there's no
    bank account yet. Identity fields only."""
    return {
        "unique_id": "ORQ-5887844290",
        "first_name": "Grace", "last_name": "Kimani", "name": "Grace Kimani",
        "ssn": "409-55-1187", "dob": "1994-10-05",
        "address": {
            "address_line1": "77 Meridian Ave", "city": "Seattle",
            "state": "WA", "zip_code": "98101", "country": "US",
        },
        "phone": "2065557743", "email": "grace.kimani@example.com",
    }


# ---------------------------------------------------------------------------
# Canned vendor responses per scenario. Fraud-relevant fields
# (fraud_history_found, OfacListPotentialMatches, ConsumerAlertMessages,
# ip_proxy_risk, email is_disposable, etc.) are always present explicitly --
# even when clean -- so scoring/fraud_model.py is genuinely reading real
# vendor-shaped attributes for whichever vendor answered, not defaulting on
# absence.
# ---------------------------------------------------------------------------

def get_demo_response(tool_name: str, request: dict) -> dict[str, Any]:
    case = classify_scenario(request)

    if case == DOMESTIC_DIRECT:
        if tool_name == "ews_check":
            # Real AVS response shape (per the client's sample request/response,
            # not an invented schema) -- CorrelationId/SuccessResponse wrapper,
            # accountStatus + accountOwnerMatch with a native overallMatchScore.
            return {
                "CorrelationId": "c1-demo-domestic-001",
                "SuccessResponse": {
                    "callTypeReceived": "BASIC_OWNERANDSTATUS",
                    "customerID": "USBTreas",
                    "accountNumber": "4471098823",
                    "routingNumber": "071000013",
                    "accountStatus": {"status": "OPEN", "additionalDetails": {"status": None, "message": "No issues - per rule set"}},
                    "accountOwnerMatch": {
                        "conditionCode": "000", "conditionCodeMessage": "Normal return-no system errors.",
                        "nameMatch": None, "firstNameMatch": "Y", "lastNameMatch": "Y", "middleNameMatch": None,
                        "businessNameMatch": None, "fullAddressMatch": None, "addressMatch": "Y",
                        "cityMatch": "Y", "stateMatch": "Y", "zipMatch": "Y",
                        "homePhoneMatch": "Y", "workPhoneMatch": None, "ssnMatch": None, "dateOfBirthMatch": None,
                        "IDTypeMatch": None, "IDNumberMatch": None, "IDStateMatch": None,
                        "overallMatchScore": 96, "signerOwnerFlag": None,
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
            # Real AVS shape, "no data" case -- no confirmed sample for this
            # specific outcome was provided; accountStatus/accountOwnerMatch
            # left unpopulated (None) is the most defensible inference from
            # the confirmed shape, not a documented "no data" response.
            return {
                "CorrelationId": "c1-demo-virtual-002",
                "SuccessResponse": {
                    "callTypeReceived": "BASIC_OWNERANDSTATUS",
                    "customerID": "USBTreas",
                    "accountNumber": "887702291",
                    "routingNumber": "091000019",
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
                    "FirstName": "ELENA", "LastName": "WHITFIELD",
                    "AddressRecords": [{
                        "AddressLine1": "88 HARBORVIEW PLAZA", "City": "AUSTIN",
                        "State": "TX", "ZipCode": "78701", "Status": "Current",
                    }],
                }],
                "ConsumerAlertMessages": [],
                "OfacListPotentialMatches": [],
                # Real GIACT PostInquiry fields (per the GIACT Verification
                # Services API sample doc, v5.86) -- GIACT is a CODE vendor,
                # no native numeric score exists in its real responses.
                "VerificationResponse": "Pass",
                "AccountResponseCode": "_1111",
                "CustomerResponseCode": "CA11",
                "FundsConfirmationResult": "Confirmed",
            }
        return {}

    if case == EWS_OUTAGE:
        # ews_check is never actually reached -- vendor_gateway raises
        # VendorUnavailable before this function is even called, because
        # classify_transfer_type/classify_scenario read the sentinel
        # routing number straight off the request.
        if tool_name == "giact_verify":
            return {
                "MatchedPersonData": [{
                    "FirstName": "PRIYA", "LastName": "NATARAJAN",
                    "AddressRecords": [{
                        "AddressLine1": "212 LARKSPUR COURT", "City": "DENVER",
                        "State": "CO", "ZipCode": "80202", "Status": "Current",
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

    if case == DOMESTIC_RANDOM:
        # All three vendors are called (engine/waterfall.py's call_all
        # mode, requested by pipeline.py for this scenario only) -- each
        # gets its own genuine success-shaped response, same real field
        # structures as every other scenario, just all populated at once
        # instead of stop-on-success skipping the later ones.
        if tool_name == "ews_check":
            return {
                "CorrelationId": "c1-demo-random-001",
                "SuccessResponse": {
                    "callTypeReceived": "BASIC_OWNERANDSTATUS",
                    "customerID": "USBTreas",
                    "accountNumber": "5540098217",
                    "routingNumber": RANDOM_ALL_VENDORS_SENTINEL_ROUTING_NUMBER,
                    "accountStatus": {"status": "OPEN", "additionalDetails": {"status": None, "message": "No issues - per rule set"}},
                    "accountOwnerMatch": {
                        "conditionCode": "000", "conditionCodeMessage": "Normal return-no system errors.",
                        "nameMatch": None, "firstNameMatch": "Y", "lastNameMatch": "Y", "middleNameMatch": None,
                        "businessNameMatch": None, "fullAddressMatch": None, "addressMatch": "Y",
                        "cityMatch": "Y", "stateMatch": "N", "zipMatch": "Y",
                        "homePhoneMatch": "Y", "workPhoneMatch": None, "ssnMatch": None, "dateOfBirthMatch": None,
                        "IDTypeMatch": None, "IDNumberMatch": None, "IDStateMatch": None,
                        "overallMatchScore": 91, "signerOwnerFlag": None,
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
                    "FirstName": "TAYLOR", "LastName": "BROOKS",
                    "AddressRecords": [{
                        "AddressLine1": "64 FOUNDRY ROW", "City": "COLUMBUS",
                        "State": "OH", "ZipCode": "43215", "Status": "Current",
                    }],
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
                "identity_check_score": 438,
                "identity_network_score": 0.10,
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
                    "email_risk_score": 0.06,
                },
                "ip_address_checks": {"proxy_risk": False, "error": None, "warnings": []},
                "alerts": [],
            }
        return {}

    if case == INTERNATIONAL_MATCH:
        if tool_name == "giact_verify":
            return {
                "MatchedPersonData": [{
                    "FirstName": "SOFIA", "LastName": "BERGMAN",
                    "AddressRecords": [{
                        "AddressLine1": "14 CANARY WHARF", "City": "LONDON",
                        "State": "", "ZipCode": "E14 5AB", "Status": "Current",
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
                "identity_check_score": 460,
                "identity_network_score": 0.12,
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
                    "email_risk_score": 0.05,
                },
                "ip_address_checks": {"proxy_risk": False, "error": None, "warnings": []},
                "alerts": [],
            }
        return {}

    return {}
