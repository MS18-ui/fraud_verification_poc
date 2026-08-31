"""
Synthetic GIACT-style response generator — all highlighted sample sections.

Given an Inquiry (service flags + Check + Customer), produces a fully
synthetic PostInquiryResult-shaped JSON response mirroring the GIACT
Verification Services API (v5.86) XML samples, converted to JSON per
Modnish's preference.

Covered sections (per GIACT_Key_Inventory.xlsx — the 12 highlighted TOC
sections, Samples 06, 07, 13-22):
- gVerify with gIdentify (Person / Business)
- gAuthenticate with gIdentify (plain / KBA / ESI)
- gOFAC (Person and Business)
- gIdentify (Person / Business / KBA / ESI / IP Address / Domain WHOIS)

Which response sections appear is driven by the request's service flags
(GVerifyEnabled, GIdentifyEnabled, GIdentifyKbaEnabled, GIdentifyEsiEnabled,
GAuthenticateEnabled, OfacScanEnabled, IpAddressInformationEnabled,
DomainWhoisEnabled) and whether the Customer is a person (FirstName/LastName)
or a business (BusinessName) — exactly like the real API. Fields that the
real API renders as xsi:nil come back as JSON null.

Design notes
------------
- Field-level generation borrows the pool/Faker approach from Shiven Sharma's
  Synthetic-data-generator (synth_agent/generators/text_generator.py), but the
  nested assembly is our own: GIACT responses are hierarchical (0-10 person
  records, each with 0-10 addresses and 0-10 phones), which doesn't fit the
  flat CanonicalSchema -> CSV model.
- Two generation modes (generate_post_inquiry_result's `match_flag` argument,
  also readable from a MatchFlag field on the Inquiry itself):
  * match_flag=None — every value is random; nothing from the request appears
    in the response (the original mock behavior, kept for ad-hoc requests
    that don't ask for a specific outcome).
  * match_flag 0 / 1 / 2 (complete / partial / no match) — the response is
    PRODUCED at generation time in the requested category, using the request
    itself as the source data. No stored truth identity or comparison step:
      0 complete match — the first matched record echoes the request's
        Customer data; account codes come from a Pass pool;
        CustomerResponseCode CA11/CI11.
      1 partial match — the first record overlaps with the request but
        deviates in a controlled subset (e.g. same street name, different
        house number; or a different phone); CA/CI 2x codes,
        AcceptWithRisk/RiskAlert.
      2 no match — records (if any) are a different identity entirely; with
        a Check the story is "real account, wrong owner" (Declined CA01/CI01),
        without one an unknown person (ND02/NoData).
    Every list section still carries multiple entries: the flag-derived
    entry comes first (record lists, and the address/phone/name/contact
    lists inside each matched record) and the rest are random filler — so a
    match means "at least one entry matches the request", not a single-entry
    echo.
    OFAC hits still fire only for watch-list names, consumer alerts only on
    deviated or flagged inputs, and documented sandbox routing/account pairs
    echo their documented AccountResponseCode.
- Repeat counts default to randint(0, 10) per meeting decision (random mode).
- XML list wrappers (e.g. NameRecords > BusinessNameRecord) become plain JSON
  arrays, matching how MatchedPersonData was already rendered.

Realism layer (added for the labelled dataset)
----------------------------------------------
The original meeting decision was "entire response would be randomly synthetic
data", with addresses random across the country. That is fine for exercising
the API but produces two problems for analytics:

- Geography and dates never agreed with each other (`PORT TIMOTHY, UT 61685`,
  area code `199`, a 2005 DOB against a 1973 SSN issue year), because City,
  State, ZipCode and area code were each drawn independently.
- Responses were uncorrelated with requests — every outcome was a flat
  `random.choice(...)` — so a dataset built from them carries no signal.

Both are addressed without changing the default behaviour of the mock:

- Coherent values now come from `data.reference`: one `Market` is chosen
  per record and city/state/ZIP/area-code/lat-long all derive from it.
- `SCENARIO_RESOLVER` is an optional hook. When it is `None` (the default, and
  what `mock_api` uses) every request draws from the `clean` profile exactly as
  before. `data/drive.py` installs a resolver so a labelled corpus can bias
  outcomes by fraud typology.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from faker import Faker

from data import reference as refdata
from data import scenarios
from request_generator import (ALERT_TAX_IDS, CATEGORY_ACCOUNT_CODES,
                               FRAUD_TEST_EMAILS, MATCH_CATEGORIES,
                               OFAC_BUSINESSES, OFAC_PERSONS,
                               SANDBOX_ACCOUNT_CODES, SANDBOX_BANK_NAME,
                               SANDBOX_ROUTING)

fake = Faker("en_US")

# Match flag values (MatchFlag on the Inquiry / the match_flag argument).
COMPLETE_MATCH, PARTIAL_MATCH, NO_MATCH = 0, 1, 2
MATCH_FLAGS = (COMPLETE_MATCH, PARTIAL_MATCH, NO_MATCH)

# ── Scenario hook ───────────────────────────────────────────────────────────

#: Optional `Callable[[dict], Optional[str]]` mapping an Inquiry to a risk
#: profile name from `data.scenarios.PROFILES`. `None` (the default) means
#: every request is treated as `clean`, preserving the mock's original
#: behaviour. This mirrors how the real GIACT sandbox works, where specific
#: account numbers map to specific response codes.
SCENARIO_RESOLVER: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None


def _profile_for(inquiry: Dict[str, Any]) -> scenarios.RiskProfile:
    """Resolve the risk profile governing this inquiry's response."""
    if SCENARIO_RESOLVER is None:
        return scenarios.CLEAN
    return scenarios.profile_for(SCENARIO_RESOLVER(inquiry))


#: VerificationResponse severity, best to worst. Used to reconcile the account
#: outcome with the customer outcome when a request enables both services.
_OUTCOME_SEVERITY = [
    "Pass", "PassNdd", "NoData", "AcceptWithRisk", "RejectItem",
    "NegativeData", "PrivateBadChecksList", "RiskAlert", "Declined", "Error",
]


def _worst_outcome(a: str, b: str) -> str:
    """The more severe of two VerificationResponse values."""
    rank = {v: i for i, v in enumerate(_OUTCOME_SEVERITY)}
    return a if rank.get(a, 0) >= rank.get(b, 0) else b


def _set_input_address_current(
    record: Dict[str, Any],
    customer: Dict[str, Any],
) -> None:
    """
    Put the submitted address on the record as its Current address.

    When the bureau's file really does contain the address we asked about,
    that address should come back verbatim rather than as an unrelated one —
    this is what makes `AddressRecords[].Status == "Current"` comparable to the
    input, and hence usable as a feature.
    """
    if not customer.get("AddressLine1"):
        return
    for existing in record.get("AddressRecords", []):
        if existing.get("Status") == "Current":
            existing["Status"] = "Previous"
    record.setdefault("AddressRecords", []).insert(0, {
        "AddressLine1": customer["AddressLine1"].upper(),
        "AddressLine2": (customer.get("AddressLine2") or "").upper(),
        "City": (customer.get("City") or "").upper(),
        "State": (customer.get("State") or "").upper(),
        "ZipCode": customer.get("ZipCode") or "",
        "Status": "Current",
        "DateReported": _past_datetime(2000),
        "ParsedAddressLine1": _parse_address_line(customer["AddressLine1"]),
    })


def _parse_address_line(line: str) -> Dict[str, str]:
    """
    Best-effort split of a street line into GIACT ParsedAddressLine components,
    so the parsed block agrees with the AddressLine1 it describes.
    """
    tokens = line.upper().split()
    parsed = {"StreetNumber": "", "PreDirectional": "", "StreetName": "",
              "StreetSuffix": "", "PostDirectional": "", "UnitType": "",
              "UnitIdentifier": ""}
    if not tokens:
        return parsed
    if tokens[0].isdigit():
        parsed["StreetNumber"] = tokens.pop(0)
    dirs = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}
    if tokens and tokens[0] in dirs:
        parsed["PreDirectional"] = tokens.pop(0)
    if tokens and tokens[-1] in dirs and len(tokens) > 1:
        parsed["PostDirectional"] = tokens.pop()
    if tokens and tokens[-1] in STREET_SUFFIXES and len(tokens) > 1:
        parsed["StreetSuffix"] = tokens.pop()
    parsed["StreetName"] = " ".join(tokens)
    return parsed



