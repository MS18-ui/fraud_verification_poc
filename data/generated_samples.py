"""
generated_samples.py

Builds synthetic sample cases directly from the request_generator.py suite
(data/generators/), replacing the master-dataset-based "Load a sample
record" path. Design: ONE shared claimed identity per case, submitted
identically to all three vendors (exactly as a real client request would
be) -- each vendor then INDEPENDENTLY decides, via its own match category,
whether its own records agree with that claim. That's what makes vendors
legitimately disagree (realistic) without three unrelated random identities
(the earlier bug).

Mechanics, verified against the actual generator code before writing this:
  - GIACT (generate_post_inquiry_result) and EWS (generate_account_
    verification_response) derive their match determination internally
    from (submitted identity + match_flag) alone -- no separate truth
    object needed.
  - Ekata (generate_identity_check_response) genuinely needs an external
    `baseline` (truth) to compare the submitted params against -- so a
    baseline is built here by deviating from the claimed identity,
    independently, per Ekata's own chosen category.

Because build_sample_request() returns a REQUEST (not a canonical), it
should be run through pipeline.run_pipeline() -- exercising the full
7-step pipeline (orchestration, LLM tool-planning, vendor calls) -- rather
than run_pipeline_from_canonical(), which the master-dataset path used and
which skipped orchestration entirely.
"""

from __future__ import annotations
import random
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

GENERATORS_DIR = Path(__file__).resolve().parent / "generators"
if str(GENERATORS_DIR) not in sys.path:
    sys.path.insert(0, str(GENERATORS_DIR))

try:
    import request_generator as _req_gen
    import response_generator as _giact_resp_gen
    import ekata_response_generator as _ekata_resp_gen
    import ews_response_generator as _ews_resp_gen
    from faker import Faker as _Faker
    from data import scenarios as _scenarios
    GENERATOR_READY = True
except ImportError as _e:
    GENERATOR_READY = False
    _IMPORT_ERROR = str(_e)

CATEGORY_TO_FLAG = {"full_match": 0, "partial_match": 1, "no_match": 2}

# Per-entity_id state so repeated vendor calls within one orchestration run
# (and Streamlit reruns) stay consistent for the same generated case.
_claimed_identity_cache: dict[str, dict[str, Any]] = {}
_category_cache: dict[str, dict[str, str]] = {}
_typology_cache: dict[str, str] = {}
_is_fraud_registry: dict[str, bool] = {}


def _build_claimed_identity(rng: random.Random, faker, is_business: bool) -> dict[str, Any]:
    """The one identity submitted to all three vendors -- what a real
    client request would contain."""
    address = {
        "street_line_1": faker.street_address(), "city": faker.city(),
        "state_code": faker.state_abbr(include_territories=False), "postal_code": faker.postcode(),
    }
    first_name = None if is_business else faker.first_name()
    last_name = None if is_business else faker.last_name()
    business_name = faker.company() if is_business else None
    return {
        "first_name": first_name, "last_name": last_name,
        "name": business_name if is_business else f"{first_name} {last_name}",
        "business_name": business_name,
        "address_line1": address["street_line_1"], "city": address["city"],
        "state": address["state_code"], "zip_code": address["postal_code"],
        "address": address,
        "phone": faker.numerify("##########"), "email": faker.email(),
        "tax_id": faker.numerify("#########"),
        "date_of_birth": None if is_business else faker.date_of_birth(minimum_age=21, maximum_age=80).isoformat(),
        "routing_number": faker.aba(), "account_number": faker.numerify("##########"),
    }


def _to_giact_customer(claimed: dict) -> dict:
    return {
        "FirstName": claimed["first_name"], "LastName": claimed["last_name"],
        "BusinessName": claimed["business_name"], "AddressLine1": claimed["address_line1"],
        "City": claimed["city"], "State": claimed["state"], "ZipCode": claimed["zip_code"],
        "PhoneNumber": claimed["phone"], "TaxId": claimed["tax_id"],
        "DateOfBirth": claimed["date_of_birth"], "EmailAddress": claimed["email"],
    }


