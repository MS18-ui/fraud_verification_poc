"""
Synthetic Ekata-style Identity Check response generator.

Given the query parameters of an `identity_check` request (primary/secondary
name, phone, email, address, plus ip_address), produces a fully synthetic
response mirroring the Ekata Identity Check API v3.3 JSON sample.

Design notes
------------
- Same design decisions as the GIACT generator (response_generator.py),
  including its two generation modes (the `baseline` argument):
  * baseline=None — every value is random/synthetic; nothing from the request
    is required to appear in the response (kept for ad-hoc requests).
  * baseline given — a truth identity stored by the request generator. Each
    check block is derived by comparison: an input element (phone / email /
    address) equal to the truth's links to the truth person (match_to_name
    compares the input name against the truth name); a deviating element
    belongs to a random stranger ("No match"). match_to_address uses the
    component ladder (Match > Postal match > City match > State match >
    No match). The identity scores are banded by how many evaluated inputs
    mismatched (0 -> low risk, some -> mid, all -> high + alerts).
- Structure follows docs/references/ekata-identity-check.md. A check block is
  only generated when the corresponding input was supplied (e.g. no
  `secondary.phone` -> `secondary_phone_checks` is null), matching how the
  real API omits checks it has no input for.
- Subscriber/resident/owner name and age_range are generated once per call so
  the blocks stay mutually consistent, like the sample response.
"""

from __future__ import annotations

import random
from typing import Any, Callable, Dict, List, Optional, Tuple

from faker import Faker

from data import reference as refdata
from data import scenarios

fake = Faker("en_US")

# ── Scenario hook ───────────────────────────────────────────────────────────

#: Optional `Callable[[dict], Optional[str]]` mapping request params to a risk
#: profile name. `None` (default) treats every request as `clean`, preserving
#: the generator's original behaviour.
SCENARIO_RESOLVER: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None


def _profile_for(params: Dict[str, Any]) -> scenarios.RiskProfile:
    if SCENARIO_RESOLVER is None:
        return scenarios.CLEAN
    return scenarios.profile_for(SCENARIO_RESOLVER(params))

# ── Enumerations (from docs/references/ekata-identity-check.md) ──────────────

MATCH_LEVELS = [
    "Match", "Zip+4 match", "Postal match", "City match", "Metro match",
    "State match", "Country match", "No match",
]
NAME_MATCH_LEVELS = ["Match", "No match", "No name found"]
LINE_TYPES = [
    "Landline", "Mobile", "fixed VOIP", "non-fixed VOIP",
    "Premium", "Tollfree", "Voicemail", "Other", "Unknown",
]
CARRIERS = [
    "AT&T Wireless", "Verizon Wireless", "T-Mobile USA", "Sprint",
    "Broadsoft", "Level 3 Communications",
]
INPUT_COMPLETENESS = ["Complete", "Partial", "Empty"]
ADDRESS_TYPES = [
    "Single unit", "Multi unit", "PO box", "Commercial mail drop", "Unknown",
]
PHONE_WARNINGS = ["Invalid Input", "Missing country code, assuming US"]
ADDRESS_WARNINGS = [
    "Invalid Input",
    "Both parsed and postal address parameters provided, "
    "postal address parameters ignored.",
]
IP_WARNINGS = ["IP address is in private range", "Invalid Input"]
ALERT_POOL = [
    {"type": "high_velocity", "message": "Identity elements seen at high velocity in the network"},
    {"type": "impossible_travel", "message": "IP geolocation inconsistent with address history"},
    {"type": "identity_mismatch", "message": "Primary and secondary identities appear unrelated"},
]
CONTINENTS = ["NA", "SA", "EU", "AS", "AF", "OC"]

STATE_ABBRS = refdata.STATES


# ── Leaf-value helpers ───────────────────────────────────────────────────────

def _error() -> Optional[str]:
    return random.choice([None, None, None, "Partial"])


def _warnings(pool: List[str]) -> List[str]:
    return random.sample(pool, k=random.randint(0, min(2, len(pool))))