# ── Enumerations & code pools (from GIACT Integration Guide / Samples) ──────

# All 18 documented AccountResponseCodes. The previous pool carried _1121 and
# _9999 (in neither the guide's table nor the sandbox list) and omitted GN01,
# GN05, GS03, GS04, RT02, RT03 and RT04.
ACCOUNT_RESPONSE_CODES = list(scenarios.ACCOUNT_CODE_OUTCOMES)
# CustomerResponseCode pools by service (Integration Guide §CustomerResponseCode).
# The previous gIdentify pool carried undocumented CI31/CI41.
CUSTOMER_CODES_GAUTHENTICATE = [c for c in scenarios.CUSTOMER_CODE_OUTCOMES
                                if c.startswith("CA")]
CUSTOMER_CODES_GIDENTIFY = [c for c in scenarios.CUSTOMER_CODE_OUTCOMES
                            if c.startswith("CI") or c == "ND02"]
# All 10 VerificationResponse_5_8 values; the previous pool omitted
# PrivateBadChecksList, RejectItem, PassNdd and NegativeData.
VERIFICATION_RESPONSES = [
    "Pass", "PassNdd", "AcceptWithRisk", "RiskAlert", "Declined", "RejectItem",
    "PrivateBadChecksList", "NegativeData", "NoData", "Error",
]
ACCOUNT_TYPES = ["Checking", "Savings", "Unknown"]
ADDRESS_STATUSES = ["Current", "Previous", "SecondPrevious", "Other"]
PHONE_CLASSIFICATIONS = ["Personal", "Business", "ResidentialBusiness", "Other"]
PHONE_NUMBER_TYPES = ["Standard", "Mobile", "VoIP", "Other"]
SSN_STATUSES = refdata.SSN_STATUSES
STREET_SUFFIXES = refdata.STREET_SUFFIXES
DIRECTIONALS = refdata.DIRECTIONALS
UNIT_TYPES = refdata.UNIT_TYPES
# Real ConsumerAlertMessage codes are numeric (101-533). The previous CA01-CA08
# keys were invented and collided with real CustomerResponseCode values.
CONSUMER_ALERT_CODES = scenarios.CONSUMER_ALERT_CODES

STATE_ABBRS = refdata.STATES

BUSINESS_NAME_TYPES = ["PRIMARY", "DBA", "TRADE NAME"]
CORPORATION_TYPES = ["CORPORATION", "LLC", "PARTNERSHIP", "SOLE PROPRIETORSHIP"]
REGISTRATION_TYPES = [
    "LIMITED LIABILITY COMPANY", "DOMESTIC CORPORATION",
    "FOREIGN CORPORATION", "LIMITED PARTNERSHIP",
]
INDUSTRIES = [
    "MARKETING CONSULTING", "SOFTWARE DEVELOPMENT", "ACCOUNTING SERVICES",
    "REAL ESTATE", "CONSTRUCTION", "RETAIL TRADE", "FOOD SERVICES",
    "TRANSPORTATION", "HEALTH CARE SERVICES", "LEGAL SERVICES",
]
CONTACT_TITLES = [
    "Managing Member", "President", "CEO", "CFO", "Tax Accountant",
    "Registered Agent", "Owner", "Director", "Secretary", "Treasurer",
]

OFAC_MATCH_LEVELS = {
    "Exact": ["Full name was an exact match."],
    "High": ["FirstName and LastName matched with additional characters/names between them."],
    "Conditional": ["List name partially matched input name."],
}
OFAC_PROGRAM_NAMES = ["SDNTK", "SDGT", "SDN", "IRAN", "CUBA", "SYRIA", "DPRK"]
OFAC_DATA_SOURCES = ["US OFAC", "US OFAC", "US OFAC", "UN Sanctions", "EU Sanctions"]
OFAC_COUNTRIES = [
    "Lebanon", "Colombia", "Syria", "Iran", "Panama", "Venezuela",
    "North Korea", "Cuba", "Russia", "Nigeria",
]

KBA_QUESTION_THEMES = [
    "county", "zip", "street", "dob_sum", "ssn_state", "ssn_first3", "ssn_last4",
]

EMAIL_STATUSES = [
    (14, "Email Created at least 5 Years Ago"),
    (13, "Email Created 2 to 5 Years Ago"),
    (12, "Email Created 1 to 2 Years Ago"),
    (11, "Email Created within last 12 Months"),
    (5, "Email First Seen more than 2 Years Ago"),
    (2, "Email Address Not Found"),
]
EMAIL_ADVICE = ["LowerFraudRisk", "LowerFraudRisk", "ModerateFraudRisk", "HigherFraudRisk"]
DOMAIN_CATEGORIES = ["Webmail", "Corporate", "Education", "Government", "ISP"]
DOMAIN_RISKS = ["Low", "Moderate", "High"]
WEBMAIL_DOMAINS = [
    ("gmail.com", "Google"), ("yahoo.com", "Yahoo"), ("outlook.com", "Microsoft"),
    ("hotmail.com", "Microsoft"), ("aol.com", "AOL"), ("icloud.com", "Apple"),
]
SOCIAL_MEDIA_SITES = [
    "http://www.facebook.com/{slug}", "http://www.linkedin.com/in/{slug}",
    "http://www.twitter.com/{slug}", "http://www.instagram.com/{slug}",
]

IP_USER_TYPES = ["Cellular", "Residential", "Business", "College", "Government"]
IP_NET_SPEEDS = ["Cellular", "Broadband", "Cable/DSL", "Corporate", "Dialup"]
ISPS = [
    ("ATT Wireless", "att.com"), ("Comcast Cable", "comcast.net"),
    ("Verizon Fios", "verizon.net"), ("Charter Communications", "charter.com"),
    ("T-Mobile USA", "t-mobile.com"), ("Cox Communications", "cox.net"),
]

DOMAIN_STATUSES = [
    "clientTransferProhibited", "clientUpdateProhibited",
    "clientDeleteProhibited", "clientRenewProhibited", "ok",
]
REGISTRARS = [
    ("GoDaddy.com, LLC", "whois.godaddy.com"),
    ("NameCheap, Inc.", "whois.namecheap.com"),
    ("Network Solutions, LLC", "whois.networksolutions.com"),
    ("Tucows Domains Inc.", "whois.tucows.com"),
    ("MarkMonitor Inc.", "whois.markmonitor.com"),
]


# AccountResponseCode -> VerificationResponse (Integration Guide, restricted
# to the VerificationResponse vocabulary this mock emits).
ACCOUNT_CODE_VERIFICATION = {
    "_1111": "Pass", "_1121": "Pass", "_2222": "Pass", "_3333": "Pass",
    "_5555": "Pass", "_9999": "Pass",
    "RT04": "PassNdd",
    "ND00": "NoData", "ND01": "NoData", "GN05": "NoData",
    "GS01": "Declined", "GS02": "Declined", "GS03": "Declined",
    "GS04": "Declined", "RT00": "Declined", "RT01": "Declined",
    "RT02": "RejectItem", "RT03": "AcceptWithRisk",
    "GN01": "NegativeData", "GP01": "PrivateBadChecksList",
}

# Least to most severe; when several services run, the worst outcome wins
# (Integration Guide "How it's determined").
VERIFICATION_SEVERITY = [
    "Pass", "PassNdd", "NoData", "AcceptWithRisk", "RiskAlert",
    "NegativeData", "RejectItem", "Declined", "PrivateBadChecksList", "Error",
]


# ── Leaf-value helpers ───────────────────────────────────────────────────────

def _rand_count(lo: int = 0, hi: int = 10) -> int:
    """Repeat count for array-like sections. Meeting decision: 0-10."""
    return random.randint(lo, hi)


def _past_datetime(max_days: int = 4000) -> str:
    dt = datetime.now() - timedelta(days=random.randint(0, max_days))
    return dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _future_datetime(max_days: int = 1500) -> str:
    dt = datetime.now() + timedelta(days=random.randint(30, max_days))
    return dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _phone_parts(market: Optional[refdata.Market] = None) -> Dict[str, str]:
    """NANP-valid phone parts, using an area code that serves `market`."""
    return refdata.phone_parts_for(market)


