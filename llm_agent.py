"""
llm_agent.py

Real implementation of the two hooks orchestrator_stub.py leaves open:
  - agent_decide_tool_plan(request, tools) -> ordered list of MCP tool names
  - agent_infer_missing_field(tool_name, request, results) -> dict of safe-to-carry-forward fields

This is the "LLM does the tool-calling" pattern discussed on the call: a static
system prompt + the MCP tool definitions (schemas/mcp_tools.json) are given to
Claude, which decides which tools to call and in what order, and can propose
carrying a field forward from one vendor's response into the next call. It can
NEVER bypass has_min_fields() (that gate lives in orchestrator_stub.py, in code,
not in the prompt) and it can NEVER fabricate an identifier — both are enforced
here in Python, not requested politely of the model.
"""

from __future__ import annotations
import json
import os
from typing import Any

import anthropic

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
FORBIDDEN_INFERRED_FIELDS = {"ssn", "dob", "account_number", "routing_number"}

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    return _client


PLAN_SYSTEM_PROMPT = """You are the tool-planning component of a payee verification \
orchestrator. You will be given the fields present on an inbound verification \
request and a list of available vendor tools (name, description, minimum input \
requirements). Decide which tools are worth attempting and in what order, from \
most to least likely to succeed given the fields present.

Rules you must follow:
- Only return tool names that exist in the provided tool list.
- If a configured_priority_order is provided, treat it as the default order --
  only deviate from it if the fields present clearly favor a different vendor
  going first (e.g. that vendor's minimum input isn't met at all).
- Prefer the vendor most likely to return a useful result first, based on which \
  request fields are populated -- do not guess wildly.
- You are proposing a plan, not deciding whether a tool actually runs -- the \
  orchestrator will independently verify minimum-field requirements before any \
  call is made, and will skip a tool your plan includes if that check fails.
- Respond with ONLY a JSON array of tool name strings, nothing else.
"""

INFER_SYSTEM_PROMPT = """You help fill in a missing field for a vendor tool call \
using ONLY data already visible in the current request or in a prior vendor \
response for this same case. You must NEVER invent, guess, or fabricate a value.

Strict rules:
- Never propose a value for ssn, dob, account_number, or routing_number -- those \
  fields are always rejected downstream even if you include them.
- Only propose a field if you can point to the exact prior value it came from \
  (e.g. carrying a corrected address that a vendor already returned).
- If you cannot confidently derive the field from data already present, return \
  an empty JSON object.
- Respond with ONLY a JSON object of {field_name: value}, nothing else.
"""


def agent_decide_tool_plan(request: dict, tools: dict[str, dict], vendor_priority: list[str] = None,
                          _trace: dict = None) -> list[str]:
    """LLM call -> ordered list of tool names to attempt. `vendor_priority`
    (from config/vendor_priority.yaml) is given to the model as guidance --
    it is a hint, not a hard instruction, since the model may reasonably
    reorder based on which fields are actually present on this request.
    But if the LLM call fails, the fallback is the CONFIGURED priority order,
    not an arbitrary one -- so vendor call order is never solely dependent
    on the LLM being available or well-behaved.

    _trace: optional dict this fills in with {"tool_plan_source": "llm" |
    "fallback"} so callers (orchestrator_stub.py -> pipeline.py -> the UI)
    can confirm whether Claude actually responded for this run, rather than
    silently falling back with no visible signal either way."""
    tool_summaries = [
        {"name": t["name"], "description": t["description"], "min_input_rules": t["min_input_rules"]}
        for t in tools.values()
    ]
    try:
        payload = {"request_fields": list(request.keys()), "available_tools": tool_summaries}
        if vendor_priority:
            payload["configured_priority_order"] = vendor_priority
        response = _get_client().messages.create(
            model=MODEL,
            max_tokens=200,
            system=PLAN_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(payload)}],
        )
        text = response.content[0].text.strip()
        plan = json.loads(text)
        if _trace is not None:
            _trace["tool_plan_source"] = "llm"
            _trace["tool_plan_model"] = MODEL
        return [name for name in plan if name in tools]
    except Exception as e:
        # Deterministic fallback: configured priority order, not schema order.
        if _trace is not None:
            _trace["tool_plan_source"] = "fallback"
            _trace["tool_plan_fallback_reason"] = str(e)
        if vendor_priority:
            return [t for t in vendor_priority if t in tools]
        return list(tools.keys())


def agent_infer_missing_field(tool_name: str, request: dict, results: dict, _trace: dict = None) -> dict[str, Any]:
    """LLM call -> fields it can safely carry forward from prior vendor results.
    Guardrail against forbidden fields is enforced again here AND in
    orchestrator_stub.py (defense in depth)."""
    try:
        response = _get_client().messages.create(
            model=MODEL,
            max_tokens=150,
            system=INFER_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": json.dumps({
                    "tool_being_called": tool_name,
                    "current_request_fields": request,
                    "prior_vendor_results": results,
                }, default=str),
            }],
        )
        text = response.content[0].text.strip()
        inferred = json.loads(text)
        if _trace is not None:
            _trace.setdefault("field_inference_calls", []).append({"tool": tool_name, "source": "llm"})
        return {k: v for k, v in inferred.items() if k not in FORBIDDEN_INFERRED_FIELDS}
    except Exception:
        if _trace is not None:
            _trace.setdefault("field_inference_calls", []).append({"tool": tool_name, "source": "fallback"})
        return {}
