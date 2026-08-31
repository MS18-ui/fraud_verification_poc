"""
ui/app.py

Demo UI. Simplified flow per latest feedback: sidebar shows account type +
configured vendor priority immediately after loading a sample (no run
needed to see that), request payload is displayed read-only, a persistent
processing message covers the full run until results appear, and results
render flat (decision -> scores -> LLM explanation -> Annex) with no
per-step click-through required.
"""

import json
import time

import streamlit as st

from pipeline import run_pipeline
from data.generated_samples import build_sample_request

st.set_page_config(page_title="Payee Verification Engine", page_icon="\U0001F6E1\ufe0f", layout="wide")

# ---------------------------------------------------------------------------
# US Bank-style theme -- soft blue page background, deeper blue sidebar,
# navy headers, blue primary button.
# ---------------------------------------------------------------------------
PAGE_BG = "#eaf2fb"
SIDEBAR_BG = "#dceafc"
CARD_BG = "#ffffff"
CARD_BORDER = "#d3e3f5"
PRIMARY = "#0b5394"
SUB = "#5b7089"

DECISION_STYLE = {
    "APPROVED": ("#e8f5ee", "#2f8f5b", "\u2713", "Approved"),
    "MANUAL_REVIEW": ("#fbf1de", "#c98a1e", "\u26a0", "Manual Review"),
    "SOFT_DECLINE": ("#fbeee2", "#c8600f", "\u2717", "Soft Decline"),
    "HARD_DECLINE": ("#fbe9e9", "#b23a3a", "\u2717", "Hard Decline"),
}

st.markdown(f"""
<style>
    html, body {{ background-color: {PAGE_BG}; }}
    .stApp {{ background-color: {PAGE_BG}; }}
    [data-testid="stAppViewContainer"] {{ background-color: {PAGE_BG}; }}
    [data-testid="stHeader"] {{ background-color: {PAGE_BG}; }}
    [data-testid="stToolbar"] {{ background-color: {PAGE_BG}; }}
    [data-testid="stSidebar"] {{ background-color: {SIDEBAR_BG}; }}
    .block-container {{ max-width: 1180px; padding-top: 1.6rem; font-family: 'Segoe UI', Arial, sans-serif; }}
    h1, h2, h3 {{ color: {PRIMARY}; font-family: 'Segoe UI', Arial, sans-serif; }}
    body, p, div, span {{ font-family: 'Segoe UI', Arial, sans-serif; }}

    .stButton>button {{
        background-color: {PRIMARY}; color: #fff; border: 1px solid {PRIMARY}; font-weight: 600;
    }}
    .stButton>button:hover {{ background-color: #084578; border-color: #084578; color: #fff; }}

    .metric-card {{
        background: {CARD_BG}; border: 1px solid {CARD_BORDER}; border-radius: 10px;
        padding: 14px 16px; text-align: center;
    }}
    .metric-card .label {{ font-size: 12px; color: {SUB}; font-weight: 600; letter-spacing:.3px; text-transform:uppercase; }}
    .metric-card .value {{ font-size: 26px; font-weight: 700; color: {PRIMARY}; margin-top: 4px; }}

    .decision-card {{
        border-radius: 12px; padding: 20px 26px; margin-bottom: 6px;
    }}
    .decision-card .top-row {{ display:flex; align-items:center; justify-content:space-between; }}
    .decision-card .title {{ font-size: 22px; font-weight:700; }}
    .decision-card .score {{ font-size: 34px; font-weight:800; }}
    .decision-card .score-label {{ font-size: 11px; color:{SUB}; text-align:right; text-transform:uppercase; letter-spacing:.3px;}}
    .decision-card .sub-scores {{
        display:flex; gap:16px; margin-top:18px; padding-top:16px; border-top:1px solid rgba(0,0,0,0.08);
    }}
    .decision-card .sub-scores .sub {{ flex:1; text-align:center; }}
    .decision-card .sub-scores .sub-label {{
        font-size: 11px; color:{SUB}; font-weight:600; letter-spacing:.3px; text-transform:uppercase;
    }}
    .decision-card .sub-scores .sub-value {{ font-size: 22px; font-weight:700; margin-top:4px; }}

    .explain-card {{
        border: 1px solid {CARD_BORDER}; border-left: 5px solid {PRIMARY}; border-radius: 8px;
        padding: 16px 20px; background: {CARD_BG};
    }}
    .explain-card .explain-title {{ font-weight: 700; color: {PRIMARY}; font-size: 14px; margin-bottom: 6px; }}
    .explain-card .explain-text {{ font-size: 13.5px; color: #1c2430; line-height: 1.5; }}

    .internal-demo-tag {{
        display:inline-block; background:{PRIMARY}; color:#fff; font-size: 10.5px;
        font-weight:700; letter-spacing:.6px; padding: 3px 9px; border-radius: 4px;
        text-transform:uppercase; margin-bottom: 10px;
    }}

    .live-step-card {{
        background: {CARD_BG}; border: 1px solid {CARD_BORDER}; border-left: 4px solid {PRIMARY};
        border-radius: 8px; padding: 12px 18px; margin-bottom: 10px;
    }}
    .live-step-card .step-label {{ font-size: 13.5px; font-weight: 700; color: {PRIMARY}; }}
    .live-step-card .step-detail {{ font-size: 12px; color: {SUB}; margin-top: 2px; }}

    .layer-trail-row {{
        display: flex; flex-wrap: wrap; align-items: center; gap: 5px;
        font-size: 10.5px; font-weight: 700; letter-spacing: .2px;
        margin: 6px 0 10px;
    }}
    .layer-trail-step {{ white-space: nowrap; }}
    .layer-trail-arrow {{ color: #b7c4d6; font-size: 11px; margin: 0 1px; }}
</style>
""", unsafe_allow_html=True)

