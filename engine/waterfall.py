"""
waterfall.py

Deterministic, per-transfer-type waterfall for the client demo. Unlike
engine/orchestrator_stub.py (which asks an LLM to plan which vendors to
call, and can call several), this path is fully config-driven and
repeatable: the route always comes from config/waterfall_rules.yaml, one
vendor is tried at a time, and the FIRST one to return usable data wins
(stop-on-success) -- everything after that is skipped, never called.

Three routes, chosen by classify_transfer_type() from the request's own
fields (no internal tag read from the request):
  - domestic:      EWS -> LSEG, falls through on no-data/outage
  - international:  LSEG only, no fallback -- no data/outage here means
                     decline, not a reroute
  - onboarding:      Ekata only -- there's no account/routing number yet
                     for EWS/LSEG to check anything against

This does NOT replace orchestrator_stub.py -- the production/agentic path
is untouched. This is the demo-only deterministic path, wired up via
pipeline.py:run_pipeline_waterfall_demo().

Each vendor's raw response is classified success / no_data / outage via
engine/vendor_scores.py's VENDOR_SCORERS (an "outage" here specifically
means the call raised VendorUnavailable -- e.g. the demo's simulated EWS
outage, see data/demo_scenarios.py / engine/vendor_gateway.py).
"""

from __future__ import annotations
from pathlib import Path
from typing import Any, Callable, Optional
import yaml

from engine.vendor_gateway import VendorUnavailable
from engine.vendor_scores import VENDOR_SCORERS

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "waterfall_rules.yaml"

DOMESTIC = "domestic"
INTERNATIONAL = "international"
ONBOARDING = "onboarding"


def load_waterfall_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def classify_transfer_type(request: dict) -> str:
    """Determines which route a request uses, from its own fields --
    no internal tag is read.

      - swift_code present, routing_number absent -> international
      - routing_number AND account_number present  -> domestic
      - neither                                     -> onboarding
    """
    has_swift = bool((request.get("swift_code") or "").strip())
    has_routing = bool((request.get("routing_number") or "").strip())
    has_account = bool((request.get("account_number") or "").strip())

    if has_swift and not has_routing:
        return INTERNATIONAL
    if has_routing and has_account:
        return DOMESTIC
    return ONBOARDING


