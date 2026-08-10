"""
case_store.py

Local SQLite persistence for every processed request -- request payload,
canonical profile, decision, and scores -- saved after each run so there's
a real, queryable history. Nothing like this existed before: the earlier
Canonical Model investigation found no SQL/DynamoDB persistence anywhere
in this codebase, despite the "this is being saved in Dynamo SQL ITB"
comment on the working call -- the only prior "memory" was an in-process
Python dict in data/generated_samples.py that resets on every restart.

Used by both pipelines (ui/app.py calls save_case() after each run,
whichever flow produced the result) -- one shared table, a `flow` column
distinguishes "waterfall_demo" from "agentic" runs.

Best-effort: a save/read failure never raises -- a storage problem should
not break a verification run or crash the UI.
"""

from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(__file__).resolve().parents[1] / "verification_cases.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unique_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    flow TEXT NOT NULL,               -- "waterfall_demo" | "agentic"
    transfer_type TEXT,               -- domestic / international / onboarding (waterfall_demo only)
    demo_scenario TEXT,               -- e.g. "domestic_direct" (waterfall_demo only, NULL for "Load any record")
    decision TEXT,
    composite_score REAL,
    rules_score REAL,
    fraud_probability REAL,
    request_json TEXT NOT NULL,
    canonical_json TEXT,
    vendor_results_json TEXT,
    reason_codes_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_cases_unique_id ON cases(unique_id);
CREATE INDEX IF NOT EXISTS idx_cases_created_at ON cases(created_at);

-- Vendor-side audit trail (per the 2026-08-08 working call: "audit trail
-- of the data from the vendor... because the vendor keeps updating").
-- One row per DETECTED CHANGE -- not one row per call -- so this table
-- stays a genuine change log, not a duplicate of `cases`. A vendor
-- returning the same data again produces no new row here.
CREATE TABLE IF NOT EXISTS vendor_response_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unique_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,          -- e.g. "giact_verify"
    vendor_label TEXT,                -- e.g. "LSEG"
    detected_at TEXT NOT NULL,
    previous_case_id INTEGER,         -- the earlier `cases` row this was compared against
    previous_seen_at TEXT,
    changed_fields_json TEXT NOT NULL -- [{"field": "...", "old": ..., "new": ...}, ...]
);
CREATE INDEX IF NOT EXISTS idx_vrc_unique_id ON vendor_response_changes(unique_id);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_SCHEMA)
    return conn


