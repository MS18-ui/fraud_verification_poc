"""
decision.py

Top-level entry point engineering/services call after the canonical mapper
has produced a canonical dict. Wires together:
  identity.yaml + owner.yaml + account.yaml  -> per-domain outcome
  supplementary.yaml                          -> flags + outcome caps/overrides
  decision_table.yaml                         -> overall decision

Output of run_decision() is exactly the payload the "LLM Explainability"
step in the architecture should receive -- it should not need to touch
the canonical dict or raw vendor payloads itself.
"""

from __future__ import annotations
import os
from functools import lru_cache
import yaml
from typing import Any

from engine.rules_engine import (
    evaluate_domain,
    apply_supplementary,
    combine_decision,
    COMPUTED_REGISTRY,
)

RULES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "rules")


@lru_cache(maxsize=None)
def _load(filename: str) -> dict:
    # Cached: rule files are read from disk once per process rather than
    # once per request -- matters a lot for batch scoring (30K+ records)
    # where the uncached version re-parses 5 YAML files per row. If you
    # need rule config to hot-reload without a process restart, call
    # _load.cache_clear() after editing rules/*.yaml.
    with open(os.path.join(RULES_DIR, filename)) as f:
        return yaml.safe_load(f)


def run_decision(canonical: dict) -> dict[str, Any]:
    identity_doc = _load("identity.yaml")
    owner_doc = _load("owner.yaml")
    account_doc = _load("account.yaml")
    supplementary_doc = _load("supplementary.yaml")
    decision_doc = _load("decision_table.yaml")

    identity_outcome, identity_rule = evaluate_domain(identity_doc, canonical, COMPUTED_REGISTRY)
    owner_outcome, owner_rule = evaluate_domain(owner_doc, canonical, COMPUTED_REGISTRY)
    account_outcome, account_rule = evaluate_domain(account_doc, canonical, COMPUTED_REGISTRY)

    primary_outcomes = {
        "identity": identity_outcome,
        "owner": owner_outcome,
        "account": account_outcome,
    }
    primary_rule_ids = {
        "identity": identity_rule,
        "owner": owner_rule,
        "account": account_rule,
    }

    adjusted_outcomes, triggered_supplementary = apply_supplementary(
        supplementary_doc, canonical, primary_outcomes, COMPUTED_REGISTRY
    )

    overall, matched_row = combine_decision(decision_doc, adjusted_outcomes)

    triggered_rule_ids = list(primary_rule_ids.values()) + [t["id"] for t in triggered_supplementary]

    return {
        "unique_id": canonical.get("unique_id"),
        "primary_outcomes": primary_outcomes,          # before supplementary caps/overrides
        "primary_rule_ids": primary_rule_ids,
        "adjusted_outcomes": adjusted_outcomes,          # after supplementary caps/overrides
        "supplementary_flags": triggered_supplementary,
        "overall_decision": overall,
        "matched_decision_row": matched_row,
        "triggered_rule_ids": triggered_rule_ids,
        "provenance": canonical.get("_provenance", {}),
        # This dict is what should be handed to the LLM Explainability step:
        # composite_risk_score is computed separately by combining this
        # deterministic result with the parallel ML fraud_probability output.
    }
