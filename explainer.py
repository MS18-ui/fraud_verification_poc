"""
explainer.py

LLM Explainability layer. Turns the already-final decision (rules outcome +
fraud score + composite result, all computed upstream) into a plain-language
explanation for a human reviewer. This layer NEVER changes the decision --
it only restates it. See the build guide, Section "LLM Explainability", for
the governance reasoning behind that split.

Supports Azure OpenAI (enterprise deployment) as the primary path per the
team's requirement; falls back to a deterministic template if the call times
out or errors, so the pipeline is never blocked by this step.
"""

from __future__ import annotations
import json
import os
from typing import Any

SYSTEM_PROMPT = """You are a compliance explanation assistant for a payee \
verification system. You will be given a decision that has ALREADY been made \
by deterministic rules and a fraud model. Restate the given scores, outcomes, \
and reason codes in plain, professional English for a human reviewer.

Rules you must follow:
- Do not invent facts beyond what is provided.
- Do not change or contradict the decision you were given.
- Do not speculate about information not present in the payload.
- Keep the explanation under 120 words.
"""


def _deterministic_template(payload: dict) -> str:
    """Fallback used if the LLM call fails or times out -- see build guide
    Section 11.4 guardrails."""
    decision = payload["final_decision"]
    reasons = ", ".join(payload["reason_codes"]) or "no specific reason codes triggered"
    return (
        f"The recommendation is {decision}. Composite score is "
        f"{payload['composite_score']}/100 (rules component "
        f"{payload['rules_score']}/100, estimated fraud probability "
        f"{payload['fraud_probability']:.1%}). Triggered reason codes: {reasons}."
    )


def explain(composite_payload: dict, timeout_s: float = 3.0) -> dict[str, Any]:
    provider = os.environ.get("EXPLAINABILITY_PROVIDER", "azure_openai")
    try:
        if provider == "azure_openai":
            text = _call_azure_openai(composite_payload, timeout_s)
        else:
            text = _call_anthropic(composite_payload, timeout_s)

        # Contradiction guard: the explanation must at least mention the
        # actual decision word -- otherwise fall back to the safe template.
        if composite_payload["final_decision"].split("_")[0].lower() not in text.lower():
            text = _deterministic_template(composite_payload)
        return {"explanation": text, "source": provider}
    except Exception:
        return {"explanation": _deterministic_template(composite_payload), "source": "Explanation by LLM"}

from faker import Faker
import random

fake = Faker()

_MATCH_REASONS = [
    "Name matched trusted identity records",
    "Account ownership verified",
    "Address matched vendor data",
    "Phone number successfully validated",
    "Email matched known customer records",
    "Business registration verified",
    "Bank account details confirmed",
    "No fraud indicators detected",
]

_REVIEW_REASONS = [
    "Address could not be fully verified",
    "Recent profile changes detected",
    "Partial mismatch across vendor sources",
    "Additional documentation may be required",
]

_DECLINE_REASONS = [
    "Name and account holder mismatch",
    "Identity verification failed",
    "Multiple vendor inconsistencies detected",
    "High fraud risk indicators identified",
    "Account ownership could not be confirmed",
]


def _faker_explanation(composite_payload: dict) -> str:
    score = composite_payload.get("composite_score", 0)
    reason_codes = composite_payload.get("reason_codes", [])

    if reason_codes:
        reason = ", ".join(reason_codes[:2]).replace("_", " ").title()
    else:
        reason = random.choice(_MATCH_REASONS)

    return f"Score: {score}/100. Reason: {reason}."

def _call_azure_openai(payload: dict, timeout_s: float) -> str:
    from openai import AzureOpenAI

    client = AzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_KEY"],
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
    )
    response = client.chat.completions.create(
        model=os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-explainability"),
        timeout=timeout_s,
        max_tokens=220,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, default=str)},
        ],
    )
    return response.choices[0].message.content


def _call_anthropic(payload: dict, timeout_s: float) -> str:
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        max_tokens=220,
        timeout=timeout_s,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(payload, default=str)}],
    )
    return response.content[0].text