def _to_ekata_params(claimed: dict) -> dict:
    params = {
        "primary.address.street_line_1": claimed["address_line1"],
        "primary.address.city": claimed["city"],
        "primary.address.state_code": claimed["state"],
        "primary.address.postal_code": claimed["zip_code"],
        "primary.phone": claimed["phone"],
        "primary.email_address": claimed["email"],
    }
    if claimed["business_name"]:
        params["primary.name"] = claimed["business_name"]
    else:
        params["primary.firstname"] = claimed["first_name"]
        params["primary.lastname"] = claimed["last_name"]
    return params


def _build_ekata_baseline(claimed: dict, rng: random.Random, faker, category: str) -> dict:
    """Ekata needs an external truth to compare submitted params against.
    Built by deviating from the claimed identity -- independently, per
    Ekata's own chosen category -- so the submitted params (undeviated,
    identical to what GIACT/EWS also received) can genuinely mismatch this
    vendor's own 'file' without touching what was actually submitted."""
    dev_all = category == "no_match"
    deviated: list[str] = []
    if category == "partial_match":
        elements = ["phone", "email", "address"]
        deviated = rng.sample(elements, 2) if rng.random() < 0.25 else [rng.choice(elements)]
    elif dev_all:
        deviated = ["identity"]

    def dev(el: str) -> bool:
        return dev_all or el in deviated

    first, last = (faker.first_name(), faker.last_name()) if dev_all else (claimed["first_name"], claimed["last_name"])
    address = ({"street_line_1": faker.street_address(), "city": faker.city(),
                "state_code": faker.state_abbr(include_territories=False), "postal_code": faker.postcode()}
               if dev("address") else
               {"street_line_1": claimed["address_line1"], "city": claimed["city"],
                "state_code": claimed["state"], "postal_code": claimed["zip_code"]})

    primary = {
        "first_name": first, "last_name": last,
        "phone": faker.numerify("##########") if dev("phone") else claimed["phone"],
        "email": faker.email() if dev("email") else claimed["email"],
        "address": address,
    }
    return {"kind": "ekata", "primary": primary, "ip": faker.ipv4_public()}


def _to_ews_request(claimed: dict, match_flag: int) -> dict:
    return {
        "account": {"routingNumber": claimed["routing_number"], "accountNumber": claimed["account_number"]},
        "identity": {
            "firstName": claimed["first_name"], "lastName": claimed["last_name"],
            "businessName": claimed["business_name"], "addressLine1": claimed["address_line1"],
            "city": claimed["city"], "state": claimed["state"], "zipCode": claimed["zip_code"],
            "phoneNumber": claimed["phone"], "taxId": claimed["tax_id"], "emailAddress": claimed["email"],
        },
        "matchFlag": match_flag,
    }


def _get_case_state(entity_id: str, account_holder_type: str, is_fraud: Optional[bool]) -> None:
    if entity_id in _claimed_identity_cache:
        return
    rng = random.Random(entity_id)
    faker = _Faker("en_US")
    faker.seed_instance(rng.random())
    is_business = account_holder_type == "business"

    if is_fraud is None:
        is_fraud = rng.random() < 0.20  # demo mix skews above the ~1.7% real base rate on purpose

    typology = _scenarios.pick_typology(is_business, rng) if is_fraud else "clean"
    weights = ({"full_match": 15, "partial_match": 35, "no_match": 50} if is_fraud
               else {"full_match": 80, "partial_match": 15, "no_match": 5})
    categories = {tool: _req_gen.choose_category(rng, weights)
                  for tool in ("giact_verify", "ekata_identity_check", "ews_check")}

    _claimed_identity_cache[entity_id] = _build_claimed_identity(rng, faker, is_business)
    _category_cache[entity_id] = categories
    _typology_cache[entity_id] = typology
    _is_fraud_registry[entity_id] = is_fraud