def _person(name: Optional[str] = None) -> Dict[str, Any]:
    """Shared subscriber/resident/registered_owner shape.

    Echoes the queried `name` when one was supplied, so the subscriber /
    resident / registered_owner blocks name the identity that was searched —
    as the real API's sample does — rather than an unrelated person.
    """
    lo = 5 * random.randint(4, 17)  # 20-85, 5-year Ekata buckets
    return {
        "name": name or fake.name(),
        "age_range": {"from": lo, "to": lo + 4},
    }


def _name_match(ok: bool) -> str:
    return "Match" if ok else random.choice(["No match", "No name found"])


def _addr_match(ok: bool) -> str:
    return (random.choice(["Match", "Zip+4 match", "Postal match"]) if ok
            else random.choice(["City match", "State match", "Country match",
                                "No match"]))


def _distance(near: bool = True) -> int:
    """Distance in miles: ~0 when the elements corroborate, large when not."""
    if near:
        return random.choice([0, 0, random.randint(1, 25)])
    return random.randint(50, 2500)


# ── Check-block builders ─────────────────────────────────────────────────────

def generate_phone_checks(
    person: Dict[str, Any], profile: scenarios.RiskProfile,
) -> Dict[str, Any]:
    name_ok = random.random() < profile.echo_input_identity
    addr_ok = random.random() < profile.address_match
    return {
        "error": _error(),
        "warnings": _warnings(PHONE_WARNINGS),
        "is_valid": random.random() < 0.95,
        "country_code": "US",
        "is_commercial": random.random() < 0.3,
        "line_type": random.choice(LINE_TYPES),
        "carrier": random.choice(CARRIERS),
        "is_prepaid": random.random() < 0.2,
        "match_to_name": _name_match(name_ok),
        "match_to_address": _addr_match(addr_ok),
        "subscriber": person,
    }


def generate_address_checks(
    person: Dict[str, Any], profile: scenarios.RiskProfile,
    secondary: bool = False,
) -> Dict[str, Any]:
    name_ok = random.random() < profile.address_match
    checks: Dict[str, Any] = {
        "error": _error(),
        "warnings": _warnings(ADDRESS_WARNINGS),
        "is_valid": random.random() < 0.95,
        "input_completeness": random.choice(INPUT_COMPLETENESS),
        "match_to_name": _name_match(name_ok),
        "resident": person,
        "is_commercial": random.random() < 0.3,
        "is_forwarder": random.random() < 0.1,
        "type": random.choice(ADDRESS_TYPES),
    }
    if secondary:
        checks["distance_from_primary_address"] = _distance(random.random() < 0.5)
        checks["linked_to_primary_resident"] = random.random() < 0.5
    return checks


def generate_email_checks(
    person: Dict[str, Any], profile: scenarios.RiskProfile,
    primary: bool = False,
) -> Dict[str, Any]:
    name_ok = random.random() < profile.echo_input_identity
    addr_ok = random.random() < profile.address_match
    lo_r, hi_r = profile.element_risk_range
    high_risk = profile.name != "clean"
    checks: Dict[str, Any] = {
        "error": _error(),
        "warnings": _warnings(["Invalid Input"]),
        "is_valid": random.random() < 0.95,
        "is_autogenerated": random.random() < (0.25 if high_risk else 0.05),
        "is_disposable": random.random() < (0.2 if high_risk else 0.03),
        "email_first_seen_days": (random.randint(1, 90) if high_risk
                                  else random.randint(200, 5000)),
        "email_domain_creation_days": random.randint(365, 10000),
        "match_to_name": _name_match(name_ok),
        "match_to_address": _addr_match(addr_ok),
    }
    if primary:
        checks["email_risk_score"] = round(random.uniform(lo_r, hi_r), 2)
        checks["mailbox_velocity"] = (random.randint(5, 20) if high_risk
                                      else random.randint(0, 5))
    checks["registered_owner"] = person
    return checks