def run_waterfall(
    request: dict,
    mcp_call: Callable[[str, dict], dict],
    on_step: Optional[Callable[[str, str, str], None]] = None,
    call_all: bool = False,
    order_override: Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    call_all: when True, every vendor in the order is called regardless of
    earlier successes -- used ONLY by the domestic "Random" scenario (see
    data/demo_scenarios.py:DOMESTIC_RANDOM), which exists specifically to
    show all three vendors' own responses side by side. Every other
    scenario leaves this False and keeps the normal stop-on-success
    waterfall behavior, completely unchanged.

    order_override: bypasses config/waterfall_rules.yaml's route order for
    this one call. Used by the same Random scenario -- the domestic route's
    OWN config stays [ews_check, giact_verify] (Ekata deliberately removed
    as a domestic fallback, per an earlier decision); Random needs Ekata
    called too, so it passes its own 3-vendor order here rather than that
    decision being silently reverted for every domestic case.

    terminal_tool is still whichever vendor succeeded FIRST, even when
    call_all=True -- that's still what the canonical/composite layers use,
    unaffected by the extra vendors called afterward.

    Returns:
        {
          "transfer_type":  "domestic" | "international" | "onboarding",
          "fallback_allowed": bool,  -- from the route's config, informational
          "vendor_results": {tool_name: raw_response_or_error, ...},
          "vendor_scores":  {tool_name: {"vendor", "status", "score"}, ...},
          "waterfall_trail": [{"vendor", "tool", "outcome", "reason"}, ...],
          "terminal_tool": tool_name of whichever vendor's data was used,
                            or None if every vendor in the route came back
                            empty/unavailable.
        }
    """

    def emit(label: str, detail: str = "", state: str = "running") -> None:
        if on_step:
            on_step(label, detail, state)

    config = load_waterfall_config()
    transfer_type = classify_transfer_type(request)
    route = config["routes"][transfer_type]
    order: list[str] = order_override if order_override is not None else route["order"]
    labels: dict[str, str] = config["labels"]

    vendor_results: dict[str, Any] = {}
    vendor_scores: dict[str, Any] = {}
    trail: list[dict[str, Any]] = []
    terminal_tool: Optional[str] = None

    for tool_name in order:
        label = labels.get(tool_name, tool_name)
        emit(f"Calling {label}", "Waiting for response...", "running")

        try:
            raw = mcp_call(tool_name, request)
        except VendorUnavailable as e:
            vendor_results[tool_name] = {"status": "error", "detail": str(e)}
            vendor_scores[tool_name] = {"vendor": label, "status": "outage", "score": None}
            reason = f"{label} unavailable \u2014 {e}"
            emit(f"Calling {label}",
                 f"\u26a1 Outage detected \u2014 automatically rerouted to next source (no manual intervention)",
                 "error")
            trail.append({"vendor": label, "tool": tool_name, "outcome": "outage", "reason": reason})
            continue

        vendor_results[tool_name] = raw
        scorer = VENDOR_SCORERS.get(tool_name)
        score_info = scorer(raw) if scorer else {"vendor": label, "status": "success", "score": None}
        vendor_scores[tool_name] = score_info

        if score_info["status"] == "success":
            emit(f"Calling {label}", f"Responded \u2014 score {score_info['score']}", "complete")
            trail.append({"vendor": label, "tool": tool_name, "outcome": "success", "reason": None})
            if terminal_tool is None:
                terminal_tool = tool_name
            if not call_all:
                break  # stop-on-success -- everything after this is not called
        else:
            reason = f"{label} has no data for this record"
            emit(f"Calling {label}", "No data \u2014 falling back" if route["fallback"] else "No data \u2014 no fallback for this route", "error")
            trail.append({"vendor": label, "tool": tool_name, "outcome": "no_data", "reason": reason})

    return {
        "transfer_type": transfer_type,
        "fallback_allowed": route["fallback"],
        "vendor_results": vendor_results,
        "vendor_scores": vendor_scores,
        "waterfall_trail": trail,
        "terminal_tool": terminal_tool,
    }


def synthesize_giact_from_ekata(ekata_response: dict, submitted: dict) -> dict:
    """
    Demo-path bridge, used ONLY for onboarding requests (Ekata is the
    sole source -- there's no account/routing number for EWS/LSEG to check
    anything against). Same problem as synthesize_giact_from_ews() above:
    engine/canonical_mapper.py's identity.record_found and owner.name_match/
    address_match are structurally GIACT-only fields (see map_giact()) --
    Ekata's own response never populates them, no matter how strong Ekata's
    own match signals are.

    This approximates a GIACT-shaped payload from Ekata's own
    phone/email/address "match_to_name" checks, so the identity/owner rules
    see a populated record when Ekata alone found a strong match, instead
    of unconditionally reading "no GIACT record". Name and address come
    from the submitted request (what the applicant claimed), gated on
    Ekata's own match verdicts -- not invented data. pipeline.py re-tags
    the resulting canonical._provenance entries from "giact" to "ekata" so
    reason-code source attribution stays honest.

    engine/orchestrator_stub.py's production path never calls this.
    """
    phone_checks = ekata_response.get("primary_phone_checks") or {}
    email_checks = ekata_response.get("primary_email_address_checks") or {}
    addr_checks = ekata_response.get("primary_address_checks") or {}

    name_matched = phone_checks.get("match_to_name") == "Match" or email_checks.get("match_to_name") == "Match"
    address_matched = addr_checks.get("match_to_name") == "Match"

    if not name_matched:
        return {"MatchedPersonData": [], "ConsumerAlertMessages": [], "OfacListPotentialMatches": []}

    address = submitted.get("address") or {}
    return {
        "MatchedPersonData": [{
            "FirstName": submitted.get("first_name", ""),
            "LastName": submitted.get("last_name", ""),
            "AddressRecords": [{
                "AddressLine1": address.get("address_line1", ""),
                "City": address.get("city", ""),
                "State": address.get("state", ""),
                "ZipCode": address.get("zip_code", ""),
                "Status": "Current",
            }] if address_matched else [],
        }],
        "ConsumerAlertMessages": [],
        "OfacListPotentialMatches": [],
        "AccountResponseCode": "R0",
        "FundsConfirmationResult": None,  # no bank account exists yet at onboarding
    }
