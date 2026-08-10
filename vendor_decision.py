"""
vendor_decision.py

Option B decision model for the waterfall demo (domestic/international/
onboarding routes): no composite score, no fraud model, no rules engine.
The decision is banded directly off whichever vendor's confidence score
resolved the case (engine/vendor_scores.py). Team decision, 2026-08-06:

    >= 85          GO
    60 - 84        NO_GO
    35 - 59        HIGH_RISK
    <  35          HARD_DECLINE
    no score at all (no vendor in the route returned data)  ->  HARD_DECLINE

This does not touch scoring/composite.py, scoring/fraud_model.py, or
engine/decision.py -- those still drive the standard/agentic flow
(run_pipeline() / pipeline.py), untouched. This model is scoped to the
waterfall demo only, per the team decision.
"""

from __future__ import annotations
from typing import Optional

GO = "GO"
NO_GO = "NO_GO"
HIGH_RISK = "HIGH_RISK"
HARD_DECLINE = "HARD_DECLINE"

BANDS = [
    (85, GO),
    (60, NO_GO),
    (35, HIGH_RISK),
    (0, HARD_DECLINE),
]

DECISION_LABEL = {
    GO: "Go",
    NO_GO: "No-Go",
    HIGH_RISK: "High Risk",
    HARD_DECLINE: "Hard Decline",
}

BAND_RANGE_TEXT = {
    GO: "\u2265 85",
    NO_GO: "60\u201384",
    HIGH_RISK: "35\u201359",
    HARD_DECLINE: "< 35",
}


def decision_from_score(score: Optional[int]) -> str:
    """A vendor score of None means no vendor in the route returned usable
    data at all (e.g. international with no fallback, or a route that
    dead-ends) -- that's a Hard Decline for insufficient data, the same
    principle the earlier composite-model international no-fallback case
    used, just simpler now: there's no score to band."""
    if score is None:
        return HARD_DECLINE
    for threshold, decision in BANDS:
        if score >= threshold:
            return decision
    return HARD_DECLINE
