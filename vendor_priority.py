"""
vendor_priority.py

Config-driven vendor call priority, per account holder type. This closes the
gap flagged from the design call: previously the LLM's tool plan had no
configured order to be guided by or fall back to. Now it does -- see
config/vendor_priority.yaml.
"""

from __future__ import annotations
from pathlib import Path
from typing import Dict, List
import yaml

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "vendor_priority.yaml"

TOOL_LABELS = {
    "giact_verify": "GIACT",
    "ekata_identity_check": "Ekata",
    "ews_check": "EWS",
}


def load_vendor_priority() -> Dict[str, List[str]]:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def get_priority_for(account_holder_type: str) -> List[str]:
    config = load_vendor_priority()
    return config.get(account_holder_type, config.get("person", []))


def priority_label(account_holder_type: str) -> str:
    """Human-readable priority string, e.g. 'GIACT \u2192 Ekata \u2192 EWS'."""
    order = get_priority_for(account_holder_type)
    return " \u2192 ".join(TOOL_LABELS.get(t, t) for t in order)
