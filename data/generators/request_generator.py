"""
Synthetic request payload builders for the batch pipeline.

Pure functions — no DB, no HTTP. batch_runner.py picks how many to build,
stores them in the shared generated_requests table (db.py), persists each
request's baseline "truth" identity (baseline_identities table), and sends the
requests to the running mock API.

Every request is assigned exactly one match category:

  full_match     the request equals the truth identity the vendor "knows"
  partial_match  the name matches; a controlled subset of secondary fields
                 (address / phone / DOB-DL / TaxId) deviates — at least one
                 field matches and at least one mismatches
  no_match       every identity field deviates (account-anchored rows keep
                 the truth bank account: the "wrong owner" fraud story;
                 identity-only rows are simply an unknown person)

The truth identity carries every attribute the mock responses echo (person,
address, phone, email, bank account incl. its AccountResponseCode, IP
geolocation, domain). The response generators compare the request against the
stored truth field by field, so full-match rows come back clean and
no-match rows come back flagged — see response_generator.py /
ekata_response_generator.py.

Every GIACT payload is valid by construction against the rules the mock
enforces (_validate_inquiry in mock_api.py): each scenario template fixes the
service flags and the fields those flags require, then coin-flips the optional
fields (phone, TaxId, DOB, DL, address, ...) so field presence varies across a
batch — that variance is what the business review needs to see. Fields the
category plan deviates are always included, so the mismatch is observable.

~15% of GIACT payloads are seeded with known sandbox trigger values from
docs/references/sandbox-test-data.md (test routing/account pairs, OFAC names,
consumer-alert TaxIds, ESI test emails, test IPs); those rows get a "_seed"
suffix on their scenario name so they are identifiable in exports. Fraud
trigger values (OFAC names, alert TaxIds, fraud emails) are never placed on
full_match rows.

Callers pass an explicit random.Random and a Faker instance (seed both for
reproducible batches).
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from faker import Faker

from data import reference as refdata

# ── Match categories ─────────────────────────────────────────────────────────

MATCH_CATEGORIES = ("full_match", "partial_match", "no_match")
DEFAULT_CATEGORY_WEIGHTS = {"full_match": 60, "partial_match": 25,
                            "no_match": 15}

# ── Sandbox trigger values (docs/references/sandbox-test-data.md) ────────────

SANDBOX_ROUTING = "122105278"
SANDBOX_BANK_NAME = "WELLS FARGO BANK NA (ARIZONA)"
# Test bank accounts and the AccountResponseCode each returns in the real
# sandbox (numeric codes carry the XML enum's leading underscore).
SANDBOX_ACCOUNT_CODES = {
    "0000000001": "GN01", "0000000005": "GS02", "0000000008": "ND00",
    "0000000010": "RT00", "0000000011": "RT01", "0000000013": "RT03",
    "0000000016": "_1111", "0000000018": "_3333", "0000000019": "_5555",
}
SANDBOX_ACCOUNTS = list(SANDBOX_ACCOUNT_CODES)
# Which sandbox accounts / unseeded account codes suit each category, so
# clean rows look clean (full_match accounts always carry Pass codes) and
# no_match rows can also be account-risky.
CATEGORY_SANDBOX_ACCOUNTS = {
    "full_match": ["0000000016", "0000000018", "0000000019"],
    "partial_match": ["0000000016", "0000000018", "0000000019",
                      "0000000008", "0000000013"],
    "no_match": ["0000000001", "0000000010", "0000000011", "0000000013",
                 "0000000016"],
}
CATEGORY_ACCOUNT_CODES = {
    "full_match": ["_1111", "_1111", "_1111", "_5555", "_2222", "_3333"],
    "partial_match": ["_1111", "_1111", "_5555", "_3333", "ND00", "RT03"],
    "no_match": ["_1111", "_5555", "RT00", "RT01", "GN01", "RT03"],
}

OFAC_PERSONS = [("Ayman", "Joumaa"), ("Ayman Saied", "Joumaa"),
                ("Bob", "Smith"), ("Robert C", "Smith")]
OFAC_BUSINESSES = ["Sniper Africa"]
ALERT_TAX_IDS = ["000000300", "000000301", "000000320", "000000321",
                 "000000322", "000000530", "000000531", "000000532",
                 "000000533"]
ESI_TEST_EMAILS = ["jane.doe@gmail.com", "102fr@gmail.com", "103ur@gmail.com",
                   "104lr@gmail.com", "105mr@gmail.com", "301cnpr@gmail.com",
                   "305it@gmail.com"]
# The fraud-flavoured subset (used as no_match replacement emails).
FRAUD_TEST_EMAILS = ["102fr@gmail.com", "301cnpr@gmail.com", "305it@gmail.com"]
SANDBOX_IPS = ["123.45.67.89", "111.22.33.44"]

SEED_PROBABILITY = 0.15

# ── GIACT scenario templates ─────────────────────────────────────────────────
# (name, weight, flags, needs) — `needs` drives which inputs the builder must
# include: check, person, business, email_or_ip, ip, domain.

GIACT_SCENARIOS: List[Tuple[str, int, Dict[str, bool], Dict[str, bool]]] = [
    ("gverify_identify_person", 20,
     {"GVerifyEnabled": True, "GIdentifyEnabled": True},
     {"check": True, "person": True}),
    ("gverify_identify_business", 10,
     {"GVerifyEnabled": True, "GIdentifyEnabled": True},
     {"check": True, "business": True}),
    ("gauthenticate_identify", 15,
     {"GAuthenticateEnabled": True, "GIdentifyEnabled": True},
     {"check": True, "person": True}),
    ("gauthenticate_identify_kba", 8,
     {"GAuthenticateEnabled": True, "GIdentifyEnabled": True,
      "GIdentifyKbaEnabled": True},
     {"check": True, "person": True}),
    ("gauthenticate_identify_esi", 8,
     {"GAuthenticateEnabled": True, "GIdentifyEnabled": True,
      "GIdentifyEsiEnabled": True},
     {"check": True, "person": True, "email_or_ip": True}),
    ("ofac_person", 7,
     {"OfacScanEnabled": True},
     {"person": True, "minimal": True, "ofac": True}),
    ("ofac_business", 5,
     {"OfacScanEnabled": True},
     {"business": True, "minimal": True, "ofac": True}),
    ("gidentify_person", 10,
     {"GIdentifyEnabled": True},
     {"person": True}),
    ("gidentify_business", 5,
     {"GIdentifyEnabled": True},
     {"business": True}),
    ("gidentify_kba", 4,
     {"GIdentifyEnabled": True, "GIdentifyKbaEnabled": True},
     {"person": True}),
    ("gidentify_ip_info", 5,
     {"GIdentifyEnabled": True, "IpAddressInformationEnabled": True},
     {"person": True, "ip": True}),
    ("gidentify_domain_whois", 4,
     {"GIdentifyEnabled": True, "DomainWhoisEnabled": True},
     {"person": True, "domain": True}),
    ("kitchen_sink", 3,
     {"GAuthenticateEnabled": True, "GIdentifyEnabled": True,
      "GIdentifyKbaEnabled": True, "GIdentifyEsiEnabled": True,
      "OfacScanEnabled": True, "IpAddressInformationEnabled": True,
      "DomainWhoisEnabled": True},
     {"check": True, "person": True, "email_or_ip": True, "ip": True,
      "domain": True, "full": True, "ofac": True}),
]

EKATA_SCENARIOS: List[Tuple[str, int, Dict[str, bool]]] = [
    ("ekata_full", 30,
     {"phone": True, "email": True, "address": True, "ip": True}),
    ("ekata_name_phone", 25, {"phone": True}),
    ("ekata_name_address", 20, {"address": True}),
    ("ekata_minimal", 15, {}),
    ("ekata_with_secondary", 10,
     {"phone": True, "email": True, "address": True, "secondary": True}),
]


def _weighted_choice(rng: random.Random, scenarios) -> Tuple:
    total = sum(w for _, w, *_ in scenarios)
    pick = rng.uniform(0, total)
    for entry in scenarios:
        pick -= entry[1]
        if pick <= 0:
            return entry
    return scenarios[-1]


def choose_category(rng: random.Random,
                    weights: Optional[Dict[str, float]] = None) -> str:
    """Draw one match category from the (defaulted) weight mix."""
    w = weights or DEFAULT_CATEGORY_WEIGHTS
    entries = [(c, w.get(c, 0)) for c in MATCH_CATEGORIES]
    if sum(weight for _, weight in entries) <= 0:
        raise ValueError(f"category weights sum to zero: {w}")
    return _weighted_choice(rng, entries)[0]


# ── Field builders ───────────────────────────────────────────────────────────

def _digits(rng: random.Random, n: int) -> str:
    return "".join(str(rng.randint(0, 9)) for _ in range(n))


def _phone(rng: random.Random) -> str:
    # 10-digit numeric, non-zero leading digit (matches GIACT examples).
    return str(rng.randint(2, 9)) + _digits(rng, 9)


def _account_number(rng: random.Random, routing: str) -> str:
    account = _digits(rng, rng.randint(6, 12))
    while account == routing:
        account = _digits(rng, rng.randint(6, 12))
    return account


def _address(rng: random.Random, faker: Faker) -> Dict[str, str]:
    return {
        "AddressLine1": faker.street_address(),
        "City": faker.city(),
        "State": faker.state_abbr(include_territories=False),
        "ZipCode": faker.postcode(),
        "Country": "US",
    }


def _past_date(rng: random.Random, lo_days: int, hi_days: int) -> str:
    dt = datetime.now() - timedelta(days=rng.randint(lo_days, hi_days))
    return dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


# ── GIACT baseline truth ─────────────────────────────────────────────────────

def _generate_giact_truth(rng: random.Random, faker: Faker,
                          needs: Dict[str, bool], category: str,
                          seeded: bool) -> Dict[str, Any]:
    """The identity the vendor "knows" — every attribute the response echoes.

    Fraud trigger values (OFAC names) only land in the truth for non-full
    categories, so clean rows stay clean.
    """
    first, last = faker.first_name(), faker.last_name()
    business_name = faker.company()
    if seeded and needs.get("ofac") and category == "partial_match":
        # Request copies the (matching) name -> deterministic OFAC hit.
        if needs.get("business"):
            business_name = rng.choice(OFAC_BUSINESSES)
        else:
            first, last = rng.choice(OFAC_PERSONS)

    if seeded:
        routing = SANDBOX_ROUTING
        account = rng.choice(CATEGORY_SANDBOX_ACCOUNTS[category])
        account_code = SANDBOX_ACCOUNT_CODES[account]
        bank_name = SANDBOX_BANK_NAME
    else:
        routing = faker.aba()  # checksum-valid ABA routing number
        account = _account_number(rng, routing)
        account_code = rng.choice(CATEGORY_ACCOUNT_CODES[category])
        bank_name = refdata.bank_name_for(routing)
    added_days = rng.randint(400, 4000)
    address = _address(rng, faker)

    truth: Dict[str, Any] = {
        "kind": "giact",
        "person": {
            "FirstName": first,
            "LastName": last,
            "DateOfBirth": faker.date_of_birth(
                minimum_age=18, maximum_age=90).isoformat(),
            "TaxId": _digits(rng, 9),
            "DlNumber": _digits(rng, 8),
            "DlState": faker.state_abbr(include_territories=False),
        },
        "address": address,
        "phone": _phone(rng),
        "email": faker.email(),
        "account": {
            "RoutingNumber": routing,
            "AccountNumber": account,
            "AccountType": rng.choices(["Checking", "Savings"],
                                       weights=[80, 20])[0],
            "AccountResponseCode": account_code,
            "BankName": bank_name,
            "AccountAddedDate": _past_date(rng, added_days, added_days),
            "AccountLastUpdatedDate": _past_date(rng, 5, min(added_days, 365)),
        },
        # The truth person's IP geolocates to their home city, so full-match
        # rows show City/State/Zip matches and a small distance.
        "ip": {
            "Address": (rng.choice(SANDBOX_IPS) if seeded
                        else faker.ipv4_public()),
            "City": address["City"],
            "State": address["State"],
            "ZipCode": address["ZipCode"][:5],
            "Latitude": round(float(faker.latitude()), 4),
            "Longitude": round(float(faker.longitude()), 4),
        },
        "domain": faker.domain_name(),
    }
    if needs.get("business"):
        truth["business"] = {"BusinessName": business_name}
    return truth


# ── Category-driven customer derivation ──────────────────────────────────────

def _partial_plan(rng: random.Random, business: bool) -> List[str]:
    """Which fields a partial_match request deviates in. Name stays matching
    except in the rare name-only plan; TaxId likewise."""
    buckets = ["address", "phone"] + ([] if business else ["id"])
    roll = rng.random()
    if roll < 0.60:
        return [rng.choice(buckets)]
    if roll < 0.85:
        return rng.sample(buckets, min(2, len(buckets)))
    if roll < 0.95 or business:
        return ["name"]
    return ["tax"]


def _deviate_address(rng: random.Random, faker: Faker,
                     truth_addr: Dict[str, str]) -> Dict[str, str]:
    """Partial deviation: usually a different street in the same city (the
    server then still reports city/state matches), sometimes a whole new
    address."""
    if rng.random() < 0.6:
        addr = dict(truth_addr)
        addr["AddressLine1"] = faker.street_address()
        return addr
    return _address(rng, faker)


def _derive_customer(rng: random.Random, faker: Faker,
                     truth: Dict[str, Any], needs: Dict[str, bool],
                     category: str, seeded: bool
                     ) -> Tuple[Dict[str, str], List[str]]:
    """Assemble a Customer dict from the truth identity per category.

    full_match copies truth values; partial_match deviates the planned
    buckets (always included in the payload, so the mismatch is observable);
    no_match replaces every identity field with fresh fakes. Optional-field
    presence keeps the original coin flips (always for kitchen_sink, never
    for minimal scenarios).
    """
    minimal = needs.get("minimal", False)
    full_flags = needs.get("full", False)
    business = bool(needs.get("business"))
    person = truth["person"]

    if category == "partial_match":
        deviations = _partial_plan(rng, business)
    elif category == "no_match":
        deviations = ["identity"]
    else:
        deviations = []
    dev_all = category == "no_match"

    def dev(bucket: str) -> bool:
        return dev_all or bucket in deviations

    def include(p: float, bucket: Optional[str] = None) -> bool:
        if bucket is not None and bucket in deviations:
            return True  # deviated fields must appear in the request
        if full_flags:
            return True
        if minimal:
            return False
        return rng.random() < p

    cust: Dict[str, str] = {}
    if business:
        if dev("name"):
            if dev_all and seeded and needs.get("ofac"):
                cust["BusinessName"] = rng.choice(OFAC_BUSINESSES)
            else:
                cust["BusinessName"] = faker.company()
        else:
            cust["BusinessName"] = truth["business"]["BusinessName"]
    else:
        if dev_all:
            if seeded and needs.get("ofac"):
                first, last = rng.choice(OFAC_PERSONS)
            else:
                first, last = faker.first_name(), faker.last_name()
        elif "name" in deviations:
            first, last = person["FirstName"], faker.last_name()
        else:
            first, last = person["FirstName"], person["LastName"]
        cust["FirstName"], cust["LastName"] = first, last

    if include(0.7, "address"):
        addr = (_deviate_address(rng, faker, truth["address"])
                if "address" in deviations
                else _address(rng, faker) if dev_all
                else truth["address"])
        cust["AddressLine1"] = addr["AddressLine1"]
        cust["City"] = addr["City"]
        cust["State"] = addr["State"]
        cust["ZipCode"] = addr["ZipCode"]
        if include(0.5):
            cust["Country"] = "US"
    if include(0.6, "phone"):
        cust["PhoneNumber"] = _phone(rng) if dev("phone") else truth["phone"]
    if include(0.5, "tax"):
        if dev("tax"):
            cust["TaxId"] = (rng.choice(ALERT_TAX_IDS)
                             if dev_all and seeded and not business
                             else _digits(rng, 9))
        else:
            cust["TaxId"] = person["TaxId"]
    if not business and include(0.5, "id"):
        cust["DateOfBirth"] = (
            faker.date_of_birth(minimum_age=18, maximum_age=90).isoformat()
            if dev("id") else person["DateOfBirth"])
    if not business and include(0.3, "id"):
        # DL number and state always emitted together
        if dev("id"):
            cust["DlNumber"] = _digits(rng, 8)
            cust["DlState"] = faker.state_abbr(include_territories=False)
        else:
            cust["DlNumber"] = person["DlNumber"]
            cust["DlState"] = person["DlState"]

    # A partial row that deviates name/tax must still carry >=1 matching
    # field, or the server would classify it as an unknown person (ND02).
    if (category == "partial_match" and {"name", "tax"} & set(deviations)
            and not ({"AddressLine1", "PhoneNumber", "DateOfBirth"} & set(cust)
                     or ("TaxId" in cust and "tax" not in deviations))):
        cust["PhoneNumber"] = truth["phone"]

    # Flag-required inputs (partial plans never deviate these; no_match does).
    if needs.get("email_or_ip"):
        if dev_all:
            cust["EmailAddress"] = (rng.choice(FRAUD_TEST_EMAILS) if seeded
                                    else faker.email())
        else:
            cust["EmailAddress"] = truth["email"]
        if include(0.4) or needs.get("ip"):
            cust["CurrentIpAddress"] = (faker.ipv4_public() if dev_all
                                        else truth["ip"]["Address"])
    elif needs.get("ip"):
        cust["CurrentIpAddress"] = (faker.ipv4_public() if dev_all
                                    else truth["ip"]["Address"])
    if needs.get("domain"):
        cust["Domain"] = faker.domain_name() if dev_all else truth["domain"]
    return cust, deviations


# ── Public builders ──────────────────────────────────────────────────────────

def generate_giact_request(rng: random.Random, faker: Faker,
                           unique_id: Optional[str] = None,
                           category: Optional[str] = None,
                           weights: Optional[Dict[str, float]] = None,
                           ) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    """Return (scenario_name, request body, baseline_record).

    The body is alias-form (PascalCase keys) exactly as POSTed to
    /api/v5/inquiries. Passing unique_id (recommended: batch-prefixed)
    guarantees a distinct store-and-replay identity per row.

    baseline_record is {"category", "identity", "deviations"} — the truth
    identity to persist via db.insert_baseline so the server can derive a
    category-consistent response.
    """
    name, _, flags, needs = _weighted_choice(rng, GIACT_SCENARIOS)
    seeded = rng.random() < SEED_PROBABILITY
    if category is None:
        category = choose_category(rng, weights)
    truth = _generate_giact_truth(rng, faker, needs, category, seeded)

    inquiry: Dict[str, Any] = {}
    if unique_id is not None:
        inquiry["UniqueId"] = unique_id
    inquiry.update(flags)
    # 0/1/2 = full/partial/no match — the response generator produces this
    # category at generation time from the request data (match_flag mode).
    inquiry["MatchFlag"] = MATCH_CATEGORIES.index(category)
    if needs.get("check"):
        # The request always presents the truth account — for no_match rows
        # that is the point: a real account submitted by the "wrong" person.
        acct = truth["account"]
        inquiry["Check"] = {
            "RoutingNumber": acct["RoutingNumber"],
            "AccountNumber": acct["AccountNumber"],
            "AccountType": acct["AccountType"],
        }
    cust, deviations = _derive_customer(rng, faker, truth, needs, category,
                                        seeded)
    inquiry["Customer"] = cust
    baseline = {"category": category, "identity": truth,
                "deviations": deviations}
    return (f"{name}_seed" if seeded else name), {"Inquiry": inquiry}, baseline


# ── Ekata ────────────────────────────────────────────────────────────────────

def _generate_ekata_person_truth(rng: random.Random, faker: Faker
                                 ) -> Dict[str, Any]:
    return {
        "first_name": faker.first_name(),
        "last_name": faker.last_name(),
        "phone": _phone(rng),
        "email": faker.email(),
        "address": {
            "street_line_1": faker.street_address(),
            "city": faker.city(),
            "state_code": faker.state_abbr(include_territories=False),
            "postal_code": faker.postcode(),
        },
    }


def _generate_ekata_truth(rng: random.Random, faker: Faker,
                          needs: Dict[str, bool]) -> Dict[str, Any]:
    truth = {
        "kind": "ekata",
        "primary": _generate_ekata_person_truth(rng, faker),
        "ip": faker.ipv4_public(),
    }
    if needs.get("secondary"):
        truth["secondary"] = _generate_ekata_person_truth(rng, faker)
    return truth


def _ekata_person_params(rng: random.Random, faker: Faker, prefix: str,
                         truth_person: Dict[str, Any],
                         needs: Dict[str, bool], deviated: List[str],
                         dev_all: bool) -> Dict[str, str]:
    params: Dict[str, str] = {}
    if dev_all:
        first, last = faker.first_name(), faker.last_name()
    else:
        first, last = truth_person["first_name"], truth_person["last_name"]
    if rng.random() < 0.5:
        params[f"{prefix}.name"] = f"{first} {last}"
    else:
        params[f"{prefix}.firstname"] = first
        params[f"{prefix}.lastname"] = last

    def dev(element: str) -> bool:
        return dev_all or element in deviated

    if needs.get("phone") or "phone" in deviated:
        params[f"{prefix}.phone"] = (_phone(rng) if dev("phone")
                                     else truth_person["phone"])
    if needs.get("email") or "email" in deviated:
        params[f"{prefix}.email_address"] = (faker.email() if dev("email")
                                             else truth_person["email"])
    if needs.get("address") or "address" in deviated:
        if dev("address"):
            addr = {
                "street_line_1": faker.street_address(),
                "city": faker.city(),
                "state_code": faker.state_abbr(include_territories=False),
                "postal_code": faker.postcode(),
            }
            if "address" in deviated and rng.random() < 0.5:
                # Half of partial address deviations keep the city/state/zip
                # (a "moved across town" story -> City match grade results).
                addr = dict(truth_person["address"])
                addr["street_line_1"] = faker.street_address()
        else:
            addr = truth_person["address"]
        params[f"{prefix}.address.street_line_1"] = addr["street_line_1"]
        params[f"{prefix}.address.city"] = addr["city"]
        params[f"{prefix}.address.state_code"] = addr["state_code"]
        params[f"{prefix}.address.postal_code"] = addr["postal_code"]
        if rng.random() < 0.5:
            params[f"{prefix}.address.country_code"] = "US"
    return params


def generate_ekata_request(rng: random.Random, faker: Faker,
                           category: Optional[str] = None,
                           weights: Optional[Dict[str, float]] = None,
                           ) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    """Return (scenario_name, dict of dotted Ekata query params,
    baseline_record) — see generate_giact_request for the record shape."""
    name, _, needs = _weighted_choice(rng, EKATA_SCENARIOS)
    if category is None:
        category = choose_category(rng, weights)
    truth = _generate_ekata_truth(rng, faker, needs)
    dev_all = category == "no_match"

    deviated: List[str] = []
    if category == "partial_match":
        # Deviated elements are force-included even when the scenario would
        # not normally carry them, so the mismatch is observable; the name
        # always stays matching (>=1 match guaranteed).
        elements = ["phone", "email", "address"]
        deviated = (rng.sample(elements, 2) if rng.random() < 0.25
                    else [rng.choice(elements)])
    elif dev_all:
        deviated = ["identity"]

    params = _ekata_person_params(rng, faker, "primary", truth["primary"],
                                  needs, deviated, dev_all)
    if needs.get("secondary"):
        # The secondary person follows the primary's category: matching for
        # full/partial (partial deviations apply to the primary only), fresh
        # for no_match.
        params.update(_ekata_person_params(
            rng, faker, "secondary", truth["secondary"], needs, [], dev_all))
    if needs.get("ip"):
        params["ip_address"] = (faker.ipv4_public() if dev_all
                                else truth["ip"])
    baseline = {"category": category, "identity": truth,
                "deviations": deviated}
    return name, params, baseline