def generate_ip_checks(
    has_secondary: bool, profile: scenarios.RiskProfile,
    market: refdata.Market,
) -> Dict[str, Any]:
    local = random.random() < profile.address_match
    geo_market = market if local else refdata.resolve_market()
    high_risk = profile.name != "clean"
    return {
        "error": _error(),
        "warnings": _warnings(IP_WARNINGS),
        "is_valid": random.random() < 0.95,
        "proxy_risk": random.random() < (0.4 if high_risk else 0.08),
        "geolocation": {
            "postal_code": refdata.zip_for(geo_market),
            "city_name": geo_market.city.title(),
            "subdivision": geo_market.state,
            "country_name": "United States",
            "country_code": "US",
            "continent_code": "NA",
        },
        "match_to_primary_name": _name_match(
            random.random() < profile.echo_input_identity),
        "match_to_secondary_name": (
            _name_match(random.random() < 0.5) if has_secondary else None
        ),
        "distance_from_primary_address": _distance(local),
        "distance_from_secondary_address": _distance(random.random() < 0.4) if has_secondary else None,
        "distance_from_primary_phone": _distance(local),
        "distance_from_secondary_phone": _distance(random.random() < 0.4) if has_secondary else None,
    }


# ── Request-vs-truth comparison (baseline mode) ─────────────────────────────

def _norm(value: Any) -> Optional[str]:
    return str(value).strip().lower() if value is not None else None


def _truth_person_shape(truth_person: Dict[str, Any]) -> Dict[str, Any]:
    """subscriber/resident/registered_owner block echoing the truth person."""
    lo = 5 * random.randint(4, 17)
    return {
        "name": f"{truth_person['first_name']} {truth_person['last_name']}",
        "age_range": {"from": lo, "to": lo + 4},
    }


def _name_matches(params: Dict[str, Any], prefix: str,
                  truth_person: Dict[str, Any]) -> bool:
    full = params.get(f"{prefix}.name")
    if full:
        truth_full = f"{truth_person['first_name']} {truth_person['last_name']}"
        return _norm(full) == _norm(truth_full)
    return (_norm(params.get(f"{prefix}.firstname"))
            == _norm(truth_person["first_name"])
            and _norm(params.get(f"{prefix}.lastname"))
            == _norm(truth_person["last_name"]))


def _request_address(params: Dict[str, Any], prefix: str
                     ) -> Optional[Dict[str, str]]:
    if not (params.get(f"{prefix}.address.street_line_1")
            or params.get(f"{prefix}.address.postal_code")):
        return None
    return {
        "street_line_1": params.get(f"{prefix}.address.street_line_1") or "",
        "city": params.get(f"{prefix}.address.city") or "",
        "state_code": params.get(f"{prefix}.address.state_code") or "",
        "postal_code": params.get(f"{prefix}.address.postal_code") or "",
    }


def _address_match_level(req_addr: Optional[Dict[str, str]],
                         truth_addr: Dict[str, str]) -> Optional[str]:
    """Component ladder from the MATCH_LEVELS vocabulary."""
    if req_addr is None:
        return None
    street = _norm(req_addr["street_line_1"]) == _norm(truth_addr["street_line_1"])
    city = _norm(req_addr["city"]) == _norm(truth_addr["city"])
    state = _norm(req_addr["state_code"]) == _norm(truth_addr["state_code"])
    postal = _norm(req_addr["postal_code"]) == _norm(truth_addr["postal_code"])
    if street and city and state and postal:
        return "Match"
    if postal:
        return "Postal match"
    if city and state:
        return "City match"
    if state:
        return "State match"
    return "No match"


def _derived_phone_checks(params: Dict[str, Any], prefix: str,
                          truth_person: Dict[str, Any]) -> Dict[str, Any]:
    phone_matches = _norm(params.get(f"{prefix}.phone")) == \
        _norm(truth_person["phone"])
    req_addr = _request_address(params, prefix)
    if phone_matches:
        subscriber = _truth_person_shape(truth_person)
        name_level = ("Match" if _name_matches(params, prefix, truth_person)
                      else "No match")
        addr_level = _address_match_level(req_addr, truth_person["address"])
    else:
        # Unknown phone: it belongs to some stranger.
        subscriber = _person()
        name_level = "No match"
        addr_level = "No match" if req_addr is not None else None
    return {
        "error": None,
        "warnings": [],
        "is_valid": True,
        "country_code": "US",
        "is_commercial": False,
        "line_type": random.choice(["Landline", "Mobile"]),
        "carrier": random.choice(CARRIERS),
        "is_prepaid": not phone_matches and random.random() < 0.4,
        "match_to_name": name_level,
        "match_to_address": addr_level,
        "subscriber": subscriber,
    }


