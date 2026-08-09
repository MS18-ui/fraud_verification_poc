"""
orchestrator_stub.py

Skeleton for the "Agentic Orchestrator" box in the architecture diagram.
This file defines the DETERMINISTIC parts engineering should implement
exactly as-is (has_min_fields, the call loop, error handling) and marks
the one spot where an LLM agent plugs in (tool selection + partial-field
inference). The agent must never bypass has_min_fields -- that gate is
code, not a prompt instruction.

This is intentionally not wired to a real LLM SDK here -- swap
`agent_decide_tool_plan` / `agent_infer_missing_field` for real calls
(e.g. Claude with the MCP tools from schemas/mcp_tools.json attached).
"""

from __future__ import annotations
import json
import os
from typing import Any, Callable, Optional

SCHEMAS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "schemas")


def load_mcp_tools() -> dict:
    with open(os.path.join(SCHEMAS_DIR, "mcp_tools.json")) as f:
        return json.load(f)


def has_min_fields(tool_def: dict, request: dict) -> bool:
    """
    Deterministic gate: at least one of tool_def['min_input_rules'] must be
    fully satisfied by non-empty fields in `request`. This function is the
    hard boundary the LLM agent CANNOT override -- if it returns False the
    tool call is skipped, full stop.
    """
    def present(field: str) -> bool:
        return bool(request.get(field)) or bool((request.get("address") or {}).get(field))

    for rule in tool_def.get("min_input_rules", []):
        all_ok = all(present(f) for f in rule.get("all", []))
        any_ok = (not rule.get("any")) or any(present(f) for f in rule["any"])
        if all_ok and any_ok:
            return True
    return False


VENDOR_LABELS = {"giact_verify": "GIACT", "ekata_identity_check": "Ekata", "ews_check": "EWS"}


def orchestrate(
    request: dict,
    mcp_call: Callable[[str, dict], dict],
    agent_decide_tool_plan: Callable[[dict, dict], list[str]],
    agent_infer_missing_field: Optional[Callable[[str, dict, dict], dict]] = None,
    vendor_priority: Optional[list[str]] = None,
    on_step: Optional[Callable[[str, str, str], None]] = None,
) -> dict[str, Any]:
    """
    request: inbound API request fields (name, ssn, address, routing/account, ...).
    mcp_call: fn(tool_name, request) -> raw vendor response. Raises VendorError subtypes.
    agent_decide_tool_plan: LLM call -> ordered list of tool names to attempt.
                             This is the only place the LLM chooses *what* to call.
    agent_infer_missing_field: optional LLM call -> dict of fields it can safely
                             derive from data already present in `request`/`results`.
                             Must NEVER fabricate identifiers (ssn, dob, account
                             numbers) -- only carry forward values already seen.
    vendor_priority: configured order (config/vendor_priority.yaml) given to the
                             LLM as guidance and used as the fallback plan if the
                             LLM call fails -- vendor call order is never solely
                             dependent on the LLM being available.
    on_step: optional fn(label, detail, state) called at real execution
                             boundaries -- LLM tool planning, and each individual
                             vendor call -- not a timer, fired as each thing
                             actually happens.

    Returns: {tool_name: raw_response_or_status, ..., "_llm_trace": {...}}
    ready for canonical_mapper (the "_llm_trace" key is excluded from vendor
    iteration by callers -- see pipeline.py).
    """
    def emit(label, detail="", state="running"):
        if on_step:
            on_step(label, detail, state)

    tools = {t["name"]: t for t in load_mcp_tools()["tools"]}
    llm_trace: dict[str, Any] = {}
    emit("LLM tool planning", "Asking Claude which vendors to call...", "running")
    plan = agent_decide_tool_plan(request, tools, vendor_priority, _trace=llm_trace)
    plan_src = llm_trace.get("tool_plan_source", "unknown")
    plan_labels = [VENDOR_LABELS.get(t, t) for t in plan]
    emit("LLM tool planning", f"Plan: {' \u2192 '.join(plan_labels) or 'none'} ({plan_src})", "complete")

    results: dict[str, Any] = {}
    working_request = dict(request)

    for tool_name in plan:
        label = VENDOR_LABELS.get(tool_name, tool_name)
        tool_def = tools[tool_name]

        if not has_min_fields(tool_def, working_request) and agent_infer_missing_field:
            emit(f"Calling {label}", "Insufficient fields \u2014 asking LLM to infer a carried-forward value...", "running")
            try:
                inferred = agent_infer_missing_field(tool_name, working_request, results, _trace=llm_trace)
            except TypeError:
                inferred = agent_infer_missing_field(tool_name, working_request, results)
            # Guardrail: only accept inferred fields the agent is allowed to add.
            forbidden = {"ssn", "dob", "account_number", "routing_number"}
            inferred = {k: v for k, v in inferred.items() if k not in forbidden}
            working_request = {**working_request, **inferred}

        if not has_min_fields(tool_def, working_request):
            emit(f"Calling {label}", "Skipped \u2014 insufficient input", "error")
            results[tool_name] = {"status": "skipped", "reason": "insufficient_input"}
            continue

        emit(f"Calling {label}", "Waiting for response...", "running")
        try:
            results[tool_name] = mcp_call(tool_name, working_request)
            emit(f"Calling {label}", "Responded", "complete")
        except Exception as e:  # replace with specific VendorError types in production
            emit(f"Calling {label}", f"Error: {e}", "error")
            results[tool_name] = {"status": "error", "detail": str(e)}

    results["_llm_trace"] = llm_trace
    return results