st.title("\U0001F6E1\ufe0f Payee Verification Engine")
st.caption("Agentic orchestration \u2022 deterministic rules \u2022 fraud risk model \u2022 LLM explainability")

# ---------------------------------------------------------------------------
# Sidebar: (1) Load a sample record, then type + vendor priority shown
# immediately below it, before any run.
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown('<div class="internal-demo-tag">Internal Demo</div>', unsafe_allow_html=True)

    if st.button("Load a sample record", use_container_width=True, type="primary"):
        import random as _random
        account_holder_type = "business" if _random.random() < 0.35 else "person"
        st.session_state["sample"] = build_sample_request(account_holder_type=account_holder_type)
        st.session_state.pop("result", None)

loaded_sample = st.session_state.get("sample")

# ---------------------------------------------------------------------------
# (2) Request payload (JSON) -- the actual claimed identity submitted.
# ---------------------------------------------------------------------------
if loaded_sample:
    with st.expander("Request payload (JSON)", expanded=False):
        st.json(loaded_sample["request"])

    run = st.button("Run Payee Validation", type="primary", use_container_width=True)
else:
    st.info("Click **Load a sample record** in the sidebar to get started.")
    run = False

# ---------------------------------------------------------------------------
# (4) Run the FULL pipeline with a LIVE status view -- each line below is
# fired by a real callback from pipeline.py/orchestrator_stub.py as that
# exact step actually executes, not a pre-scripted animation. A small dwell
# floor keeps fast steps legible. Fully removed once results are ready.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# (4) Run the FULL pipeline with a LIVE single-line view -- one placeholder
# that gets OVERWRITTEN on every real callback (not appended to), so only
# the current step is ever visible. Fired by real callbacks from
# pipeline.py/orchestrator_stub.py as each step actually executes, not a
# pre-scripted animation. A small dwell floor keeps fast steps legible.
# Fully removed once results are ready.
# ---------------------------------------------------------------------------
STEP_DWELL_FLOOR = 0.45  # seconds -- just for legibility, not simulated timing

if run:
    status_slot = st.empty()

    def on_step(label, detail="", state="running"):
        icon = {"running": "\u23f3", "complete": "\u2705", "error": "\u26a0\ufe0f"}.get(state, "\u2022")
        status_slot.markdown(f"""
        <div class="live-step-card">
            <div class="step-label">{icon} {label}</div>
            <div class="step-detail">{detail}</div>
        </div>
        """, unsafe_allow_html=True)
        time.sleep(STEP_DWELL_FLOOR)

    result = run_pipeline(
        loaded_sample["request"], account_holder_type=loaded_sample["account_holder_type"],
        on_step=on_step,
    )
    status_slot.empty()  # fully remove -- nothing from this stays on screen
    st.session_state["result"] = result