def _derived_address_checks(params: Dict[str, Any], prefix: str,
                            truth_person: Dict[str, Any],
                            secondary: bool = False) -> Dict[str, Any]:
    req_addr = _request_address(params, prefix)
    level = _address_match_level(req_addr, truth_person["address"])
    complete = req_addr is not None and all(req_addr.values())
    if level == "Match":
        resident = _truth_person_shape(truth_person)
        name_level = ("Match" if _name_matches(params, prefix, truth_person)
                      else "No match")
    else:
        resident = _person()
        name_level = "No match"
    checks: Dict[str, Any] = {
        "error": None,
        "warnings": [],
        "is_valid": True,
        "input_completeness": "Complete" if complete else "Partial",
        "match_to_name": name_level,
        "resident": resident,
        "is_commercial": False,
        "is_forwarder": False,
        "type": random.choice(["Single unit", "Multi unit"]),
    }
    if secondary:
        checks["distance_from_primary_address"] = _distance()
        checks["linked_to_primary_resident"] = level == "Match"
    return checks


def _derived_email_checks(params: Dict[str, Any], prefix: str,
                          truth_person: Dict[str, Any],
                          primary: bool = False) -> Dict[str, Any]:
    email_matches = _norm(params.get(f"{prefix}.email_address")) == \
        _norm(truth_person["email"])
    req_addr = _request_address(params, prefix)
    if email_matches:
        owner = _truth_person_shape(truth_person)
        name_level = ("Match" if _name_matches(params, prefix, truth_person)
                      else "No match")
        addr_level = _address_match_level(req_addr, truth_person["address"])
    else:
        owner = _person()
        name_level = "No match"
        addr_level = "No match" if req_addr is not None else None
    checks: Dict[str, Any] = {
        "error": None,
        "warnings": [],
        "is_valid": True,
        "is_autogenerated": False,
        "is_disposable": not email_matches and random.random() < 0.3,
        "email_first_seen_days": (random.randint(1500, 5000) if email_matches
                                  else random.randint(0, 400)),
        "email_domain_creation_days": random.randint(365, 10000),
        "match_to_name": name_level,
        "match_to_address": addr_level,
    }
    if primary:
        checks["email_risk_score"] = (round(random.uniform(0.0, 0.25), 2)
                                      if email_matches
                                      else round(random.uniform(0.5, 1.0), 2))
        checks["mailbox_velocity"] = random.randint(0, 20)
    checks["registered_owner"] = owner
    return checks


def _derived_ip_checks(params: Dict[str, Any], truth: Dict[str, Any],
                       has_secondary: bool) -> Dict[str, Any]:
    ip_matches = _norm(params.get("ip_address")) == _norm(truth["ip"])
    primary = truth["primary"]
    if ip_matches:
        # The truth person's IP geolocates to their home city.
        geo = {
            "postal_code": primary["address"]["postal_code"][:5],
            "city_name": primary["address"]["city"],
            "subdivision": primary["address"]["state_code"],
            "country_name": "United States",
            "country_code": "US",
            "continent_code": "NA",
        }
        primary_name_level = ("Match"
                              if _name_matches(params, "primary", primary)
                              else "No match")
        near, far = (0, 15), (0, 25)
    else:
        geo = {
            "postal_code": fake.postcode()[:5],
            "city_name": fake.city(),
            "subdivision": random.choice(STATE_ABBRS),
            "country_name": "United States",
            "country_code": "US",
            "continent_code": "NA",
        }
        primary_name_level = "No match"
        near, far = (100, 2500), (100, 2500)
    return {
        "error": None,
        "warnings": [],
        "is_valid": True,
        "proxy_risk": False,
        "geolocation": geo,
        "match_to_primary_name": primary_name_level,
        "match_to_secondary_name": (primary_name_level if has_secondary
                                    else None),
        "distance_from_primary_address": random.randint(*near),
        "distance_from_secondary_address": (random.randint(*far)
                                            if has_secondary else None),
        "distance_from_primary_phone": random.randint(*near),
        "distance_from_secondary_phone": (random.randint(*far)
                                          if has_secondary else None),
    }