def _business_name() -> str:
    corp_type = random.choice(CORPORATION_TYPES)
    return refdata.business_name_for(corp_type)["BusinessName"]


# ── Nested record builders (mirror GIACT classes) ───────────────────────────

def generate_parsed_address_line() -> Dict[str, str]:
    """ParsedAddressLine — components of AddressLine1."""
    unit_type = random.choice(UNIT_TYPES)
    # A real address carries at most one directional, not both a pre- and a
    # post-directional as the original produced ("4481 S SMITH ST" + "SE").
    pre, post = "", ""
    directional = random.choice(DIRECTIONALS)
    if directional:
        if random.random() < 0.8:
            pre = directional
        else:
            post = directional
    return {
        "StreetNumber": str(random.randint(1, 9999)),
        "PreDirectional": pre,
        "StreetName": random.choice(refdata.STREET_NAMES),
        "StreetSuffix": random.choice(STREET_SUFFIXES),
        "PostDirectional": post,
        "UnitType": unit_type,
        "UnitIdentifier": str(random.randint(1, 999)) if unit_type else "",
    }


def _address_lines(parsed: Dict[str, str]) -> Dict[str, str]:
    line1_parts = [parsed["StreetNumber"], parsed["PreDirectional"],
                   parsed["StreetName"], parsed["StreetSuffix"]]
    return {
        "AddressLine1": " ".join(p for p in line1_parts if p),
        "AddressLine2": (f"{parsed['UnitType']} {parsed['UnitIdentifier']}"
                         if parsed["UnitType"] else ""),
    }


