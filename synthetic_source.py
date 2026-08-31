"""
synthetic_source.py

Integration point for the two intern deliverables:

  1. The synthetic Ekata/GIACT/EWS request+response generator suite
     (data/generators/) -- used both for live DEMO_MODE vendor calls
     (get_synthetic_response, called by engine/vendor_gateway.py) AND now
     for the UI's "Load a sample record" button (see
     data/generated_samples.py:build_sample_request) -- per the team's
     decision to demo the full pipeline (orchestration, LLM tool-planning,
     vendor calls) rather than loading pre-flattened master-dataset rows
     that skip straight to scoring.
  2. The 100K-record master dataset -- still used by
     data/master_dataset_adapter.py for fraud-model training and batch
     scoring/calibration. load_random_master_record() below is kept for
     that kind of programmatic use, but the UI no longer calls it for
     sample loading.

get_synthetic_response() now delegates to
data/generated_samples.py:get_generated_vendor_response(), which fixes a
real bug in the previous version here: each vendor used to independently
invent its OWN random identity (GIACT might say "Patricia Martinez" while
Ekata said "James Williams" for the same case). Now there is one shared
claimed identity submitted to all three vendors, and each vendor
independently decides -- via its own match category -- whether its own
records agree with that claim.
"""

from __future__ import annotations
import json
import os
import random
from pathlib import Path
from typing import Any

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "tests" / "sample_data" / "partial_match.json"


def get_synthetic_response(tool_name: str, request: dict) -> dict[str, Any]:
    """Called by engine/vendor_gateway.py in DEMO_MODE. Returns a raw
    vendor-shaped response for the given tool, derived from the SAME
    submitted request fields across all three vendors."""
    from data.generated_samples import GENERATOR_READY, get_generated_vendor_response

    if GENERATOR_READY:
        return get_generated_vendor_response(tool_name, request)

    # Fallback: replay the bundled fixture (used only if the generator
    # suite's dependencies -- data/reference.py, data/scenarios.py,
    # data/match_flags.py, or the `faker` package -- are missing).
    with open(FIXTURE_PATH) as f:
        sample = json.load(f)
    if tool_name == "giact_verify":
        return sample["vendors"]["giact"]["response"]["PostInquiryResult"]
    if tool_name == "ekata_identity_check":
        return sample["vendors"]["ekata"]["response"]
    if tool_name == "ews_check":
        return {"fraud_history_found": False}
    return {}


_master_records_cache: dict[str, list[dict[str, Any]]] = {}


class MasterDatasetNotConfigured(Exception):
    """Raised by load_random_master_record() when MASTER_DATASET_PATH isn't
    set. No longer used by the UI's sample-loading path (see
    data/generated_samples.py:build_sample_request instead) -- kept here
    for programmatic/training use of the real master dataset."""


def load_random_master_record() -> dict[str, Any]:
    """Pulls one record from the configured master dataset
    (MASTER_DATASET_PATH). Kept for training/calibration scripts -- the
    UI's 'Load a sample record' button now uses
    data/generated_samples.py:build_sample_request instead, so the full
    pipeline (including agentic orchestration) actually runs on click."""
    master_path = os.environ.get("MASTER_DATASET_PATH")
    if not master_path:
        raise MasterDatasetNotConfigured(
            "MASTER_DATASET_PATH is not set."
        )

    if master_path not in _master_records_cache:
        from data.master_dataset_adapter import load_master_records
        _master_records_cache[master_path] = load_master_records(master_path)

    return random.choice(_master_records_cache[master_path])
