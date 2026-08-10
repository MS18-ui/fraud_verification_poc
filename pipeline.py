"""
pipeline.py

Single entry point tying every layer together, in order:

  1. Request intake (caller-supplied dict, already validated by the API layer)
  2. Agentic Orchestration -- engine/orchestrator_stub.py:orchestrate()
     (LLM tool-plan via engine/llm_agent.py, vendor calls via
     engine/vendor_gateway.py, deterministic has_min_fields gate)
  3. Canonical Normalization -- engine/canonical_mapper.py:build_canonical()
  4. Deterministic Scoring -- engine/decision.py:run_decision()
  5. Fraud Risk Model -- scoring/fraud_model.py:fraud_probability()
  6. Composite Risk + Decision -- scoring/composite.py:composite_result()
  7. LLM Explainability -- explainability/explainer.py:explain()

Both the FastAPI service (api.py) and the Streamlit demo (ui/app.py) call
run_pipeline() so there is exactly one implementation of "what a request
does," never two copies to keep in sync.
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Optional

from engine.orchestrator_stub import orchestrate
from engine.vendor_gateway import mcp_call


def _demo_mcp_call(tool_name: str, request: dict) -> dict:
    """Used ONLY by run_pipeline_waterfall_demo() below, via run_waterfall().
    Passes demo_scenario=True as a normal function argument -- not a field
    smuggled into the request dict -- so the request itself stays identical
    to what a real inbound API call would look like. run_pipeline() (the
    normal/agentic flow) keeps using mcp_call directly, unaffected."""
    return mcp_call(tool_name, request, demo_scenario=True)
from engine.llm_agent import agent_decide_tool_plan, agent_infer_missing_field
from engine.vendor_priority import get_priority_for
from engine.canonical_mapper import build_canonical
from engine.decision import run_decision
from engine.waterfall import run_waterfall, synthesize_giact_from_ekata
from engine.vendor_decision import decision_from_score, DECISION_LABEL, BAND_RANGE_TEXT
from data.demo_scenarios import classify_scenario, DOMESTIC_RANDOM
from engine.vendor_normalization import vendor_code_outcome, VENDOR_CODE_RULE_ID
from engine.reason_codes import enrich_reason_codes
from scoring.fraud_model import fraud_probability
from scoring.composite import composite_result
from explainability.explainer import explain

# Real vendor names for the "Vendor Requests" step detail (normal/agentic
# flow) -- previously showed only a bare count ("3 vendor(s) called").
VENDOR_DISPLAY_LABELS = {"ews_check": "EWS", "giact_verify": "GIACT", "ekata_identity_check": "Ekata"}


def _composite_step_status(final_decision: str) -> str:
    """Maps the final decision to a step-trail status so the "Composite
    Risk & Decision" node in the UI's orchestration-flow diagram renders
    red (decline), amber (review), or green (approved) -- the ONLY place
    color-by-outcome is applied; every other step stays plain green-on-
    complete like before."""
    if final_decision in ("HARD_DECLINE", "SOFT_DECLINE"):
        return "error"
    if final_decision == "MANUAL_REVIEW":
        return "warning"
    return "complete"


def _vendor_decision_step_status(decision: str) -> str:
    """Same idea as _composite_step_status, for the waterfall demo's
    Option B model (engine/vendor_decision.py) -- GO green, NO_GO amber,
    HIGH_RISK a distinct dark-orange (still visually different from a
    HARD_DECLINE red), HARD_DECLINE red."""
    if decision == "GO":
        return "complete"
    if decision == "NO_GO":
        return "warning"
    if decision == "HIGH_RISK":
        return "high_risk"
    return "error"  # HARD_DECLINE


def run_pipeline_from_canonical(canonical: dict, entity_id: str = None) -> dict[str, Any]:
    """Entry point for records that already have a canonical profile --
    i.e. rows from data/master_dataset_adapter.py, which is pre-flattened
    to the canonical level and has no raw request/vendor-call step to run.
    Starts at Deterministic Scoring (step 4); steps 1-3 are marked
    not_applicable in the trace rather than faked."""
    steps: list[dict[str, Any]] = [
        {"step": "Request Intake", "status": "not_applicable", "detail": "Loaded from master dataset (already past vendor response stage)"},
        {"step": "Agentic Orchestration", "status": "not_applicable", "detail": "No live vendor calls -- dataset already contains vendor results"},
        {"step": "Canonical Normalization", "status": "not_applicable", "detail": "Dataset is pre-flattened to canonical level"},
    ]

    decision_result = run_decision(canonical)
    steps.append({"step": "Deterministic Scoring", "status": "complete",
                  "detail": f"Outcomes: {decision_result['adjusted_outcomes']}"})

    fraud_result = fraud_probability(canonical)
    steps.append({"step": "Fraud Risk Model", "status": "complete",
                  "detail": f"Fraud probability: {fraud_result['fraud_probability']:.1%} ({fraud_result['source']})"})

    composite = composite_result(decision_result, fraud_result)
    steps.append({"step": "Composite Risk & Decision", "status": _composite_step_status(composite["final_decision"]),
                  "detail": f"Decision: {composite['final_decision']} ({composite['composite_score']}/100)"})

    explanation = explain(composite)
    steps.append({"step": "LLM Explainability", "status": "complete", "detail": explanation["source"]})

    return {
        "entity_id": entity_id or canonical.get("entity_id"),
        "vendor_results": {"note": "not applicable -- loaded from master dataset"},
        "canonical": canonical,
        "decision_result": decision_result,
        "fraud_result": fraud_result,
        "composite": composite,
        "explanation": explanation,
        "steps": steps,
    }


def run_pipeline_waterfall_demo(request: dict, account_holder_type: str = "person", on_step=None) -> dict[str, Any]:
    """Deterministic, per-transfer-type waterfall entry point for the
    client demo's scenarios (domestic direct / virtual account / EWS
    outage / international match / international no-data / onboarding).
    Deliberately bypasses the LLM tool-planner in
    engine/orchestrator_stub.py -- the demo waterfall's route must be
    repeatable every run, not subject to an LLM's plan varying run to
    run. run_pipeline() (the production/agentic path) is untouched.

    Steps 3-7 (canonical normalization onward) reuse exactly the same
    functions run_pipeline() uses -- only step 2 (orchestration) differs,
    plus the single-source bridge below (see engine/waterfall.py:
    synthesize_giact_from_ews / synthesize_giact_from_ekata for why it's
    needed), plus the international-route decline override."""
    def emit(label, detail="", state="running"):
        if on_step:
            on_step(label, detail, state)

    steps: list[dict[str, Any]] = []

    emit("Request Intake", f"{len(request)} fields received", "running")
    steps.append({"step": "Request Intake", "status": "complete",
                  "detail": f"{len(request)} fields received"})
    emit("Request Intake", f"{len(request)} fields received", "complete")

    emit("Waterfall Orchestration", "Determining route...", "running")
    is_random_all_vendors = classify_scenario(request) == DOMESTIC_RANDOM
    waterfall = run_waterfall(
        request=request, mcp_call=_demo_mcp_call, on_step=emit,
        call_all=is_random_all_vendors,
        order_override=["ews_check", "giact_verify", "ekata_identity_check"] if is_random_all_vendors else None,
    )
    transfer_type = waterfall["transfer_type"]
    trail_summary = " \u2192 ".join(f"{t['vendor']} ({t['outcome']})" for t in waterfall["waterfall_trail"])
    detail = f"[{transfer_type}] {trail_summary}"
    steps.append({"step": "Waterfall Orchestration", "status": "complete", "detail": detail})
    emit("Waterfall Orchestration", detail, "complete")

    emit("Canonical Normalization", "Unifying vendor response...", "running")
    vendor_results = waterfall["vendor_results"]

    # Vendor-side audit trail (2026-08-08 call): compare against whatever
    # was previously stored for this identity, BEFORE this run gets saved
    # -- see data/case_store.py:check_and_log_vendor_changes(). Best-effort;
    # never blocks the run if the DB is unavailable.
    from data.case_store import check_and_log_vendor_changes
    vendor_data_changes = check_and_log_vendor_changes(request.get("unique_id", "N/A"), vendor_results)

    giact_raw = vendor_results.get("giact_verify")
    ekata_raw = vendor_results.get("ekata_identity_check")
    ews_raw = vendor_results.get("ews_check")
    giact_raw = giact_raw if isinstance(giact_raw, dict) and "status" not in giact_raw else None
    ekata_raw = ekata_raw if isinstance(ekata_raw, dict) and "status" not in ekata_raw else None
    ews_raw = ews_raw if isinstance(ews_raw, dict) and "status" not in ews_raw else None

    # Onboarding-only bridge: Ekata is the sole source for onboarding
    # requests (no account/routing number for EWS/LSEG to check against).
    # Unlike EWS (engine/canonical_mapper.py:map_ews() now natively drives
    # identity/owner/account fields when EWS is the sole source, grounded
    # in the real AVS response shape), Ekata's equivalent hasn't been built
    # out the same way yet -- this still translates Ekata's own match
    # signals into a GIACT-shaped payload so those rules see a populated
    # record instead of unconditional "no GIACT data" defaults. See
    # engine/waterfall.py:synthesize_giact_from_ekata.
    bridged_from: Optional[str] = None
    if giact_raw is None and ekata_raw is not None:
        giact_raw = synthesize_giact_from_ekata(ekata_raw, request)
        bridged_from = "ekata"

    TERMINAL_TOOL_TO_SOURCE = {"ews_check": "ews", "giact_verify": "giact", "ekata_identity_check": "ekata"}
    primary_source = TERMINAL_TOOL_TO_SOURCE.get(waterfall["terminal_tool"])

    canonical = build_canonical(
        unique_id=request.get("unique_id", "N/A"),
        submitted=request,
        giact_result=giact_raw,
        ekata_response=ekata_raw,
        ews_response=ews_raw,
        account_holder_type=account_holder_type,
        reference_time=datetime.now(timezone.utc),
        primary_source=primary_source,
    )
    if bridged_from:
        # Keep source attribution honest: these fields came from Ekata,
        # not a real GIACT/LSEG call -- relabel provenance accordingly so
        # the demo's reason-code "source" column doesn't credit a vendor
        # that was never actually reached.
        canonical["_provenance"] = {
            field: (bridged_from if source == "giact" else source)
            for field, source in canonical["_provenance"].items()
        }
    steps.append({"step": "Canonical Normalization", "status": "complete",
                  "detail": "Vendor response unified into one canonical profile"})
    emit("Canonical Normalization", "Canonical profile built", "complete")

    emit("Vendor Score Decision", "Banding vendor confidence into Go / No-Go / High Risk / Hard Decline...", "running")
    terminal_tool = waterfall["terminal_tool"]
    terminal_score_info = waterfall["vendor_scores"].get(terminal_tool) if terminal_tool else None
    vendor_score = terminal_score_info["score"] if terminal_score_info else None
    vendor_checks = terminal_score_info["checks"] if terminal_score_info else []
    terminal_vendor_label = terminal_score_info["vendor"] if terminal_score_info else None
    normalized_score = terminal_score_info["normalized_score"] if terminal_score_info else None
    native_score = terminal_score_info["native_score"] if terminal_score_info else None
    native_scale = terminal_score_info["native_scale"] if terminal_score_info else None

    decision = decision_from_score(vendor_score)
    decision_label = DECISION_LABEL[decision]
    band_range = BAND_RANGE_TEXT[decision]

    score_text = f"{vendor_score}/100" if vendor_score is not None else "no score \u2014 no vendor returned data"
    vsd_detail = f"{decision_label} ({band_range}) \u2014 {terminal_vendor_label or 'no source'}: {score_text}"
    steps.append({"step": "Vendor Score Decision", "status": _vendor_decision_step_status(decision), "detail": vsd_detail})
    emit("Vendor Score Decision", vsd_detail, _vendor_decision_step_status(decision))

    # --- Restored bottom layer: deterministic + probabilistic composite ---
    # Team decision, 2026-08-06 call: keep this (don't remove it), but move
    # it below the "as received" vendor view above, and fold the vendor's
    # own normalized 0-10 score/code into the deterministic component as a
    # 4th domain alongside identity/owner/account -- "we'll just have it
    # normalized between 0 to 10 based on the reason code and then add it
    # to our deterministic score."
    emit("Deterministic Scoring", "Evaluating business rules...", "running")
    decision_result = run_decision(canonical)
    vendor_code_band = vendor_code_outcome(normalized_score)
    vendor_code_rule_id = VENDOR_CODE_RULE_ID[vendor_code_band]
    decision_result = {
        **decision_result,
        "adjusted_outcomes": {**decision_result["adjusted_outcomes"], "vendor_code": vendor_code_band},
        "triggered_rule_ids": decision_result["triggered_rule_ids"] + [vendor_code_rule_id],
    }
    steps.append({"step": "Deterministic Scoring", "status": "complete",
                  "detail": f"Outcomes: {decision_result['adjusted_outcomes']}"})
    emit("Deterministic Scoring", f"Outcomes: {decision_result['adjusted_outcomes']}", "complete")

    emit("Fraud Risk Model", "Scoring fraud probability...", "running")
    fraud_result = fraud_probability(canonical)
    steps.append({"step": "Fraud Risk Model", "status": "complete",
                  "detail": f"Fraud probability: {fraud_result['fraud_probability']:.1%} ({fraud_result['source']})"})
    emit("Fraud Risk Model", f"{fraud_result['fraud_probability']:.1%} ({fraud_result['source']})", "complete")

    emit("Composite Risk & Decision", "Combining scores...", "running")
    composite = composite_result(decision_result, fraud_result)
    steps.append({"step": "Composite Risk & Decision", "status": _composite_step_status(composite["final_decision"]),
                  "detail": f"Decision: {composite['final_decision']} ({composite['composite_score']}/100)"})
    emit("Composite Risk & Decision", f"{composite['final_decision']} ({composite['composite_score']}/100)", _composite_step_status(composite["final_decision"]))

    emit("LLM Explainability", "Generating explanation...", "running")
    if vendor_score is not None:
        top_one_liner = (f"{terminal_vendor_label} returned a confidence score of {vendor_score}/100 "
                          f"({decision_label}, {band_range}).")
    else:
        why = "this route has no fallback and the source came back empty" if not waterfall["fallback_allowed"] \
            else "every source in this route came back empty"
        top_one_liner = f"No vendor returned a usable score \u2014 {why} ({decision_label})."

    bottom_one_liner = (
        f"Composite recommendation is {composite['final_decision']}. Composite score is "
        f"{composite['composite_score']}/100 (rules component {composite['rules_score']}/100 -- "
        f"identity/owner/account plus {terminal_vendor_label or 'vendor'}'s normalized code "
        f"({normalized_score if normalized_score is not None else 'n/a'}/10) -- "
        f"estimated risk probability {fraud_result['fraud_probability']:.1%})."
    )
    business_reasoning = (
        "Why read the composite: the vendor score above tells you how confident that ONE source is in "
        "its own answer. The composite adds two things a single vendor score can't: whether the identity, "
        "the account owner, and the account itself independently corroborate each other (deterministic "
        "rules), and a separate risk estimate. A high vendor score with a risk flag, or a "
        "vendor score that doesn't line up with the identity/owner checks, is exactly what the composite "
        "is designed to catch."
    )
    explanation = {
        "explanation": top_one_liner,
        "bottom_explanation": bottom_one_liner,
        "business_reasoning": business_reasoning,
        "source": "vendor_score_band + deterministic_probabilistic_composite",
    }
    steps.append({"step": "LLM Explainability", "status": "complete", "detail": "vendor_score_band + composite"})
    emit("LLM Explainability", "vendor_score_band + composite", "complete")

    reason_codes_enriched = enrich_reason_codes(composite["reason_codes"], canonical["_provenance"])

    return {
        "unique_id": canonical["unique_id"],
        "transfer_type": transfer_type,
        "fallback_allowed": waterfall["fallback_allowed"],
        "vendor_results": vendor_results,
        "vendor_scores": waterfall["vendor_scores"],
        "waterfall_trail": waterfall["waterfall_trail"],
        "terminal_tool": waterfall["terminal_tool"],
        "bridged_from": bridged_from,
        "vendor_data_changes": vendor_data_changes,
        "canonical": canonical,
        "vendor_decision": {
            "decision": decision,
            "decision_label": decision_label,
            "score": vendor_score,
            "native_score": native_score,
            "native_scale": native_scale,
            "normalized_score": normalized_score,
            "vendor": terminal_vendor_label,
            "checks": vendor_checks,
            "band_range": band_range,
        },
        "decision_result": decision_result,
        "fraud_result": fraud_result,
        "composite": composite,
        "reason_codes_enriched": reason_codes_enriched,
        "explanation": explanation,
        "steps": steps,
    }


def run_pipeline(request: dict, account_holder_type: str = "person", on_step=None) -> dict[str, Any]:
    """on_step(label, detail="", state="running"|"complete"|"error"), if
    provided, is called at each real execution boundary -- not a timer, an
    actual callback fired as each layer starts/finishes. Passed through to
    orchestrate() too, so individual vendor calls are visible, not just the
    orchestration layer as one block."""
    def emit(label, detail="", state="running"):
        if on_step:
            on_step(label, detail, state)

    steps: list[dict[str, Any]] = []

    # ---- Step 1: Request intake ----
    emit("Request Intake", f"{len(request)} fields received", "running")
    steps.append({"step": "Request Intake", "status": "complete",
                  "detail": f"{len(request)} fields received"})
    emit("Request Intake", f"{len(request)} fields received", "complete")

    # ---- Step 2: Agentic Orchestration (LLM tool plan + vendor calls) ----
    emit("Agentic Orchestration", "Planning vendor calls...", "running")
    vendor_results = orchestrate(
        request=request,
        mcp_call=mcp_call,
        agent_decide_tool_plan=agent_decide_tool_plan,
        agent_infer_missing_field=agent_infer_missing_field,
        vendor_priority=get_priority_for(account_holder_type),
        on_step=emit,
    )
    llm_trace = vendor_results.pop("_llm_trace", {})
    called = [t for t, r in vendor_results.items() if isinstance(r, dict) and r.get("status") not in ("skipped", "error")]
    called_labels = [VENDOR_DISPLAY_LABELS.get(t, t) for t in called]
    plan_source = llm_trace.get("tool_plan_source", "unknown")
    plan_note = "Claude decided the plan" if plan_source == "llm" else "config fallback (Claude unavailable)"
    steps.append({"step": "Agentic Orchestration", "status": "complete",
                  "detail": f"Vendors called: {', '.join(called_labels) or 'none'} \u2014 {plan_note}"})
    emit("Agentic Orchestration", f"{', '.join(called_labels) or 'none'} called", "complete")

    # ---- Step 3: Canonical Normalization ----
    emit("Canonical Normalization", "Unifying vendor responses...", "running")
    giact_raw = vendor_results.get("giact_verify")
    ekata_raw = vendor_results.get("ekata_identity_check")
    ews_raw = vendor_results.get("ews_check")
    giact_raw = giact_raw if isinstance(giact_raw, dict) and "status" not in giact_raw else None
    ekata_raw = ekata_raw if isinstance(ekata_raw, dict) and "status" not in ekata_raw else None
    ews_raw = ews_raw if isinstance(ews_raw, dict) and "status" not in ews_raw else None

    canonical = build_canonical(
        unique_id=request.get("unique_id", "N/A"),
        submitted=request,
        giact_result=giact_raw,
        ekata_response=ekata_raw,
        ews_response=ews_raw,
        account_holder_type=account_holder_type,
        reference_time=datetime.now(timezone.utc),
    )
    steps.append({"step": "Canonical Normalization", "status": "complete",
                  "detail": "Vendor responses unified into one canonical profile"})
    emit("Canonical Normalization", "Canonical profile built", "complete")

    # ---- Step 4: Deterministic Scoring ----
    emit("Deterministic Scoring", "Evaluating business rules...", "running")
    decision_result = run_decision(canonical)
    steps.append({"step": "Deterministic Scoring", "status": "complete",
                  "detail": f"Outcomes: {decision_result['adjusted_outcomes']}"})
    emit("Deterministic Scoring", f"Outcomes: {decision_result['adjusted_outcomes']}", "complete")

    # ---- Step 5: Fraud Risk Model ----
    emit("Fraud Risk Model", "Scoring fraud probability...", "running")
    fraud_result = fraud_probability(canonical)
    steps.append({"step": "Fraud Risk Model", "status": "complete",
                  "detail": f"Fraud probability: {fraud_result['fraud_probability']:.1%} ({fraud_result['source']})"})
    emit("Fraud Risk Model", f"{fraud_result['fraud_probability']:.1%} ({fraud_result['source']})", "complete")

    # ---- Step 6: Composite Risk + Decision ----
    emit("Composite Risk & Decision", "Combining scores...", "running")
    composite = composite_result(decision_result, fraud_result)
    steps.append({"step": "Composite Risk & Decision", "status": _composite_step_status(composite["final_decision"]),
                  "detail": f"Decision: {composite['final_decision']} ({composite['composite_score']}/100)"})
    emit("Composite Risk & Decision", f"{composite['final_decision']} ({composite['composite_score']}/100)", _composite_step_status(composite["final_decision"]))

    # ---- Step 7: LLM Explainability ----
    emit("LLM Explainability", "Generating explanation...", "running")
    explanation = explain(composite)
    steps.append({"step": "LLM Explainability", "status": "complete",
                  "detail": explanation["source"]})
    emit("LLM Explainability", explanation["source"], "complete")

    return {
        "unique_id": canonical["unique_id"],
        "vendor_results": vendor_results,
        "llm_trace": llm_trace,
        "canonical": canonical,
        "decision_result": decision_result,
        "fraud_result": fraud_result,
        "composite": composite,
        "explanation": explanation,
        "steps": steps,
    }