def generate_residential_address_record(
    market: Optional[refdata.Market] = None,
    status: Optional[str] = None,
    truth_addr: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    ResidentialAddressRecord, in either generation mode.

    `truth_addr` echoes a known address verbatim — that is what lets a
    match-flag response put the requested address on the file it returns.
    Otherwise the address is drawn inside a real US market, so City, State and
    ZipCode agree with one another rather than being drawn independently.
    """
    if truth_addr is not None:
        line1 = truth_addr["AddressLine1"].upper()
        return {
            "AddressLine1": line1,
            "AddressLine2": "",
            "City": truth_addr["City"].upper(),
            "State": truth_addr["State"],
            "ZipCode": truth_addr["ZipCode"][:5],
            "Status": status or "Current",
            "DateReported": _past_datetime(),
            "ParsedAddressLine1": _parse_address_line(line1),
        }
    mkt = market or refdata.resolve_market()
    parsed = generate_parsed_address_line()
    return {
        **_address_lines(parsed),
        "City": mkt.city,
        "State": mkt.state,
        "ZipCode": refdata.zip_for(mkt),
        "Status": status or random.choice(ADDRESS_STATUSES),
        "DateReported": _past_datetime(),
        "ParsedAddressLine1": parsed,
    }


def generate_business_address_record(
    market: Optional[refdata.Market] = None,
) -> Dict[str, Any]:
    """BusinessAddressRecord — like the residential record, without
    Status/DateReported (per the Samples/Key Inventory)."""
    mkt = market or refdata.resolve_market()
    parsed = generate_parsed_address_line()
    return {
        **_address_lines(parsed),
        "City": mkt.city,
        "State": mkt.state,
        "ZipCode": refdata.zip_for(mkt),
        "ParsedAddressLine1": parsed,
    }


def generate_phone_number_information(
    market: Optional[refdata.Market] = None,
    number: Optional[str] = None,
) -> Dict[str, Any]:
    """PhoneNumberInformation.

    `number` echoes a known 10-digit number verbatim; otherwise the parts are
    drawn from `market`'s own area codes, so the number belongs to the metro
    the rest of the record sits in.
    """
    if number is not None and len(number) == 10:
        parts = {"AreaCode": number[:3], "Exchange": number[3:6],
                 "Suffix": number[6:], "PhoneNumber": number}
    else:
        parts = _phone_parts(market)
    return {
        "Classification": random.choice(PHONE_CLASSIFICATIONS),
        "NumberType": random.choice(PHONE_NUMBER_TYPES),
        **parts,
    }


def generate_person_data_record(
    n_addresses: Optional[int] = None,
    n_phones: Optional[int] = None,
    market: Optional[refdata.Market] = None,
    profile: Optional[scenarios.RiskProfile] = None,
    customer: Optional[Dict[str, Any]] = None,
    truth: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    PersonDataRecord — one matched consumer record, in either mode.

    `truth` (the flag identity) makes the record echo that known person:
    name and DOB come from it, and the address/phone lists carry the truth
    entry FIRST (the current address / primary phone) padded with random
    filler entries — multiple entries per list, at least one matching. SSN
    issue details stay synthetic and SsnStatus reads clear.

    Otherwise `customer`, when given, is the submitted Customer whose identity
    this record should echo — a real bureau match returns the person you asked
    about, not an unrelated one — and `market` anchors every address and phone
    to one metro so the record's geography is self-consistent.
    """
    if truth is not None:
        issue_start = random.randint(1960, 2005)
        person, phone = truth["person"], truth["phone"]
        addresses = [generate_residential_address_record(
            truth_addr=truth["address"], status="Current")]
        addresses += [
            generate_residential_address_record(
                status=random.choice(ADDRESS_STATUSES[1:]))
            for _ in range(_rand_count(1, 9))
        ]
        phones = [generate_phone_number_information(number=phone)]
        phones += [generate_phone_number_information()
                   for _ in range(_rand_count(1, 9))]
        return {
            "FirstName": person["FirstName"].upper(),
            "MiddleName": "",
            "LastName": person["LastName"].upper(),
            "DateOfBirth": f"{person['DateOfBirth']}T00:00:00",
            "SsnIssueState": random.choice(STATE_ABBRS),
            "SsnIssueStartYear": str(issue_start),
            "SsnIssueEndYear": str(issue_start + random.randint(0, 2)),
            "SsnStatus": "clear",
            "AddressRecords": addresses,
            "PhoneNumbers": phones,
        }

    prof = profile or scenarios.CLEAN
    mkt = market or refdata.resolve_market()
    cust = customer or {}

    birth = fake.date_of_birth(minimum_age=21, maximum_age=88)
    if cust.get("DateOfBirth"):
        try:
            birth = datetime.strptime(cust["DateOfBirth"][:10], "%Y-%m-%d").date()
        except ValueError:
            pass

    # SsnIssueState is where the number was issued — usually, but not always,
    # the state the person still lives in.
    issue_state = (mkt.state if random.random() < 0.65
                   else random.choice(STATE_ABBRS))

    n_addr = (n_addresses if n_addresses is not None
              else random.randint(*prof.n_addresses))
    n_ph = n_phones if n_phones is not None else random.randint(*prof.n_phones)

    # Exactly one address is Current; the rest are historical.
    addresses = []
    for i in range(n_addr):
        addresses.append(generate_residential_address_record(
            market=mkt if i == 0 or random.random() < 0.6 else None,
            status="Current" if i == 0 else random.choice(
                ["Previous", "Previous", "SecondPrevious", "Other"]),
        ))

    return {
        "FirstName": (cust.get("FirstName") or fake.first_name()).upper(),
        "MiddleName": random.choice(["", fake.first_name().upper()]),
        "LastName": (cust.get("LastName") or fake.last_name()).upper(),
        "DateOfBirth": birth.strftime("%Y-%m-%dT00:00:00"),
        "SsnIssueState": issue_state,
        **refdata.ssn_issue_years_for(birth.year),
        "SsnStatus": scenarios.weighted(prof.ssn_statuses),
        "AddressRecords": addresses,
        "PhoneNumbers": [
            generate_phone_number_information(mkt) for _ in range(n_ph)
        ],
    }


def _business_address_from_truth(truth_addr: Dict[str, str]) -> Dict[str, Any]:
    line1 = truth_addr["AddressLine1"].upper()
    return {
        "AddressLine1": line1,
        "AddressLine2": "",
        "City": truth_addr["City"].upper(),
        "State": truth_addr["State"],
        "ZipCode": truth_addr["ZipCode"][:5],
        "ParsedAddressLine1": _parse_address_line(line1),
    }


def generate_business_data_record(
    market: Optional[refdata.Market] = None,
    profile: Optional[scenarios.RiskProfile] = None,
    customer: Optional[Dict[str, Any]] = None,
    truth: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    BusinessDataRecord — one matched business record (gIdentify Business).

    With `truth`, the PRIMARY name, first address, first phone, and first
    contact echo the flag identity; each of those lists is padded with random
    filler entries (multiple entries, at least one matching) and the
    registration details stay synthetic.

    Otherwise CorporationType, RegistrationType, the entity suffix on the name
    and the industry are all drawn together so they agree: an "... LLC" is
    registered as a LIMITED LIABILITY COMPANY, not a DOMESTIC CORPORATION.
    """
    if truth is not None:
        primary_name = truth["business"]["BusinessName"].upper()
        person = truth["person"]
        name_records = [{"BusinessName": primary_name,
                         "BusinessType": "PRIMARY"}]
        name_records += [
            {"BusinessName": _business_name(),
             "BusinessType": random.choice(BUSINESS_NAME_TYPES[1:])}
            for _ in range(_rand_count(1, 2))
        ]
        addresses = [_business_address_from_truth(truth["address"])]
        addresses += [generate_business_address_record()
                      for _ in range(_rand_count(1, 9))]
        phones = [generate_phone_number_information(number=truth["phone"])]
        phones += [generate_phone_number_information()
                   for _ in range(_rand_count(1, 9))]
        contacts = [{
            "FirstName": person["FirstName"],
            "MiddleName": "",
            "LastName": person["LastName"],
            "Title": random.choice(CONTACT_TITLES),
        }]
        contacts += [
            {
                "FirstName": fake.first_name(),
                "MiddleName": random.choice(["", fake.first_name()]),
                "LastName": fake.last_name(),
                "Title": random.choice(CONTACT_TITLES),
            }
            for _ in range(_rand_count(1, 3))
        ]
        return {
            "NameRecords": name_records,
            "FEIN": str(random.randint(100_000_000, 999_999_999)),
            "DunsNumber": str(random.randint(100_000_000, 999_999_999)),
            "CorporationType": random.choice(CORPORATION_TYPES),
            "RegistrationType": random.choice(REGISTRATION_TYPES),
            "IncorporationState": truth["address"]["State"],
            "IncorporationDate": _past_datetime(9000),
            "FilingNumber": f"{random.randint(0, 9_999_999_999):010d}",
            "Industries": random.sample(INDUSTRIES, k=random.randint(1, 2)),
            "Domains": [truth["domain"].upper()] + [
                fake.domain_name().upper() for _ in range(_rand_count(0, 2))],
            "AddressRecords": addresses,
            "PhoneNumbers": phones,
            "BusinessContacts": contacts,
        }

    prof = profile or scenarios.CLEAN
    mkt = market or refdata.resolve_market()
    cust = customer or {}

    corp_type = random.choice(CORPORATION_TYPES)
    built = refdata.business_name_for(corp_type)
    primary_name = cust.get("BusinessName", "").upper() or built["BusinessName"]

    name_records = [{"BusinessName": primary_name, "BusinessType": "PRIMARY"}]
    for _ in range(_rand_count(0, 2)):
        name_records.append({
            "BusinessName": refdata.business_name_for(corp_type)["BusinessName"],
            "BusinessType": random.choice(BUSINESS_NAME_TYPES[1:]),
        })

    # Domain follows the trading name, as a real business registration would.
    slug = "".join(ch for ch in primary_name.split()[0] if ch.isalnum()).lower()
    industries = [built["Industry"]]
    for extra in random.sample(INDUSTRIES, k=random.randint(0, 2)):
        if extra not in industries:
            industries.append(extra)

    return {
        "NameRecords": name_records,
        "FEIN": str(random.randint(100_000_000, 999_999_999)),
        "DunsNumber": str(random.randint(100_000_000, 999_999_999)),
        "CorporationType": corp_type,
        "RegistrationType": built["RegistrationType"],
        "IncorporationState": mkt.state,
        "IncorporationDate": _past_datetime(
            random.randint(*prof.account_age_days) + 30
        ),
        "FilingNumber": f"{random.randint(0, 9_999_999_999):010d}",
        "Industries": industries,
        "Domains": [f"{slug}.com".upper()],
        "AddressRecords": [
            generate_business_address_record(mkt)
            for _ in range(random.randint(*prof.n_addresses))
        ],
        "PhoneNumbers": [
            generate_phone_number_information(mkt)
            for _ in range(random.randint(*prof.n_phones))
        ],
        "BusinessContacts": [
            {
                "FirstName": fake.first_name(),
                "MiddleName": random.choice(["", fake.first_name()]),
                "LastName": fake.last_name(),
                "Title": random.choice(CONTACT_TITLES),
            }
            for _ in range(_rand_count(0, 4))
        ],
    }


def generate_consumer_alert_messages(
    profile: Optional[scenarios.RiskProfile] = None,
) -> List[Dict[str, str]]:
    """
    ConsumerAlertMessages — drawn from the codes this risk profile tends to
    raise. A clean file mostly raises benign, high-frequency alerts; the fraud
    profiles bias toward the SSN/address/fraud-flag families.
    """
    prof = profile or scenarios.CLEAN
    n = random.randint(*prof.n_alerts)
    pool = list(prof.alert_codes)
    codes = random.sample(pool, k=min(n, len(pool)))
    return [scenarios.alert_message(c) for c in codes]


def generate_ofac_list_data(
    business: bool = False,
    matched_name: Optional[Tuple[str, str]] = None,
) -> Dict[str, Any]:
    """OfacListData — one watch-list potential match (person or business).

    `matched_name` echoes the input that hit the list: (first, last) for a
    person, (name, "") for a business — the match level is then Exact."""
    level = ("Exact" if matched_name is not None
             else random.choice(list(OFAC_MATCH_LEVELS)))
    countries = random.sample(OFAC_COUNTRIES, k=random.randint(1, 2))
    if business:
        list_name = (matched_name[0].upper() if matched_name
                     else _business_name())
        name = {"ListItemName": list_name, "FirstName": None,
                "MiddleName": None, "LastName": None}
        website = f"WWW.{list_name.split()[0]}.COM"
        tax_id = str(random.randint(100_000_000, 999_999_999))
        dob_listings: List[str] = []
        pob_listings: List[str] = []
        passports: List[str] = []
        akas = [_business_name() for _ in range(random.randint(0, 2))]
    else:
        if matched_name is not None:
            first, middle, last = (matched_name[0], "",
                                   matched_name[1].upper())
        else:
            first, middle, last = (fake.first_name(), fake.first_name(),
                                   fake.last_name().upper())
        name = {"ListItemName": " ".join(p for p in (first, middle, last) if p),
                "FirstName": first, "MiddleName": middle or None,
                "LastName": last}
        website = None
        tax_id = None
        dob_listings = [
            fake.date_of_birth(minimum_age=25, maximum_age=75).strftime("%d %b %Y")
            for _ in range(random.randint(1, 2))
        ]
        pob_listings = [f"{fake.city()}, {c}" for c in countries]
        passports = [
            f"{random.choice('PRLM')}{random.randint(100_000, 9_999_999)}"
            for _ in range(random.randint(0, 2))
        ]
        akas = [f"{fake.first_name()} {last}" for _ in range(random.randint(0, 3))]
    addresses = []
    for country in countries:
        addresses.append({
            "Address": random.choice([None, fake.street_address()]),
            "CityStatePost": random.choice([None, fake.city()]),
            "Country": country,
        })
    return {
        "EntityId": random.randint(1, 99999),
        "Summary": {
            "PotentialMatchLevel": level,
            "PotentialMatchDetails": list(OFAC_MATCH_LEVELS[level]),
            "PotentialMatchAka": level != "Exact" and random.random() < 0.5,
        },
        "Name": name,
        "DataSource": random.choice(OFAC_DATA_SOURCES),
        "ProgramNames": random.sample(OFAC_PROGRAM_NAMES, k=random.randint(1, 2)),
        "AlsoKnownAs": akas,
        "Associations": [],
        "DateOfBirthListings": dob_listings,
        "PlaceOfBirthListings": pob_listings,
        "Nationalities": countries,
        "NationalIds": ([f"Cedula No. {random.randint(10_000_000, 99_999_999)}"]
                        if not business and random.random() < 0.5 else []),
        "Citizenships": countries,
        "Passports": passports,
        "Website": website,
        "TaxIdNumber": tax_id,
        "Addresses": addresses,
        "LastUpdatedDate": _past_datetime(2000),
    }


def _kba_answers(theme: str) -> List[str]:
    if theme == "county":
        return [f"{fake.last_name().upper()}" for _ in range(9)]
    if theme == "zip":
        return [fake.postcode()[:5] for _ in range(9)]
    if theme == "street":
        return [f"{random.randint(1, 9999)} {fake.last_name().upper()} "
                f"{random.choice(STREET_SUFFIXES)}" for _ in range(9)]
    if theme == "ssn_state":
        return random.sample(STATE_ABBRS, k=9)
    # numeric sums (dob_sum, ssn_first3, ssn_last4)
    return [str(n) for n in random.sample(range(1, 44), k=9)]


KBA_QUESTION_TEXT = {
    "county": "Which one of the following counties is associated with you?",
    "zip": "Which one of the following zip codes is associated with you?",
    "street": "Which one of the following addresses is associated with you?",
    "dob_sum": "What is the sum of the month and day of your date of birth?",
    "ssn_state": "In which state was your Social Security Number issued?",
    "ssn_first3": "What is the sum of the first 3 digits of your Social Security Number?",
    "ssn_last4": "What is the sum of the last 4 digits of your Social Security Number?",
}


def generate_kba_result() -> List[Dict[str, Any]]:
    """GIdentifyKbaResult — 4-5 KnowledgeBasedAuthenticationQuestions, each
    with distractor answers, exactly one correct, 'NONE OF THE ABOVE' last
    (occasionally the correct answer, as seen in the samples)."""
    questions = []
    for theme in random.sample(KBA_QUESTION_THEMES, k=random.randint(4, 5)):
        answers = [{"Answer": a, "IsCorrect": False} for a in _kba_answers(theme)]
        none_correct = random.random() < 0.1
        if not none_correct:
            answers[random.randrange(len(answers))]["IsCorrect"] = True
        random.shuffle(answers)
        answers.append({"Answer": "NONE OF THE ABOVE", "IsCorrect": none_correct})
        questions.append({
            "Question": KBA_QUESTION_TEXT[theme],
            "PossibleAnswers": answers,
        })
    return questions


def generate_email_address_information_result(
    truth: Optional[Dict[str, Any]] = None,
    request_email: Optional[str] = None,
) -> Dict[str, Any]:
    """EmailAddressInformationResult (gIdentify ESI).

    With `truth`: EmailAddressFound reflects whether the request email is the
    truth person's email, the owner name echoes the truth person, and the
    advice is LowerFraudRisk on a match / HigherFraudRisk on a mismatch or a
    known fraud test email."""
    found_matches = None
    if truth is not None:
        email_matches = (request_email or "").strip().lower() == \
            truth["email"].strip().lower()
        fraudulent = (request_email or "").strip().lower() in \
            {e.lower() for e in FRAUD_TEST_EMAILS}
        found_matches = email_matches and not fraudulent
    status_id, status = random.choice(EMAIL_STATUSES)
    domain, company = random.choice(WEBMAIL_DOMAINS)
    owner_first, owner_last = fake.first_name(), fake.last_name()
    if truth is not None:
        person = truth["person"]
        owner_first, owner_last = person["FirstName"], person["LastName"]
        status_id, status = ((14, "Email Created at least 5 Years Ago")
                             if found_matches
                             else (2, "Email Address Not Found"))
        email_domain = (request_email or "").rpartition("@")[2] or domain
        webmail = {d: c for d, c in WEBMAIL_DOMAINS}
        domain, company = email_domain, webmail.get(email_domain,
                                                    "Unknown Provider")
    slug = f"{owner_first}{owner_last}"
    created = datetime.now() - timedelta(days=random.randint(400, 6000))
    first_seen = created + timedelta(days=random.randint(30, 700))
    last_seen = datetime.now() - timedelta(days=random.randint(0, 200))
    return {
        "EmailAddressFound": status_id != 2,
        "EmailAddressOwnerName": f"{owner_first} {owner_last}",
        "EmailAddressOwnerCompany": random.choice([None, _business_name().title()]),
        "EmailAddressOwnerTitle": random.choice([None] + CONTACT_TITLES),
        "EmailAddressOwnerPhoto": None,
        "EmailAddressStatus": status,
        "EmailAddressStatusId": status_id,
        "EmailAddressAdvice": (
            random.choice(EMAIL_ADVICE) if found_matches is None
            else "LowerFraudRisk" if found_matches else "HigherFraudRisk"),
        "EmailAddressCreatedDate": created.strftime("%Y-%m-%dT00:00:00"),
        "FirstSeen": first_seen.strftime("%Y-%m-%dT00:00:00"),
        "LastSeen": last_seen.strftime("%Y-%m-%dT00:00:00"),
        "Domain": domain,
        "DomainExists": True,
        "DomainCreatedDate": (datetime(1995, 1, 1) +
                              timedelta(days=random.randint(0, 7000))
                              ).strftime("%Y-%m-%dT00:00:00"),
        "DomainCompany": company,
        "DomainCountry": "United States",
        "DomainCategory": random.choice(DOMAIN_CATEGORIES),
        "DomainRisk": random.choice(DOMAIN_RISKS),
        "SocialMediaLinks": [
            site.format(slug=slug)
            for site in random.sample(SOCIAL_MEDIA_SITES,
                                      k=random.randint(0, len(SOCIAL_MEDIA_SITES)))
        ],
        "Fraud": None,
    }


def generate_ip_address_information_result(
    input_address_provided: bool,
    market: Optional[refdata.Market] = None,
    profile: Optional[scenarios.RiskProfile] = None,
    truth: Optional[Dict[str, Any]] = None,
    customer: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    IpAddressInformationResult. The Zip/City/State/Country match fields and
    Distance compare the IP location against the *input* address, so they are
    null when no address was supplied (IP-only variant in the samples).

    With `truth`, a request IP equal to the truth person's IP geolocates to
    their home city and an unknown IP gets a random location, so the match
    fields become real comparisons against the input address.

    Otherwise the geolocation resolves to a real metro with matching lat/long,
    and the match flags follow whether the IP landed in the same market as the
    input address rather than being independently random — a legitimate
    session usually geolocates near the customer, a hijacked one does not.
    """
    isp, isp_domain = random.choice(ISPS)
    if truth is not None:
        tip = truth["ip"]
        request_ip = (customer or {}).get("CurrentIpAddress")
        if request_ip == tip["Address"]:
            geo = {"Latitude": tip["Latitude"], "Longitude": tip["Longitude"],
                   "City": tip["City"], "StateRegion": tip["State"],
                   "ZipPostalCode": tip["ZipCode"]}
        else:
            geo = {"Latitude": round(float(fake.latitude()), 4),
                   "Longitude": round(float(fake.longitude()), 4),
                   "City": fake.city(),
                   "StateRegion": random.choice(STATE_ABBRS),
                   "ZipPostalCode": fake.postcode()[:5]}
        cust = customer or {}
        if input_address_provided:
            zip_match = geo["ZipPostalCode"] == (cust.get("ZipCode") or "")[:5]
            city_match = geo["City"].strip().lower() == \
                (cust.get("City") or "").strip().lower()
            state_match = geo["StateRegion"] == cust.get("State")
            distance = (random.randint(0, 15) if city_match
                        else random.randint(50, 500))
        else:
            zip_match = city_match = state_match = distance = None
        return {
            "Location": {
                **geo,
                "Country": "US",
                "AccuracyRadius": random.randint(1, 200),
                "Distance": distance,
                "ZipCodeMatch": zip_match,
                "CityMatch": city_match,
                "StateMatch": state_match,
                "CountryMatch": True if input_address_provided else None,
            },
            "UserType": random.choice(IP_USER_TYPES),
            "NetSpeed": random.choice(IP_NET_SPEEDS),
            "Domain": isp_domain,
            "ISP": isp,
            "Organization": isp,
            "CorporateProxy": False,
            "AnonymousProxy": False,
        }

    prof = profile or scenarios.CLEAN
    # Does the session geolocate to the customer's own market?
    same_market = random.random() < prof.address_match
    mkt = (market or refdata.resolve_market()) if same_market \
        else refdata.resolve_market()

    return {
        "Location": {
            **refdata.coords_for(mkt),
            "City": mkt.city.title(),
            "StateRegion": mkt.state,
            "ZipPostalCode": refdata.zip_for(mkt),
            "Country": "US",
            "AccuracyRadius": random.randint(1, 200),
            "Distance": (random.randint(0, 25) if same_market
                         else random.randint(50, 2500)) if input_address_provided else None,
            "ZipCodeMatch": (same_market and random.random() < 0.4)
                            if input_address_provided else None,
            "CityMatch": same_market if input_address_provided else None,
            "StateMatch": (same_market or random.random() < 0.15)
                          if input_address_provided else None,
            "CountryMatch": True if input_address_provided else None,
        },
        "UserType": random.choice(IP_USER_TYPES),
        "NetSpeed": random.choice(IP_NET_SPEEDS),
        "Domain": isp_domain,
        "ISP": isp,
        "Organization": isp,
        "CorporateProxy": random.random() < 0.1,
        "AnonymousProxy": random.random() < 0.05,
    }


def _generate_domain_contact(privacy: bool) -> Dict[str, Any]:
    """Name/Organization/Address/Phone/Email block shared by the Registrant,
    Admin, and Technical contacts of a DomainRegistry."""
    if privacy:
        name, org = "Registration Private", "Domains By Proxy, LLC"
        email_local = "REDACTED"
    else:
        name = f"{fake.first_name()} {fake.last_name()}"
        org = _business_name().title()
        email_local = name.replace(" ", ".").lower()
    return {
        "Name": name,
        "Organization": org,
        "Address": generate_business_address_record(),
        "Phone": generate_phone_number_information(),
        "EmailAddress": f"{email_local}@{fake.domain_name()}",
    }


def generate_domain_registry(domain: Optional[str] = None) -> Dict[str, Any]:
    """DomainRegistry (Domain WHOIS) — echoes the queried domain if given."""
    domain = (domain or fake.domain_name()).upper()
    registrar, whois_server = random.choice(REGISTRARS)
    privacy = random.random() < 0.5
    creation = datetime.now() - timedelta(days=random.randint(400, 8000))
    update = creation + timedelta(days=random.randint(30, 2000))
    result: Dict[str, Any] = {
        "DomainName": domain,
        "CreationDate": creation.strftime("%Y-%m-%dT00:00:00"),
        "UpdateDate": update.strftime("%Y-%m-%dT00:00:00"),
        "ExpirationDate": _future_datetime(),
        "Status": random.sample(DOMAIN_STATUSES, k=random.randint(1, 4)),
        "RegistrarName": registrar,
        "RegistrarWhoisServer": whois_server,
        "NameServers": [
            f"NS{i}.P{random.randint(1, 10):02d}.DYNECT.NET"
            for i in range(1, random.randint(2, 5))
        ],
    }
    for role in ("Registrant", "Admin", "Technical"):
        contact = _generate_domain_contact(privacy)
        result[f"{role}Name"] = contact["Name"]
        result[f"{role}Organization"] = contact["Organization"]
        result[f"{role}Address"] = contact["Address"]
        result[f"{role}Phone"] = contact["Phone"]
        result[f"{role}EmailAddress"] = contact["EmailAddress"]
    return result


# ── Flag-driven derivation (match_flag mode) ────────────────────────────────

def _norm(value: Any) -> Optional[str]:
    return str(value).strip().lower() if value is not None else None


def _partial_flag_plan(customer: Dict[str, Any],
                       is_business: bool) -> List[str]:
    """Which buckets a partial-match response deviates in, chosen among the
    fields the request actually carries (a deviation nobody can see is not a
    partial match). Name stays matching except in the rare name-only plan."""
    buckets = []
    if customer.get("AddressLine1"):
        buckets.append("address")
    if customer.get("PhoneNumber"):
        buckets.append("phone")
    if not is_business and (customer.get("DateOfBirth")
                            or customer.get("DlNumber")):
        buckets.append("id")
    if customer.get("TaxId"):
        buckets.append("tax")
    if not buckets:
        return ["name"]  # name-only request: overlap = first name only
    # Name and TaxId only ever deviate alone — combined with anything else
    # the CA/CI code table grades the row Declined, i.e. a no-match outcome.
    secondary = [b for b in buckets if b != "tax"]
    roll = random.random()
    if roll < 0.85 and secondary:
        if roll < 0.60 or len(secondary) == 1:
            return [random.choice(secondary)]
        return random.sample(secondary, 2)
    if roll < 0.95 and "tax" in buckets:
        return ["tax"]
    return ["name"]


def _perturb_address(addr: Dict[str, str]) -> Dict[str, str]:
    """Partial address deviation: usually the same street with a different
    house number, sometimes a different street in the same city — either way
    City/State/Zip survive, so the overlap is visible in the record."""
    tokens = addr["AddressLine1"].split()
    if tokens and tokens[0].isdigit() and random.random() < 0.7:
        number = tokens[0]
        while number == tokens[0]:
            number = str(random.randint(1, 19999))
        line1 = " ".join([number] + tokens[1:])
    else:
        line1 = (f"{random.randint(1, 19999)} {fake.last_name().upper()} "
                 f"{random.choice(STREET_SUFFIXES)}")
    return {**addr, "AddressLine1": line1}


def _flag_identity(customer: Dict[str, Any], flag: int, is_business: bool,
                   plan: List[str]) -> Dict[str, Any]:
    """The identity the response echoes, derived from the request per flag:
    COMPLETE copies the request (gaps filled synthetically), PARTIAL perturbs
    the planned buckets, NO_MATCH replaces every identity field."""
    dev_all = flag == NO_MATCH

    def dev(bucket: str) -> bool:
        return dev_all or bucket in plan

    first = customer.get("FirstName") or fake.first_name()
    last = customer.get("LastName") or fake.last_name()
    if dev_all:
        first, last = fake.first_name(), fake.last_name()
    elif "name" in plan:
        last = fake.last_name()  # keep the first name -> visible overlap

    if customer.get("AddressLine1"):
        address = {
            "AddressLine1": customer["AddressLine1"],
            "City": customer.get("City") or fake.city(),
            "State": customer.get("State") or random.choice(STATE_ABBRS),
            "ZipCode": (customer.get("ZipCode") or fake.postcode())[:5],
        }
    else:
        address = {
            "AddressLine1": fake.street_address(),
            "City": fake.city(),
            "State": random.choice(STATE_ABBRS),
            "ZipCode": fake.postcode()[:5],
        }
    if dev_all:
        address = {
            "AddressLine1": fake.street_address(),
            "City": fake.city(),
            "State": random.choice(STATE_ABBRS),
            "ZipCode": fake.postcode()[:5],
        }
    elif "address" in plan:
        address = _perturb_address(address)

    phone = customer.get("PhoneNumber") or _phone_parts()["PhoneNumber"]
    if dev("phone"):
        phone = _phone_parts()["PhoneNumber"]

    dob = customer.get("DateOfBirth") or fake.date_of_birth(
        minimum_age=18, maximum_age=90).isoformat()
    if dev("id"):
        dob = fake.date_of_birth(minimum_age=18, maximum_age=90).isoformat()

    email = customer.get("EmailAddress") or fake.email()
    if dev_all:
        email = fake.email()

    request_ip = customer.get("CurrentIpAddress")
    ip = {
        # NO_MATCH: a different address, so the request IP geolocates randomly.
        "Address": (fake.ipv4_public() if dev_all or not request_ip
                    else request_ip),
        "City": address["City"],
        "State": address["State"],
        "ZipCode": address["ZipCode"],
        "Latitude": round(float(fake.latitude()), 4),
        "Longitude": round(float(fake.longitude()), 4),
    }

    identity: Dict[str, Any] = {
        "person": {"FirstName": first, "LastName": last, "DateOfBirth": dob},
        "address": address,
        "phone": phone,
        "email": email,
        "ip": ip,
        "domain": (customer.get("Domain")
                   or fake.domain_name()),
    }
    if is_business:
        business_name = customer.get("BusinessName") or _business_name()
        if dev_all or "name" in plan:
            business_name = _business_name()
        identity["business"] = {"BusinessName": business_name}
    return identity


def _flag_cmp(customer: Dict[str, Any], flag: int, plan: List[str],
              is_business: bool) -> Dict[str, Optional[bool]]:
    """The field-by-field match picture implied by the flag. None = the field
    was absent from the request ("not evaluated", like the real API);
    True/False = evaluated match/mismatch."""
    dev_all = flag == NO_MATCH

    def result(present: Any, bucket: str) -> Optional[bool]:
        if not present:
            return None
        if dev_all:
            return False
        return bucket not in plan

    has_name = (customer.get("BusinessName") if is_business
                else customer.get("FirstName") or customer.get("LastName"))
    return {
        "name": result(has_name, "name"),
        "tax_id": result(customer.get("TaxId"), "tax"),
        "dob": result(customer.get("DateOfBirth"), "id"),
        "dl": result(customer.get("DlNumber"), "id"),
        "address": result(customer.get("AddressLine1"), "address"),
        "phone": result(customer.get("PhoneNumber"), "phone"),
    }


def _customer_code(cmp: Dict[str, Optional[bool]], g_authenticate: bool,
                   account_anchored: bool) -> Tuple[str, str]:
    """(CustomerResponseCode, VerificationResponse) per the Integration
    Guide's CA/CI code table. `account_anchored` = the request presented the
    truth bank account, so a total identity mismatch is a wrong-owner decline
    rather than an unknown person (ND02/NoData)."""
    prefix = "CA" if g_authenticate else "CI"
    evaluated = {k: v for k, v in cmp.items() if v is not None}
    if evaluated and all(evaluated.values()):
        return prefix + "11", "Pass"
    if not evaluated or (not any(evaluated.values()) and not account_anchored):
        return "ND02", "NoData"
    name_bad = cmp["name"] is False
    tax_bad = cmp["tax_id"] is False
    secondary_bad = [
        bucket for bucket, bad in (
            ("address", cmp["address"] is False),
            ("phone", cmp["phone"] is False),
            ("id", cmp["dob"] is False or cmp["dl"] is False),
        ) if bad
    ]
    if (name_bad and tax_bad) or ((name_bad or tax_bad) and secondary_bad):
        return prefix + "01", "Declined"
    if name_bad:
        return prefix + "21", "RiskAlert"
    if tax_bad:
        return prefix + "22", "RiskAlert"
    if len(secondary_bad) >= 2:
        return prefix + "30", "RiskAlert"
    code = {"address": "23", "phone": "24", "id": "25"}[secondary_bad[0]]
    return prefix + code, "AcceptWithRisk"


def _flag_account(check: Dict[str, Any], flag: int
                  ) -> Tuple[str, str, str, str]:
    """(code, bank, added, updated). A documented sandbox routing/account
    pair echoes its documented code and bank; otherwise the code is drawn
    from the flag's category pool, so complete-match rows carry Pass codes
    and no-match rows can also be account-risky. The bank is always the
    routing number's stable institution (data.reference.bank_name_for)."""
    if (check.get("RoutingNumber") == SANDBOX_ROUTING
            and check.get("AccountNumber") in SANDBOX_ACCOUNT_CODES):
        code = SANDBOX_ACCOUNT_CODES[check["AccountNumber"]]
        bank = SANDBOX_BANK_NAME
    else:
        code = random.choice(CATEGORY_ACCOUNT_CODES[MATCH_CATEGORIES[flag]])
        bank = refdata.bank_name_for(check.get("RoutingNumber"))
    return code, bank, _past_datetime(), _past_datetime(365)


def _combine_verification(*responses: Optional[str]) -> str:
    """Worst-outcome-wins combiner (Integration Guide)."""
    present = [r for r in responses if r]
    if not present:
        return "Pass"
    return max(present, key=VERIFICATION_SEVERITY.index)


#: The alert each derived deviation raises. Real ConsumerAlertMessage codes
#: are numeric; this once used the invented `CA0x` keys, which collided with
#: real CustomerResponseCode values and no longer exist. Each is mapped to the
#: documented code carrying the same wording, so the meaning is unchanged.
TAX_ID_ALERT = 320    # {subject} SSN reported as suspicious or belonging to a minor
ADDRESS_ALERT = 120   # {subject} address reported as suspicious, misused, or used in fraud
PHONE_ALERT = 220     # {subject} phone number reported as suspicious, misused, or used in fraud


def _derived_consumer_alerts(cmp: Dict[str, Optional[bool]],
                             customer: Dict[str, Any]) -> List[Dict[str, str]]:
    codes: List[int] = []
    if customer.get("TaxId") in ALERT_TAX_IDS or cmp["tax_id"] is False:
        codes.append(TAX_ID_ALERT)
    if cmp["address"] is False:
        codes.append(ADDRESS_ALERT)
    if cmp["phone"] is False:
        codes.append(PHONE_ALERT)
    # `alert_message` resolves the {subject} placeholder the real messages use.
    return [scenarios.alert_message(c) for c in codes[:2]]


def _ofac_matches(customer: Dict[str, Any],
                  is_business: bool) -> List[Dict[str, Any]]:
    """Watch-list hits fire only for the known sandbox OFAC names."""
    if is_business:
        name = _norm(customer.get("BusinessName"))
        if name in {b.lower() for b in OFAC_BUSINESSES}:
            return [generate_ofac_list_data(
                business=True, matched_name=(customer["BusinessName"], ""))]
        return []
    pair = (_norm(customer.get("FirstName")), _norm(customer.get("LastName")))
    if pair in {(f.lower(), l.lower()) for f, l in OFAC_PERSONS}:
        return [generate_ofac_list_data(
            matched_name=(customer["FirstName"], customer["LastName"]))]
    return []


def _flagged_post_inquiry_result(
    inquiry: Dict[str, Any], customer: Dict[str, Any], flag: int, *,
    g_verify: bool, g_authenticate: bool, g_identify: bool, g_kba: bool,
    g_esi: bool, ofac_scan: bool, ip_info: bool, domain_whois: bool,
    is_business: bool, has_input_address: bool,
) -> Dict[str, Any]:
    """Flag mode: produce a response in the requested match category, using
    the request itself as the source identity."""
    has_account_service = g_verify or g_authenticate
    check = inquiry.get("Check") or {}
    plan = (_partial_flag_plan(customer, is_business)
            if flag == PARTIAL_MATCH else [])
    truth = _flag_identity(customer, flag, is_business, plan)
    cmp = _flag_cmp(customer, flag, plan, is_business)

    account_code = bank_name = added = updated = None
    account_vr = None
    if has_account_service and check:
        account_code, bank_name, added, updated = _flag_account(check, flag)
        account_vr = ACCOUNT_CODE_VERIFICATION.get(account_code, "NoData")

    customer_code = customer_vr = None
    if g_authenticate or g_identify or ofac_scan:
        # A partial row is never an unknown person; a no-match row with the
        # account presented is a wrong-owner decline rather than ND02.
        customer_code, customer_vr = _customer_code(
            cmp, g_authenticate,
            account_anchored=bool(check) or flag == PARTIAL_MATCH)
    unknown_person = customer_code == "ND02"

    result: Dict[str, Any] = {
        "ItemReferenceId": random.randint(5_000_000_000, 5_999_999_999),
        "CreatedDate": datetime.now().isoformat(timespec="seconds"),
        "AccountResponseCode": account_code,
        "BankName": bank_name,
        "AccountAddedDate": added,
        "AccountLastUpdatedDate": updated,
        "AccountClosedDate": None,
        "FundsConfirmationResult": None,
        "CustomerResponseCode": customer_code,
    }
    verification = _combine_verification(account_vr, customer_vr)

    if g_identify:
        # The record derived from the flag identity always comes first; the
        # rest are random filler, so every list section has multiple entries
        # with at least one matching the request (when the flag says match).
        if is_business:
            result["MatchedBusinessData"] = (
                [] if unknown_person
                else [generate_business_data_record(truth=truth)] + [
                    generate_business_data_record()
                    for _ in range(_rand_count(1, 3))])
        else:
            result["MatchedPersonData"] = (
                [] if unknown_person
                else [generate_person_data_record(truth=truth)] + [
                    generate_person_data_record()
                    for _ in range(_rand_count(1, 3))])
            alerts = _derived_consumer_alerts(cmp, customer)
            result["ConsumerAlertMessages"] = alerts
            if alerts:
                # Alerts override a Pass outcome (Integration Guide).
                verification = _combine_verification(verification,
                                                     "AcceptWithRisk")

    if ofac_scan or g_identify:
        hits = _ofac_matches(customer, is_business)
        result["OfacListPotentialMatches"] = hits
        if hits:
            # OFAC potential match overrides the outcome (Integration Guide).
            verification = _combine_verification(verification, "RiskAlert")

    if g_kba:
        result["GIdentifyKbaResult"] = ([] if unknown_person
                                        else generate_kba_result())

    if g_esi and customer.get("EmailAddress"):
        result["EmailAddressInformationResult"] = (
            generate_email_address_information_result(
                truth=truth, request_email=customer["EmailAddress"]))

    if (g_esi or ip_info) and customer.get("CurrentIpAddress"):
        result["IpAddressInformationResult"] = (
            generate_ip_address_information_result(
                has_input_address, truth=truth, customer=customer))

    if domain_whois and customer.get("Domain"):
        result["DomainRegistry"] = generate_domain_registry(
            domain=customer["Domain"])

    result["VerificationResponse"] = verification
    return {"PostInquiryResult": result}


# ── Top-level response ───────────────────────────────────────────────────────

def generate_post_inquiry_result(
    request: Dict[str, Any],
    match_flag: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Build a full PostInquiryResult-shaped dict for any highlighted section.

    `request` is the stored request body: {"Inquiry": {...}} with service
    flags, Check, and Customer (a legacy flat dict also works — it maps to
    gVerify + gIdentify). Flags decide which sections appear.

    `match_flag` selects the match category the response is generated in:
    0 = complete match, 1 = partial match (some overlap, e.g. street matches
    but the house number differs), 2 = no match. When omitted it is read
    from the Inquiry's MatchFlag field; when absent there too, all response
    data is random (the original behavior) — see the module docstring.
    """
    inquiry = request.get("Inquiry", request)
    customer = inquiry.get("Customer") or {}

    if match_flag is None:
        match_flag = inquiry.get("MatchFlag")

    g_verify = bool(inquiry.get("GVerifyEnabled"))
    g_authenticate = bool(inquiry.get("GAuthenticateEnabled"))
    g_identify = bool(inquiry.get("GIdentifyEnabled"))
    g_kba = bool(inquiry.get("GIdentifyKbaEnabled"))
    g_esi = bool(inquiry.get("GIdentifyEsiEnabled"))
    ofac_scan = bool(inquiry.get("OfacScanEnabled"))
    ip_info = bool(inquiry.get("IpAddressInformationEnabled"))
    domain_whois = bool(inquiry.get("DomainWhoisEnabled"))

    # Legacy flat request (v0.3 shape): no flags at all -> gVerify + gIdentify.
    if not any([g_verify, g_authenticate, g_identify, g_kba, g_esi,
                ofac_scan, ip_info, domain_whois]):
        g_verify = g_identify = True

    is_business = bool(customer.get("BusinessName") or request.get("BusinessName"))
    has_account_service = g_verify or g_authenticate
    has_input_address = bool(customer.get("AddressLine1")
                             or request.get("Address"))

    # A requested match category is produced from the request itself, so it
    # short-circuits the profile-driven draw below.
    if match_flag is not None:
        match_flag = int(match_flag)
        if match_flag not in MATCH_FLAGS:
            raise ValueError(
                f"match_flag must be 0 (complete), 1 (partial), or "
                f"2 (no match); got {match_flag}")
        return _flagged_post_inquiry_result(
            inquiry, customer, match_flag,
            g_verify=g_verify, g_authenticate=g_authenticate,
            g_identify=g_identify, g_kba=g_kba, g_esi=g_esi,
            ofac_scan=ofac_scan, ip_info=ip_info, domain_whois=domain_whois,
            is_business=is_business, has_input_address=has_input_address)

    # Risk profile governing this response. Without a SCENARIO_RESOLVER
    # installed this is always `clean`, matching the mock's original behaviour.
    profile = _profile_for(inquiry)

    # One market anchors the whole response, taken from the input address when
    # there is one so the matched records sit near the customer we were asked
    # about, as a real bureau lookup would.
    market = refdata.resolve_market(customer.get("State"))

    # The account outcome and the overall VerificationResponse must agree: the
    # Integration Guide maps each AccountResponseCode to exactly one response.
    account_code = (scenarios.weighted(profile.account_codes)
                    if has_account_service else None)
    if account_code:
        verification = scenarios.ACCOUNT_CODE_OUTCOMES[account_code]
    else:
        verification = None

    customer_code = (
        scenarios.weighted(profile.customer_codes_gauthenticate) if g_authenticate
        else scenarios.weighted(profile.customer_codes_gidentify)
        if (g_identify or ofac_scan) else None
    )
    # When both fire, the worse of the two outcomes wins.
    if customer_code:
        cust_outcome = scenarios.CUSTOMER_CODE_OUTCOMES[customer_code]
        verification = (cust_outcome if verification is None
                        else _worst_outcome(verification, cust_outcome))
    if verification is None:
        verification = "Pass"

    account_age = random.randint(*profile.account_age_days)

    result: Dict[str, Any] = {
        "ItemReferenceId": random.randint(5_000_000_000, 5_999_999_999),
        "CreatedDate": datetime.now().isoformat(timespec="seconds"),
        "VerificationResponse": verification,
        "AccountResponseCode": account_code,
        "BankName": (refdata.bank_name_for(
            (inquiry.get("Check") or {}).get("RoutingNumber"))
            if has_account_service else None),
        "AccountAddedDate": _past_datetime(account_age) if has_account_service else None,
        "AccountLastUpdatedDate": (
            _past_datetime(min(random.randint(*profile.account_updated_days),
                               account_age))
            if has_account_service else None),
        "AccountClosedDate": None,
        "FundsConfirmationResult": None,
        "CustomerResponseCode": customer_code,
    }

    if g_identify:
        n_records = random.randint(*profile.n_matched_records)
        # The first record echoes the submitted identity when the bureau
        # matched it; the rest are near-matches the search also turned up.
        echo = random.random() < profile.echo_input_identity
        if is_business:
            result["MatchedBusinessData"] = [
                generate_business_data_record(
                    market=market, profile=profile,
                    customer=customer if (i == 0 and echo) else None,
                )
                for i in range(n_records)
            ]
        else:
            result["MatchedPersonData"] = [
                generate_person_data_record(
                    market=market, profile=profile,
                    customer=customer if (i == 0 and echo) else None,
                )
                for i in range(n_records)
            ]
            # If the input address is the one on file, surface it as Current.
            if (n_records and has_input_address
                    and random.random() < profile.address_match):
                _set_input_address_current(
                    result["MatchedPersonData"][0], customer)
            result["ConsumerAlertMessages"] = generate_consumer_alert_messages(profile)

    if ofac_scan or g_identify:
        # A standalone gOFAC scan is far more likely to surface a hit than
        # gIdentify's auto-included scan; the watchlist profile forces one.
        hit_chance = (profile.ofac_hit_chance if ofac_scan
                      else profile.ofac_hit_chance * 0.2)
        n_hits = random.randint(1, 2) if random.random() < hit_chance else 0
        result["OfacListPotentialMatches"] = [
            generate_ofac_list_data(business=is_business) for _ in range(n_hits)
        ]
        if n_hits:
            # OFAC potential match overrides the outcome (Integration Guide).
            result["VerificationResponse"] = "RiskAlert"

    if g_kba:
        result["GIdentifyKbaResult"] = generate_kba_result()

    if g_esi and customer.get("EmailAddress"):
        result["EmailAddressInformationResult"] = (
            generate_email_address_information_result()
        )

    if (g_esi or ip_info) and customer.get("CurrentIpAddress"):
        result["IpAddressInformationResult"] = (
            generate_ip_address_information_result(
                has_input_address, market=market, profile=profile)
        )

    if domain_whois and customer.get("Domain"):
        result["DomainRegistry"] = generate_domain_registry()

    return {"PostInquiryResult": result}