def save_case(
    request: dict,
    result: dict,
    flow: str,
    transfer_type: Optional[str] = None,
    demo_scenario: Optional[str] = None,
) -> Optional[int]:
    """Persists one processed request. Returns the new row id, or None if
    the save failed for any reason (DB locked, disk full, etc.) -- never
    raises."""
    try:
        composite = result.get("composite", {})
        conn = _connect()
        with conn:
            cur = conn.execute(
                """INSERT INTO cases
                   (unique_id, created_at, flow, transfer_type, demo_scenario,
                    decision, composite_score, rules_score, fraud_probability,
                    request_json, canonical_json, vendor_results_json, reason_codes_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    result.get("unique_id", request.get("unique_id", "N/A")),
                    datetime.now(timezone.utc).isoformat(),
                    flow,
                    transfer_type,
                    demo_scenario,
                    composite.get("final_decision"),
                    composite.get("composite_score"),
                    composite.get("rules_score"),
                    (result.get("fraud_result") or {}).get("fraud_probability"),
                    json.dumps(request, default=str),
                    json.dumps(result.get("canonical"), default=str),
                    json.dumps(result.get("vendor_results"), default=str),
                    json.dumps(composite.get("reason_codes", [])),
                ),
            )
        row_id = cur.lastrowid
        conn.close()
        return row_id
    except Exception:
        return None


def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Flattens a nested dict/list into {"a.b.0.c": value} pairs, so two
    vendor response bodies can be diffed field-by-field regardless of
    nesting depth."""
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(_flatten(v, f"{prefix}.{i}"))
    else:
        out[prefix] = obj
    return out


def check_and_log_vendor_changes(unique_id: str, vendor_results: dict) -> list[dict[str, Any]]:
    """
    Vendor-side audit trail (2026-08-08 working call): compares this run's
    vendor responses against the MOST RECENT prior request for the same
    unique_id, and logs any field-level differences to
    vendor_response_changes. A vendor returning identical data again is
    NOT logged -- only genuine changes are.

    Returns the list of change records logged this call (empty if this is
    the first time this unique_id has been seen, or if nothing changed --
    both are the common case with this demo's static fixtures; the
    mechanism itself is what's being scoped here, per "I can provide some
    scope in the code for that").
    """
    try:
        conn = _connect()
        conn.row_factory = sqlite3.Row
        prior = conn.execute(
            """SELECT id, created_at, vendor_results_json FROM cases
               WHERE unique_id = ? ORDER BY created_at DESC LIMIT 1""",
            (unique_id,),
        ).fetchone()
        if not prior or not prior["vendor_results_json"]:
            conn.close()
            return []  # first time we've seen this identity -- nothing to compare against

        prior_results = json.loads(prior["vendor_results_json"])
        logged: list[dict[str, Any]] = []
        detected_at = datetime.now(timezone.utc).isoformat()

        for tool_name, new_raw in vendor_results.items():
            old_raw = prior_results.get(tool_name)
            if old_raw is None or new_raw is None:
                continue  # vendor wasn't called both times -- not a "change", just a different route
            old_flat, new_flat = _flatten(old_raw), _flatten(new_raw)
            changed_fields = [
                {"field": k, "old": old_flat.get(k), "new": new_flat[k]}
                for k in new_flat
                if new_flat[k] != old_flat.get(k)
            ]
            if not changed_fields:
                continue
            with conn:
                conn.execute(
                    """INSERT INTO vendor_response_changes
                       (unique_id, tool_name, vendor_label, detected_at,
                        previous_case_id, previous_seen_at, changed_fields_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (unique_id, tool_name, tool_name, detected_at,
                     prior["id"], prior["created_at"], json.dumps(changed_fields, default=str)),
                )
            logged.append({
                "tool_name": tool_name, "previous_seen_at": prior["created_at"],
                "changed_fields": changed_fields,
            })
        conn.close()
        return logged
    except Exception:
        return []


def list_vendor_changes(unique_id: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    """Recent vendor-data changes, optionally filtered to one identity."""
    try:
        conn = _connect()
        conn.row_factory = sqlite3.Row
        if unique_id:
            rows = conn.execute(
                """SELECT * FROM vendor_response_changes WHERE unique_id = ?
                   ORDER BY detected_at DESC LIMIT ?""",
                (unique_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM vendor_response_changes ORDER BY detected_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        conn.close()
        out = []
        for r in rows:
            d = dict(r)
            d["changed_fields"] = json.loads(d["changed_fields_json"])
            out.append(d)
        return out
    except Exception:
        return []


def list_cases(limit: int = 50) -> list[dict[str, Any]]:
    """Most recent cases first -- summary columns only, not the full JSON
    blobs (use get_case() for those)."""
    try:
        conn = _connect()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT id, unique_id, created_at, flow, transfer_type, demo_scenario,
                      decision, composite_score, rules_score, fraud_probability
               FROM cases ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_case(case_id: int) -> Optional[dict[str, Any]]:
    """Full row for one case by its row id, with the JSON blob columns
    parsed back into dicts (request / canonical / vendor_results /
    reason_codes keys added alongside the raw *_json columns)."""
    try:
        conn = _connect()
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        conn.close()
        if not row:
            return None
        d = dict(row)
        for col in ("request_json", "canonical_json", "vendor_results_json", "reason_codes_json"):
            if d.get(col):
                d[col.replace("_json", "")] = json.loads(d[col])
        return d
    except Exception:
        return None