result = st.session_state.get("result")

# ---------------------------------------------------------------------------
# (5) Decision + scores (unchanged), (6) LLM explainability directly below,
# with Annex collapsible at the end.
# ---------------------------------------------------------------------------
if result:
    decision = result["composite"]["final_decision"]
    bg, color, icon, label = DECISION_STYLE.get(decision, ("#f2f3f5", SUB, "?", decision))

    st.markdown(f"""
    <div class="decision-card" style="background:{bg}; border:1px solid {color}44;">
        <div class="top-row">
            <div class="title" style="color:{color};">{icon} {label}</div>
            <div>
                <div class="score" style="color:{color};">{result['composite']['composite_score']}</div>
                <div class="score-label">Composite score / 100</div>
            </div>
        </div>
        <div class="sub-scores">
            <div class="sub">
                <div class="sub-label">Deterministic Score</div>
                <div class="sub-value" style="color:{PRIMARY};">{result['composite']['rules_score']}</div>
            </div>
            <div class="sub">
                <div class="sub-label">Fraud Probability</div>
                <div class="sub-value" style="color:{PRIMARY};">{result['composite']['fraud_probability']:.1%}</div>
            </div>
            <div class="sub">
                <div class="sub-label">Reason Codes</div>
                <div class="sub-value" style="color:{PRIMARY};">{len(result['composite']['reason_codes'])}</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    LAYER_LABELS_SHORT = {
        "Request Intake": "Request Intake",
        "Agentic Orchestration": "Orchestration",
        "Canonical Normalization": "Normalization",
        "Deterministic Scoring": "Deterministic Scoring",
        "Fraud Risk Model": "Fraud Model",
        "Composite Risk & Decision": "Composite Decision",
        "LLM Explainability": "Explainability",
    }
    STEP_STATUS_COLOR = {
        "complete": "#2f8f5b",       # green -- layer completed
        "error": "#b23a3a",         # red -- layer actually failed
        "skipped": "#9aa4b1",       # gray -- not applicable this run
        "not_applicable": "#9aa4b1",
    }

    trail_parts = ['<div class="layer-trail-row">']
    for i, step in enumerate(result["steps"]):
        label = LAYER_LABELS_SHORT.get(step["step"], step["step"])
        color = STEP_STATUS_COLOR.get(step["status"], "#9aa4b1")
        trail_parts.append(f'<span class="layer-trail-step" style="color:{color};">{label}</span>')
        if i < len(result["steps"]) - 1:
            trail_parts.append('<span class="layer-trail-arrow">\u2192</span>')
    trail_parts.append("</div>")
    st.markdown("".join(trail_parts), unsafe_allow_html=True)

    st.write("")
    st.write("")

    plan_source = result["llm_trace"].get("tool_plan_source", "unknown")
    plan_color = "#2f8f5b" if plan_source == "llm" else "#c98a1e"
    plan_text = f"Claude ({result['llm_trace'].get('tool_plan_model', '')})" if plan_source == "llm" else "Config fallback \u2014 Claude was not reached"
    st.markdown(f'<div style="font-size:12px; color:{plan_color}; font-weight:600; margin-bottom:8px;">'
               f'\U0001F916 Vendor call plan decided by: {plan_text}</div>', unsafe_allow_html=True)

    st.markdown(f"""
    <div class="explain-card">
        <div class="explain-title">\U0001F4AC LLM Explainability</div>
        <div class="explain-text">{result['explanation']['explanation']}</div>
    </div>
    """, unsafe_allow_html=True)
    st.caption(f"Source: {result['explanation']['source']}")

    st.markdown('<div style="height:64px;"></div>', unsafe_allow_html=True)

    with st.expander("Annex"):
        annex_view = st.radio(
            "View", ["Raw vendor responses", "Full API response"],
            horizontal=True, label_visibility="collapsed",
        )
        if annex_view == "Raw vendor responses":
            st.json(result["vendor_results"])
        else:
            st.json(result)
elif loaded_sample:
    st.info("Click **Run Payee Validation** to process the loaded record.")
