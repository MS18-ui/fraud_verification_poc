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