def _mismatch_counts(params: Dict[str, Any],
                     truth: Dict[str, Any]) -> Tuple[int, int]:
    """(mismatched, evaluated) over the primary inputs + ip — drives the
    identity score bands."""
    primary = truth["primary"]
    evaluated = mismatched = 0

    def tally(bad: bool) -> None:
        nonlocal evaluated, mismatched
        evaluated += 1
        mismatched += bad

    if (params.get("primary.name") or params.get("primary.firstname")
            or params.get("primary.lastname")):
        tally(not _name_matches(params, "primary", primary))
    if params.get("primary.phone"):
        tally(_norm(params["primary.phone"]) != _norm(primary["phone"]))
    if params.get("primary.email_address"):
        tally(_norm(params["primary.email_address"]) != _norm(primary["email"]))
    req_addr = _request_address(params, "primary")
    if req_addr is not None:
        tally(_address_match_level(req_addr, primary["address"]) != "Match")
    if params.get("ip_address"):
        tally(_norm(params["ip_address"]) != _norm(truth["ip"]))
    return mismatched, evaluated


# ── Flag-driven baseline (match_flag mode) ──────────────────────────────────
# The Ekata generator has no match_flag argument of its own — flag-driven
# behaviour comes from the `baseline` truth identity it compares the request
# against. `flagged_baseline` builds that truth FROM the request so the
# comparison lands in the requested 0/1/2 category, giving the orchestrator
# and the static-sample script the same control the GIACT/EWS generators get
# from `match_flag`.

def _different(value: Any, make: Callable[[], str]) -> str:
    """A fresh value guaranteed to differ from `value`."""
    new = make()
    while _norm(new) == _norm(value):
        new = make()
    return new


def _params_person_truth(params: Dict[str, Any], prefix: str,
                         deviated: List[str], dev_all: bool
                         ) -> Dict[str, Any]:
    """One truth person derived from the request's params: matching fields
    copy the request (gaps filled synthetically), deviated fields differ."""
    full = params.get(f"{prefix}.name")
    if full and " " in full:
        first, _, last = full.partition(" ")
    else:
        first = params.get(f"{prefix}.firstname") or fake.first_name()
        last = params.get(f"{prefix}.lastname") or fake.last_name()
    if dev_all:
        first = _different(first, fake.first_name)
        last = _different(last, fake.last_name)

    def dev(element: str) -> bool:
        return dev_all or element in deviated

    phone = params.get(f"{prefix}.phone") or fake.numerify("##########")
    if dev("phone"):
        phone = _different(phone, lambda: fake.numerify("##########"))
    email = params.get(f"{prefix}.email_address") or fake.email()
    if dev("email"):
        email = _different(email, fake.email)

    address = {
        "street_line_1": params.get(f"{prefix}.address.street_line_1")
        or fake.street_address(),
        "city": params.get(f"{prefix}.address.city") or fake.city(),
        "state_code": params.get(f"{prefix}.address.state_code")
        or random.choice(STATE_ABBRS),
        "postal_code": params.get(f"{prefix}.address.postal_code")
        or fake.postcode()[:5],
    }
    if dev("address"):
        # Keep city/state/zip so the deviation grades "City match" — visibly
        # partial rather than a different person across the country.
        address = dict(address)
        address["street_line_1"] = _different(address["street_line_1"],
                                              fake.street_address)
    if dev_all:
        address = {
            "street_line_1": fake.street_address(),
            "city": fake.city(),
            "state_code": random.choice(STATE_ABBRS),
            "postal_code": fake.postcode()[:5],
        }
    return {"first_name": first, "last_name": last, "phone": phone,
            "email": email, "address": address}


