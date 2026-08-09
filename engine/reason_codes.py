"""
reason_codes.py

engine/decision.py's output only carries a flat list of rule-id strings
(triggered_rule_ids / composite.py's reason_codes). This module enriches
each one with the description already written into the rule's own YAML
definition (rules/*.yaml) and the vendor source of the canonical field(s)
that rule's condition actually reads (from canonical["_provenance"]), so
the UI can render one reason per line: code, description, source -- no
duplicate copy of rule text maintained in Python.

Demo-only convenience module -- does not change engine/decision.py or
engine/rules_engine.py, and is not on the normal (run_pipeline) code path
unless a caller chooses to use it.
"""

from __future__ import annotations
import os
from functools import lru_cache
from typing import Any

RULES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "rules")
RULE_FILES = ["identity.yaml", "owner.yaml", "account.yaml", "supplementary.yaml"]

# Domain-level default rule ids (rules/*.yaml: default_rule_id), fired when
# no rule in that domain matched -- these never appear inside a "rules:"
# block, so they're indexed separately.
_DEFAULT_RULE_DESCRIPTIONS = {
    "IDENTITY_NO_RULE_MATCHED": "No identity rule matched this record; safe default (review) applied.",
    "OWNER_NO_RULE_MATCHED": "No owner rule matched this record; safe default (review) applied.",
    "ACCOUNT_NO_RULE_MATCHED": "No account rule matched this record; safe default (review) applied.",
    "INTL_NO_FALLBACK_INSUFFICIENT_DATA": "International route has no fallback (config/waterfall_rules.yaml) -- "
                                           "LSEG returned no data and no other source was tried; declined for insufficient data.",
    "VENDOR_CODE_HIGH_CONFIDENCE": "The terminal vendor's own score/code, normalized to 0-10, was >= 8 -- "
                                    "folded into the deterministic score as a 4th domain (see engine/vendor_normalization.py).",
    "VENDOR_CODE_MEDIUM_CONFIDENCE": "The terminal vendor's own score/code, normalized to 0-10, was 4-7.9 -- "
                                      "folded into the deterministic score as a 4th domain.",
    "VENDOR_CODE_LOW_CONFIDENCE": "The terminal vendor's own score/code, normalized to 0-10, was below 4 (or no "
                                   "score was available) -- folded into the deterministic score as a 4th domain.",
}

SOURCE_DISPLAY = {"giact": "LSEG", "ekata": "Ekata", "ews": "EWS"}


@lru_cache(maxsize=None)
def _rule_index() -> dict[str, dict[str, Any]]:
    """rule id -> {"description", "condition", "domain"}"""
    import yaml

    index: dict[str, dict[str, Any]] = {}
    for filename in RULE_FILES:
        with open(os.path.join(RULES_DIR, filename)) as f:
            doc = yaml.safe_load(f)
        domain = doc.get("domain", filename.replace(".yaml", ""))

        rules_section = doc.get("rules", {})
        if isinstance(rules_section, dict):  # identity/owner/account: fail/verified/review buckets
            for bucket_rules in rules_section.values():
                for rule in bucket_rules:
                    index[rule["id"]] = {
                        "description": rule.get("description", ""),
                        "condition": rule.get("condition"),
                        "domain": domain,
                    }
        elif isinstance(rules_section, list):  # supplementary.yaml: flat list, rules A-I
            for rule in rules_section:
                category = (rule.get("category") or "").replace("_", " ").title()
                index[rule["id"]] = {
                    "description": rule.get("description") or category or "Supplementary flag.",
                    "condition": rule.get("condition"),
                    "domain": domain,
                }

    for rule_id, description in _DEFAULT_RULE_DESCRIPTIONS.items():
        index[rule_id] = {"description": description, "condition": None, "domain": rule_id.split("_")[0].lower()}

    return index


def _fields_in_condition(condition: Any) -> list[str]:
    """Flatten a (possibly nested all/any/not) condition down to the
    canonical field paths it reads. computed-function conditions have no
    single field to point to -- returns []."""
    if not condition or not isinstance(condition, dict):
        return []
    if "field" in condition:
        return [condition["field"]]
    fields: list[str] = []
    for key in ("all", "any"):
        for c in condition.get(key) or []:
            fields.extend(_fields_in_condition(c))
    if "not" in condition:
        fields.extend(_fields_in_condition(condition["not"]))
    return fields


def enrich_reason_codes(triggered_rule_ids: list[str], provenance: dict[str, str]) -> list[dict[str, Any]]:
    """Returns one dict per triggered rule: {"code", "description", "domain", "source"}."""
    index = _rule_index()
    enriched = []
    for rule_id in triggered_rule_ids:
        meta = index.get(rule_id, {})
        fields = _fields_in_condition(meta.get("condition"))
        raw_sources = {provenance[f] for f in fields if f in provenance and provenance[f] is not None}
        source = ", ".join(sorted(SOURCE_DISPLAY.get(s, s.upper()) for s in raw_sources)) if raw_sources else "\u2014"
        enriched.append({
            "code": rule_id,
            "description": (meta.get("description") or "").strip(),
            "domain": meta.get("domain", ""),
            "source": source,
        })
    return enriched
