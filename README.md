# Verification Engine — Deterministic Rules + Schemas

Reference implementation for the **Deterministic Score**, **Canonical Normalization
Layer**, and the vendor-facing **MCP tool contracts** shown in the architecture diagram
(Agentic Orchestrator → MCP vendor calls → Canonical Normalization → Deterministic
Score / Fraud ML → LLM Explainability). This package is runnable as-is and includes a
passing test suite driven by a real sample record.

```
verification-engine/
├── README.md                    <- you are here
├── requirements.txt
├── run_demo.py                  <- end-to-end demo on the sample record
├── schemas/
│   ├── canonical_schema.json    <- JSON Schema for the normalized model (source of truth for field names)
│   └── mcp_tools.json           <- MCP tool definitions for the vendor-gateway server (GIACT/Ekata/EWS)
├── rules/
│   ├── identity.yaml            <- Identity verification (fail/verified/review)
│   ├── owner.yaml               <- Account owner verification
│   ├── account.yaml             <- Bank account verification
│   ├── supplementary.yaml       <- Rules A-I: cross-vendor, SSN, address, phone, email, IP, watchlist, account, score bands
│   └── decision_table.yaml      <- Combines the three domain outcomes into one overall decision
├── engine/
│   ├── canonical_mapper.py      <- raw GIACT/Ekata/EWS payloads -> canonical dict (ONLY place vendor field names appear)
│   ├── rules_engine.py          <- condition DSL evaluator + computed-function registry, no eval()
│   ├── decision.py              <- top-level run_decision(canonical) -> full explainability payload
│   └── orchestrator_stub.py     <- deterministic min-input gate + MCP call-loop skeleton for the agent
└── tests/
    ├── sample_data/partial_match.json   <- real sample record (identity-theft labeled)
    └── test_rules_engine.py             <- pytest suite validating the full pipeline against it
```

## Quick start

```bash
cd verification-engine
pip install -r requirements.txt
python run_demo.py            # prints canonical model + full decision payload
pytest tests/ -v               # run the test suite
```

## How a request flows through this package

1. **Agentic Orchestrator** (`engine/orchestrator_stub.py`) receives the inbound
   request, asks an LLM agent which MCP tools apply (`agent_decide_tool_plan`), and
   calls them via `mcp_call`. The deterministic gate `has_min_fields()` — not the
   LLM — decides whether a tool has enough input to be worth calling, per the
   `min_input_rules` in `schemas/mcp_tools.json`.
2. **MCP vendor-gateway** returns each vendor's **raw, unmodified** response.
3. **Canonical Normalization Layer** (`engine/canonical_mapper.py`) turns those raw
   payloads into one dict matching `schemas/canonical_schema.json`. This is the
   only file that should ever reference a GIACT or Ekata field name directly.
4. **Deterministic Score** (`engine/rules_engine.py` + `rules/*.yaml`) evaluates,
   in order:
   - `identity.yaml`, `owner.yaml`, `account.yaml` → one outcome each (`verified` /
     `review` / `fail`), each domain checking **fail rules first**, then
     **verified**, then **review**, falling back to a safe `review` default if
     nothing matches.
   - `supplementary.yaml` (rules A–I) → raises monitoring flags and can **cap** a
     domain's outcome to `review` (e.g. a PO Box address) or **force** it to `fail`
     (e.g. an OFAC hit) — see the `effect` field on each rule.
   - `decision_table.yaml` → combines the three (possibly capped) outcomes into
     one `overall_decision`: `approve` / `manual_review` / `decline`. **Fail rows
     are checked before review rows**, so a hard fail in any one domain always
     forces `decline`, regardless of the other two.
5. **Fraud Risk Score (ML)** — not included in this package — should be trained on
   the *same* canonical dict (see `schemas/canonical_schema.json`) so its features
   never depend on vendor-specific field names either.
6. **LLM Explainability** — the return value of `engine/decision.py:run_decision()`
   is exactly the payload to hand this step: `overall_decision`, every
   `triggered_rule_ids` entry, and `provenance` (which vendor each canonical field
   came from), so the generated reason code can say *"Ekata flagged the address as
   a commercial mail drop"* instead of a generic "system flagged."

## Design decisions worth knowing before extending this

- **No `eval()` anywhere.** Rule conditions are a small closed DSL
  (`all` / `any` / `not` / `{field, op, value}` / `{type: computed, function: ...}`),
  so compliance/risk can review or amend a `.yaml` file without a code review, and
  nothing in a rule file can execute arbitrary code.
- **Fail-first evaluation order** in every domain and in the decision table. A rule
  that would allow "mostly verified" to outrank a single hard-fail condition is a
  bug, not a feature — the ordering has to be fail → verified → review, everywhere.
- **`supplementary.yaml` never introduces a new *primary* outcome.** It can only
  cap an existing outcome down (`cap_outcome`) or force a harder one
  (`force_outcome`); it never upgrades a `review` to `verified`. This keeps audit
  trails simple: you can always explain a `verified`→`review` downgrade, never the
  reverse.
- **Every canonical field is provenance-tagged.** If you add a new field, add its
  source vendor to `_provenance` in `canonical_mapper.py` — the explainability
  step depends on it.
- **Adding a 4th vendor**: one new tool entry in `schemas/mcp_tools.json`, one new
  `map_<vendor>()` function in `canonical_mapper.py`. No changes needed to
  `rules/*.yaml`, `rules_engine.py`, or the orchestrator loop.

## Calibration warnings (do not ship as-is)

The following numeric thresholds are illustrative placeholders and **must be
calibrated against labeled fraud/legit data** before this goes into production:

- `identity.yaml`: `identity_check_score >= 400` / `< 200`, `identity_network_score < 0.30`
- `owner.yaml` / `supplementary.yaml`: `recent_previous_address_count_90d >= 2`,
  address-recency window of 30 days (`C2`)
- `supplementary.yaml`: `email_risk_score >= 0.5` (`E4`), `mailbox_velocity >= 5` (`E3`),
  distance thresholds of 100 miles (`F2`), the SSN issuance age window `[14, 21]` (`B2`)
- `AccountResponseCode` and `ConsumerAlertMessages` codes (e.g. `RT03`, `130`) are
  treated generically here (`fail` = closed/invalid vendor outcomes only). Map the
  full GIACT code table before relying on `G2`'s "some codes force FAIL" note.

## Known gaps / stubs to fill in for production

- `engine/rules_engine.py: account_type_mismatch()` (H2) always returns `False` —
  needs the bank's own account-type field from a richer GIACT response tier.
- `engine/rules_engine.py: current_address_reported_within_days()` (C2) reads
  `supplementary.current_address_reported_days_ago`, which `canonical_mapper.py`
  does not currently populate — wire it up once the "Current" record's
  `DateReported` is surfaced.
- `supplementary.travel_alert_on_file` is hardcoded to `False` in
  `canonical_mapper.py` — replace with a real relocation-notice lookup if one
  becomes available.
- `engine/orchestrator_stub.py` is a skeleton: swap `agent_decide_tool_plan` /
  `agent_infer_missing_field` for real LLM calls (e.g. Claude with the tools from
  `schemas/mcp_tools.json` attached via MCP).