def flagged_baseline(params: Dict[str, Any],
                     match_flag: int) -> Dict[str, Any]:
    """Truth identity that makes `generate_identity_check_response(params,
    baseline=...)` come out in the requested category.

    0 (complete) — the truth equals the request, so every check block reads
    Match. 1 (partial) — one (sometimes two) of the phone/email/address
    elements deviates in the truth; the name always stays matching. A partial
    needs at least two of those elements in the request to be expressible —
    with fewer, no element is deviated and the response reads complete.
    2 (no match) — every identity field including the name deviates.
    """
    if match_flag not in (0, 1, 2):
        raise ValueError(
            f"match_flag must be 0 (complete), 1 (partial), or "
            f"2 (no match); got {match_flag}")
    dev_all = match_flag == 2

    deviated: List[str] = []
    if match_flag == 1:
        present = [e for e, keys in (
            ("phone", ("primary.phone",)),
            ("email", ("primary.email_address",)),
            ("address", ("primary.address.street_line_1",
                         "primary.address.postal_code")),
        ) if any(params.get(k) for k in keys)]
        if len(present) >= 2:
            deviated = (random.sample(present, 2)
                        if len(present) == 3 and random.random() < 0.25
                        else [random.choice(present)])

    truth: Dict[str, Any] = {
        "kind": "ekata",
        "primary": _params_person_truth(params, "primary", deviated, dev_all),
        "ip": (params.get("ip_address") or fake.ipv4_public()) if not dev_all
        else _different(params.get("ip_address"), fake.ipv4_public),
    }
    if any(params.get(k) for k in (
            "secondary.name", "secondary.firstname", "secondary.lastname",
            "secondary.phone", "secondary.email_address",
            "secondary.address.street_line_1",
            "secondary.address.postal_code")):
        # The secondary person follows the primary's category: matching for
        # complete/partial (deviations apply to the primary only), fresh for
        # no_match — same rule as the request generator.
        truth["secondary"] = _params_person_truth(params, "secondary", [],
                                                  dev_all)
    return truth


# ── Top-level response ───────────────────────────────────────────────────────