def build_sample_request(account_holder_type: str = "person", is_fraud: Optional[bool] = None) -> dict[str, Any]:
    """Builds a fresh sample case, returned shaped like a live client
    request. Feed sample['request'] into pipeline.run_pipeline() to
    exercise the full pipeline, not run_pipeline_from_canonical()."""
    if not GENERATOR_READY:
        raise RuntimeError(f"Generator suite not importable: {_IMPORT_ERROR}")

    entity_id = f"SYN-{uuid.uuid4().hex[:10].upper()}"
    _get_case_state(entity_id, account_holder_type, is_fraud)
    claimed = _claimed_identity_cache[entity_id]

    request = {
        "unique_id": entity_id,
        "first_name": claimed["first_name"], "last_name": claimed["last_name"],
        "name": claimed["name"], "business_name": claimed["business_name"],
        "address_line1": claimed["address_line1"], "city": claimed["city"],
        "state": claimed["state"], "zip_code": claimed["zip_code"],
        "address": dict(claimed["address"]),
        "phone": claimed["phone"], "email": claimed["email"],
        "date_of_birth": claimed["date_of_birth"], "tax_id": claimed["tax_id"],
        "routing_number": claimed["routing_number"], "account_number": claimed["account_number"],
    }
    return {
        "entity_id": entity_id,
        "account_holder_type": account_holder_type,
        "request": request,
        "is_fraud": _is_fraud_registry[entity_id],
        "meta": {"typology": _typology_cache[entity_id], "vendor_categories": dict(_category_cache[entity_id])},
    }


def get_generated_vendor_response(tool_name: str, request: dict) -> dict[str, Any]:
    """Called by engine/vendor_gateway.py in DEMO_MODE. Derives that
    vendor's response independently from the SAME submitted request
    (the shared claimed identity), using that vendor's own cached match
    category and case typology."""
    entity_id = request.get("unique_id", "DEMO-DEFAULT")
    if entity_id not in _claimed_identity_cache:
        # A live/manual request not built via build_sample_request -- derive
        # case state from it on the fly, keyed the same way.
        is_business = bool(request.get("business_name"))
        _get_case_state(entity_id, "business" if is_business else "person", None)

    claimed = _claimed_identity_cache[entity_id]
    category = _category_cache[entity_id].get(tool_name, "full_match")
    typology = _typology_cache[entity_id]
    rng = random.Random(f"{entity_id}:{tool_name}")
    faker = _Faker("en_US")
    faker.seed_instance(rng.random())

    def with_scenario_resolver(module, fn):
        original = getattr(module, "SCENARIO_RESOLVER", None)
        module.SCENARIO_RESOLVER = lambda *_a, **_k: typology
        try:
            return fn()
        finally:
            module.SCENARIO_RESOLVER = original

    if tool_name == "giact_verify":
        inquiry = {
            "GVerifyEnabled": True, "GIdentifyEnabled": True, "OfacScanEnabled": True,
            "MatchFlag": CATEGORY_TO_FLAG[category], "Customer": _to_giact_customer(claimed),
            "Check": {"RoutingNumber": claimed["routing_number"],
                      "AccountNumber": claimed["account_number"], "AccountType": "Checking"},
        }
        giact_request = {"Inquiry": inquiry}
        raw = with_scenario_resolver(
            _giact_resp_gen,
            lambda: _giact_resp_gen.generate_post_inquiry_result(giact_request, match_flag=CATEGORY_TO_FLAG[category]),
        )
        # generate_post_inquiry_result returns {"PostInquiryResult": {...}} --
        # canonical_mapper.py / pipeline.py expect the unwrapped inner object
        # (confirmed against how the fixture-fallback path shapes this).
        return raw.get("PostInquiryResult", raw)

    if tool_name == "ekata_identity_check":
        params = _to_ekata_params(claimed)
        baseline = _build_ekata_baseline(claimed, rng, faker, category)
        return with_scenario_resolver(
            _ekata_resp_gen,
            lambda: _ekata_resp_gen.generate_identity_check_response(params, baseline=baseline),
        )

    if tool_name == "ews_check":
        ews_request = _to_ews_request(claimed, CATEGORY_TO_FLAG[category])
        return _ews_resp_gen.generate_account_verification_response(ews_request, match_flag=CATEGORY_TO_FLAG[category])

    return {}
