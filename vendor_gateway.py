"""
vendor_gateway.py

The mcp_call(tool_name, request) function orchestrator_stub.py expects. This
is the ONLY file that should ever hold vendor credentials or make an outbound
HTTP call -- kept separate from orchestrator_stub.py so the orchestrator
itself never needs vendor secrets in scope. Two modes:

  - DEMO_MODE=true (default): serves responses from a local fixture / the
    interns' synthetic-JSON generator, so the whole pipeline is runnable with
    no live vendor credentials -- this is what the Streamlit demo uses.
  - DEMO_MODE=false: makes real HTTP calls. Fill in the marked sections with
    your bank-provided API wrapper endpoints and Key Vault-sourced credentials.
"""

from __future__ import annotations
import os
from typing import Any

DEMO_MODE = os.environ.get("DEMO_MODE", "true").lower() == "true"


class VendorUnavailable(Exception):
    pass


def mcp_call(tool_name: str, request: dict, demo_scenario: bool = False) -> dict[str, Any]:
    """
    demo_scenario: set by the caller (pipeline.py's demo-only wrapper), NOT
    read from the request -- the request itself carries no internal tags.
    When True, the request is one of the fixed client-demo scenarios
    (data/demo_scenarios.py); which one it is gets derived purely from the
    request's own fields via classify_scenario(), not a hidden flag.
    """
    if demo_scenario:
        from data.demo_scenarios import classify_scenario, get_demo_response, EWS_OUTAGE
        if tool_name == "ews_check" and classify_scenario(request) == EWS_OUTAGE:
            raise VendorUnavailable("Simulated EWS outage (demo)")
        return get_demo_response(tool_name, request)

    if DEMO_MODE:
        from data.synthetic_source import get_synthetic_response
        return get_synthetic_response(tool_name, request)

    # ---- production path: replace with real bank-provided API wrapper calls ----
    if tool_name == "giact_verify":
        return _call_real_vendor("GIACT_BASE_URL", "/api/v5/inquiries", request)
    if tool_name == "ekata_identity_check":
        return _call_real_vendor("EKATA_BASE_URL", "/3.3/identity_check", request)
    if tool_name == "ews_check":
        return _call_real_vendor("EWS_BASE_URL", "/verify", request)
    raise VendorUnavailable(f"Unknown tool: {tool_name}")


def _call_real_vendor(base_url_env: str, path: str, request: dict) -> dict:
    import httpx

    base_url = os.environ[base_url_env]
    api_key = os.environ[f"{base_url_env}_KEY"]  # sourced from Azure Key Vault at deploy time
    try:
        resp = httpx.post(
            f"{base_url}{path}", json=request,
            headers={"Authorization": f"Bearer {api_key}"}, timeout=5.0,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
        raise VendorUnavailable(str(e)) from e