def generate_identity_check_response(
    params: Dict[str, Any],
    baseline: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build a full identity_check response dict.

    `params` holds the flattened query parameters (keys like "primary.phone").
    Without a `baseline` they only decide which check blocks appear and all
    response data is synthetic; with a `baseline` (the stored truth identity)
    every match level, person block, and identity score is derived by
    comparing the request against the truth — see the module docstring.
    """
    def given(*keys: str) -> bool:
        return any(params.get(k) for k in keys)

    profile = _profile_for(params)
    market = refdata.resolve_market(params.get("primary.address.state_code"))

    has_secondary = given(
        "secondary.name", "secondary.firstname", "secondary.lastname",
        "secondary.phone", "secondary.email_address",
        "secondary.address.street_line_1", "secondary.address.postal_code",
        "secondary.address.country_code",
    )
    # Baseline mode derives every value from the stored truth identity, so it
    # short-circuits before the profile-driven synthetic draw below.
    if baseline is not None:
        return _derive_identity_check_response(params, baseline, given,
                                               has_secondary)

    # One identity shared across blocks, echoing the queried name like the
    # sample. Ekata's identity_check_score runs 0-500, higher = *safer*, so a
    # riskier profile scores lower — the inverse of the profile's 0-1000 range.
    person = _person(params.get("primary.name"))
    # Drawn across the profile's whole range, not pinned to its floor. Using
    # only `[0]` collapsed each profile to a single point, and the jitter below
    # could not bridge the gap between clean and fraud — the score separated
    # the labels perfectly (AUC 1.0), which is the leak `scenarios` warns of.
    risk_frac = random.randint(*profile.identity_risk_range) / 1000.0
    id_score = max(0, int(500 * (1 - risk_frac) + random.randint(-60, 60)))
    upcoming = max(0, int(500 * (1 - risk_frac) + random.randint(-80, 80)))
    net_score = round(min(1.0, risk_frac + random.uniform(-0.1, 0.1)), 3)
    n_alerts = (random.choices([0, 1, 2], [3, 4, 3])[0]
                if profile.name != "clean"
                else random.choices([0, 1], [9, 1])[0])

    return {
        "request": None,
        "primary_phone_checks": (
            generate_phone_checks(person, profile) if given("primary.phone") else None
        ),
        "secondary_phone_checks": (
            generate_phone_checks(person, profile) if given("secondary.phone") else None
        ),
        "primary_address_checks": (
            generate_address_checks(person, profile)
            if given("primary.address.street_line_1", "primary.address.postal_code")
            else None
        ),
        "secondary_address_checks": (
            generate_address_checks(person, profile, secondary=True)
            if given("secondary.address.street_line_1", "secondary.address.postal_code")
            else None
        ),
        "primary_email_address_checks": (
            generate_email_checks(person, profile, primary=True)
            if given("primary.email_address") else None
        ),
        "secondary_email_address_checks": (
            generate_email_checks(person, profile)
            if given("secondary.email_address") else None
        ),
        "ip_address_checks": (
            generate_ip_checks(has_secondary, profile, market)
            if given("ip_address") else None
        ),
        "identity_check_score": min(id_score, 500),
        "identity_network_score": max(0.0, net_score),
        "upcoming_identity_check_score": min(upcoming, 500),
        "alerts": random.sample(ALERT_POOL, k=min(n_alerts, len(ALERT_POOL))),
    }


def _derive_identity_check_response(params: Dict[str, Any],
                                    truth: Dict[str, Any],
                                    given, has_secondary: bool
                                    ) -> Dict[str, Any]:
    """Baseline mode: every block is derived by comparing params to truth."""
    primary = truth["primary"]
    # A secondary person in the request without a stored secondary truth
    # cannot match anyone the vendor knows — treat them as a stranger.
    secondary = truth.get("secondary") or {
        "first_name": fake.first_name(), "last_name": fake.last_name(),
        "phone": "0000000000", "email": "",
        "address": {"street_line_1": "", "city": "", "state_code": "",
                    "postal_code": ""},
    }

    mismatched, evaluated = _mismatch_counts(params, truth)
    if mismatched == 0:
        score = random.randint(10, 120)
        network = round(random.uniform(0.0, 0.30), 3)
        alerts: List[Dict[str, str]] = []
    elif mismatched < evaluated:
        score = random.randint(150, 300)
        network = round(random.uniform(0.30, 0.60), 3)
        alerts = random.sample(ALERT_POOL, k=random.randint(0, 1))
    else:
        score = random.randint(330, 500)
        network = round(random.uniform(0.60, 0.99), 3)
        alerts = [ALERT_POOL[2]]  # identity_mismatch
        if random.random() < 0.5:
            alerts.append(random.choice(ALERT_POOL[:2]))

    return {
        "request": None,
        "primary_phone_checks": (
            _derived_phone_checks(params, "primary", primary)
            if given("primary.phone") else None
        ),
        "secondary_phone_checks": (
            _derived_phone_checks(params, "secondary", secondary)
            if given("secondary.phone") else None
        ),
        "primary_address_checks": (
            _derived_address_checks(params, "primary", primary)
            if given("primary.address.street_line_1",
                     "primary.address.postal_code")
            else None
        ),
        "secondary_address_checks": (
            _derived_address_checks(params, "secondary", secondary,
                                    secondary=True)
            if given("secondary.address.street_line_1",
                     "secondary.address.postal_code")
            else None
        ),
        "primary_email_address_checks": (
            _derived_email_checks(params, "primary", primary, primary=True)
            if given("primary.email_address") else None
        ),
        "secondary_email_address_checks": (
            _derived_email_checks(params, "secondary", secondary)
            if given("secondary.email_address") else None
        ),
        "ip_address_checks": (
            _derived_ip_checks(params, truth, has_secondary)
            if given("ip_address") else None
        ),
        "identity_check_score": score,
        "identity_network_score": network,
        "upcoming_identity_check_score": max(
            0, min(500, score + random.randint(-40, 40))),
        "alerts": alerts,
    }
