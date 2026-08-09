"""
composite.py

Combines the deterministic decision (engine/decision.py:run_decision) with the
fraud model output (scoring/fraud_model.py:fraud_probability) into one
composite score + a single overall decision the Decision Engine step can act
on. Outcome ranks come straight from decision_table.yaml so this file never
hardcodes a second copy of that logic.
"""

from __future__ import annotations
from typing import Any

OUTCOME_TO_SCORE = {"verified": 100.0, "review": 55.0, "fail": 0.0}
FRAUD_WEIGHT = 0.35
RULES_WEIGHT = 0.65

THRESHOLDS = {"approved": 78, "manual_review": 55, "soft_decline": 35}


def _rules_component_score(decision_result: dict) -> float:
    outcomes = decision_result["adjusted_outcomes"]
    scores = [OUTCOME_TO_SCORE[o] for o in outcomes.values()]
    return sum(scores) / len(scores)


def composite_result(decision_result: dict, fraud_result: dict) -> dict[str, Any]:
    rules_score = _rules_component_score(decision_result)
    fraud_safety_score = 100.0 * (1 - fraud_result["fraud_probability"])
    composite_score = round(RULES_WEIGHT * rules_score + FRAUD_WEIGHT * fraud_safety_score, 2)

    # A hard fail from the deterministic engine always wins, regardless of score --
    # matches decision_table.yaml's fail-first ordering (see engine/rules_engine.py).
    if decision_result["overall_decision"] == "decline":
        final_decision = "HARD_DECLINE"
    elif composite_score >= THRESHOLDS["approved"]:
        final_decision = "APPROVED"
    elif composite_score >= THRESHOLDS["manual_review"]:
        final_decision = "MANUAL_REVIEW"
    elif composite_score >= THRESHOLDS["soft_decline"]:
        final_decision = "SOFT_DECLINE"
    else:
        final_decision = "HARD_DECLINE"

    return {
        "rules_score": round(rules_score, 2),
        "fraud_probability": fraud_result["fraud_probability"],
        "fraud_safety_score": round(fraud_safety_score, 2),
        "composite_score": composite_score,
        "final_decision": final_decision,
        "reason_codes": decision_result["triggered_rule_ids"],
    }
