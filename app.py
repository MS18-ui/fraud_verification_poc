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

from pipeline import run_pipeline, run_pipeline_waterfall_demo
from engine.reason_codes import enrich_reason_codes

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
    # Waterfall demo's Option B model (engine/vendor_decision.py) -- same
    # visual language, different decision keys. HARD_DECLINE above is
    # shared between both models (same string, same styling).
    "GO": ("#e8f5ee", "#2f8f5b", "\u2713", "Go"),
    "NO_GO": ("#fbf1de", "#c98a1e", "\u26a0", "No-Go"),
    "HIGH_RISK": ("#fbeee2", "#c8600f", "\u26a0", "High Risk"),
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
    /* Streamlit's own button CSS centers the label with higher specificity
       than a plain .stButton>button rule -- override explicitly, on the
       button's flex box AND the inner text container, with !important so
       it wins regardless of Streamlit version internals. */
    div[data-testid="stButton"] > button,
    .stButton > button {{
        justify-content: flex-start !important;
        text-align: left !important;
    }}
    div[data-testid="stButton"] > button > div,
    div[data-testid="stButton"] > button p,
    .stButton > button > div,
    .stButton > button p {{
        text-align: left !important;
        width: 100% !important;
    }}

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

    .step-trail-card {{
        background: {CARD_BG}; border: 1px solid {CARD_BORDER}; border-radius: 10px;
        padding: 18px 22px 16px; margin: 18px 0 20px;
    }}
    .step-trail-title {{
        font-size: 11px; font-weight: 700; letter-spacing: .4px; text-transform: uppercase;
        color: {SUB}; margin-bottom: 14px;
    }}
    .step-trail {{
        display: flex; align-items: flex-start;
        overflow-x: auto; overflow-y: hidden;
        padding-bottom: 10px; margin-bottom: -4px;
        scrollbar-width: thin; scrollbar-color: {CARD_BORDER} transparent;
    }}
    .step-trail::-webkit-scrollbar {{ height: 6px; }}
    .step-trail::-webkit-scrollbar-track {{ background: transparent; }}
    .step-trail::-webkit-scrollbar-thumb {{ background: {CARD_BORDER}; border-radius: 3px; }}
    .step-trail::-webkit-scrollbar-thumb:hover {{ background: #b7c4d6; }}
    .step-trail-item {{
        display: flex; flex-direction: column; align-items: center;
        min-width: 108px; flex: 0 0 108px;
    }}
    .step-trail-circle {{
        width: 30px; height: 30px; border-radius: 50%;
        display: flex; align-items: center; justify-content: center;
        font-size: 13px; font-weight: 700; color: #fff; flex-shrink: 0;
        box-sizing: border-box;
    }}
    .step-trail-circle-pending {{
        background: #fff !important; border: 2px solid #c7d2e0;
    }}
    .step-trail-circle-running {{
        animation: step-trail-pulse 1.1s ease-in-out infinite;
    }}
    @keyframes step-trail-pulse {{
        0%   {{ box-shadow: 0 0 0 0 rgba(11,83,148,0.35); }}
        70%  {{ box-shadow: 0 0 0 7px rgba(11,83,148,0); }}
        100% {{ box-shadow: 0 0 0 0 rgba(11,83,148,0); }}
    }}
    .step-trail-label {{
        font-size: 11px; font-weight: 600; text-align: center; line-height: 1.3;
        margin-top: 8px; padding: 0 4px; max-width: 108px;
    }}
    .step-trail-detail {{
        font-size: 10.5px; font-weight: 400; color: {SUB}; text-align: center;
        line-height: 1.3; margin-top: 3px; padding: 0 4px; max-width: 108px;
    }}
    .step-trail-connector {{
        flex: 0 0 32px; height: 2px; margin-top: 15px; min-width: 32px;
        border-radius: 2px;
    }}
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

    # -----------------------------------------------------------------
    # Two dropdowns -- Transfer Type, then Scenario within that type --
    # instead of a stack of buttons. Feeds the SAME "Request payload
    # (JSON)" + "Run Payee Validation" flow below via
    # session_state["sample"]; session_state["demo_case"] is what tells
    # the run button to use the deterministic waterfall path instead of
    # the normal agentic one. The normal flow is unaffected -- demo_case
    # stays None unless "Load Scenario" is clicked. Which transfer type /
    # scenario a request actually is gets derived entirely from its own
    # fields (see engine/waterfall.py:classify_transfer_type and
    # data/demo_scenarios.py:classify_scenario) -- nothing is tagged.
    # -----------------------------------------------------------------
    from data.demo_scenarios import (
        DOMESTIC_DIRECT, VIRTUAL_ACCOUNT_NO_EWS, EWS_OUTAGE, DOMESTIC_RANDOM,
        INTERNATIONAL_MATCH, ONBOARDING_MATCH,
        build_domestic_direct_request, build_virtual_account_request, build_ews_outage_request,
        build_domestic_random_request,
        build_international_match_request, build_onboarding_request,
    )

    SCENARIOS_BY_TYPE = {
        "Domestic": [
            ("Direct match", DOMESTIC_DIRECT, build_domestic_direct_request),
            ("Virtual Account (no EWS data)", VIRTUAL_ACCOUNT_NO_EWS, build_virtual_account_request),
            ("Simulate EWS Outage", EWS_OUTAGE, build_ews_outage_request),
            ("Random (all vendors called)", DOMESTIC_RANDOM, build_domestic_random_request),
        ],
        "International": [
            ("Match", INTERNATIONAL_MATCH, build_international_match_request),
        ],
        "Onboarding": [
            ("Match", ONBOARDING_MATCH, build_onboarding_request),
        ],
    }

    transfer_type_choice = st.selectbox("Transfer Type", list(SCENARIOS_BY_TYPE.keys()))
    scenario_options = SCENARIOS_BY_TYPE[transfer_type_choice]
    scenario_choice = st.selectbox("Scenario", [s[0] for s in scenario_options])

    if st.button("Load Scenario", use_container_width=True, type="primary"):
        _, demo_case_label, request_builder = next(s for s in scenario_options if s[0] == scenario_choice)
        st.session_state["sample"] = {"request": request_builder(), "account_holder_type": "person"}
        st.session_state.pop("result", None)
        st.session_state["demo_case"] = demo_case_label

loaded_sample = st.session_state.get("sample")

# ---------------------------------------------------------------------------
# (2) Request payload (JSON) -- the actual claimed identity submitted.
# ---------------------------------------------------------------------------
if loaded_sample:
    with st.expander("Request payload (JSON)", expanded=False):
        # life_cycle_stage genuinely drives routing (engine/waterfall.py:
        # classify_transfer_type) but isn't shown here -- display-only
        # filtering, the field is still present on the real request object
        # passed into the pipeline below.
        display_request = {k: v for k, v in loaded_sample["request"].items() if k != "life_cycle_stage"}
        st.json(display_request)

    run = st.button("Run Payee Validation", type="primary", use_container_width=True)
else:
    st.info("Choose a Transfer Type and Scenario, then click **Load Scenario** in the sidebar to get started.")
    run = False

# ---------------------------------------------------------------------------
# Shared step-trail config + renderer -- used both for the LIVE view while
# the pipeline is running and for the final static view, so the stepper
# never "changes shape" between the two: it just fills in as steps complete.
# ---------------------------------------------------------------------------
LAYER_LABELS_SHORT = {
    "Request Intake":"Request Processing",
    "Agentic Orchestration": "Vendor Requests",
    "Waterfall Orchestration": "Vendor Requests",  # demo flow's orchestration step -- same display label
    "Canonical Normalization": "Vendor Response Canonical Model",
    "Vendor Score Decision": "Vendor Score (as received)",
    "Deterministic Scoring": "Deterministic Score",
    "Fraud Risk Model": "Probabilistic Score",
    "Composite Risk & Decision": "Composite Decision",
    "LLM Explainability": "Explainability",
}
# Two different flows emit two different orchestration-step names -- the
# live trail needs to watch for the right one depending on which pipeline
# function is about to run, or the "Vendor Requests" bubble for whichever
# name never fires just sits pending forever (this was a real bug: demo
# scenario runs emit "Waterfall Orchestration", but the live loop was only
# ever watching for "Agentic Orchestration").
#
# Team decision, 2026-08-06 working call: the demo flow now runs BOTH
# layers -- the vendor's own score/code (Option B, unchanged, kept as the
# top "as received" layer) AND the deterministic + probabilistic composite
# (restored as a second, lower layer, per "don't throw away what you have
# done ... keep it for now"). That's 8 steps, not 5 or 7. The standard/
# agentic flow (ALL_STEPS_NORMAL) is untouched -- still its own 7-step
# rules+fraud+composite model.
ALL_STEPS_NORMAL = ["Request Intake", "Agentic Orchestration", "Canonical Normalization",
                     "Deterministic Scoring", "Fraud Risk Model", "Composite Risk & Decision", "LLM Explainability"]
ALL_STEPS_DEMO = ["Request Intake", "Waterfall Orchestration", "Canonical Normalization",
                   "Vendor Score Decision", "Deterministic Scoring", "Fraud Risk Model",
                   "Composite Risk & Decision", "LLM Explainability"]

STEP_STATUS_COLOR = {
    "pending": "#9aa4b1",         # gray outline -- not reached yet
    "running": PRIMARY,           # blue, pulsing -- executing right now
    "complete": "#2f8f5b",        # green -- step completed / GO
    "warning": "#c98a1e",         # amber -- Composite Decision node, MANUAL_REVIEW / NO_GO
    "high_risk": "#c8600f",       # dark orange -- Vendor Score Decision node, HIGH_RISK band specifically
    "error": "#b23a3a",           # red -- step actually failed / decline node
    "skipped": "#9aa4b1",         # gray -- not applicable this run
    "not_applicable": "#9aa4b1",
}
STEP_STATUS_ICON = {
    "running": "\u23f3",
    "complete": "\u2713",
    "warning": "\u26a0",
    "high_risk": "\u26a0",
    "error": "\u2717",
    "skipped": "\u2013",
    "not_applicable": "\u2013",
}


def render_step_trail(step_tuples):
    """step_tuples: ordered list of (raw_step_name, status, detail_text)."""
    n = len(step_tuples)
    parts = [
        '<div class="step-trail-card">',
        '<div class="step-trail-title">Orchestration Flow</div>',
        '<div class="step-trail">',
    ]
    for i, (name, status, detail) in enumerate(step_tuples):
        label = LAYER_LABELS_SHORT.get(name, name)
        color = STEP_STATUS_COLOR.get(status, "#9aa4b1")
        pending = status == "pending"
        glyph = str(i + 1) if pending else STEP_STATUS_ICON.get(status, str(i + 1))
        circle_class = "step-trail-circle"
        circle_style = f"background:{color};"
        if pending:
            circle_class += " step-trail-circle-pending"
            circle_style = f"color:{color};"
        elif status == "running":
            circle_class += " step-trail-circle-running"
        detail_html = f'<div class="step-trail-detail">{detail}</div>' if detail else ""
        item_html = (
            f'<div class="step-trail-item">'
            f'<div class="{circle_class}" style="{circle_style}">{glyph}</div>'
            f'<div class="step-trail-label" style="color:{color};">{label}</div>'
            f'{detail_html}'
            f'</div>'
        )
        parts.append(item_html)
        if i < n - 1:
            parts.append(f'<div class="step-trail-connector" style="background:{color}55;"></div>')
    parts.append("</div></div>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# (4) Run the FULL pipeline with a LIVE step trail -- the same stepper used
# for the final view is re-rendered on every real callback fired by
# pipeline.py/orchestrator_stub.py as each step actually executes (not a
# pre-scripted animation): steps before the current one show complete/error,
# the current one pulses as "running", and steps not reached yet stay
# pending. A small dwell floor keeps fast steps legible.
# ---------------------------------------------------------------------------
STEP_DWELL_FLOOR = 0.35  # seconds -- just for legibility, not simulated timing

if run:
    trail_slot = st.empty()
    live_status = {}
    live_detail = {}
    demo_case = st.session_state.get("demo_case")
    current_steps = ALL_STEPS_DEMO if demo_case else ALL_STEPS_NORMAL
    orchestration_step_name = "Waterfall Orchestration" if demo_case else "Agentic Orchestration"

    def on_step(label, detail="", state="running"):
        if label.startswith("Calling "):
            # Per-vendor sub-events from engine/waterfall.py (e.g. "Calling
            # EWS", "Calling LSEG") -- reflected as the live status/detail of
            # the single "Vendor Requests" bubble itself, not as separate
            # disconnected steps. The bubble stays "running" (with the
            # detail text updating per vendor tried) until the real
            # "Waterfall Orchestration" complete event arrives below.
            live_status[orchestration_step_name] = "running"
            live_detail[orchestration_step_name] = f"{label}: {detail}"
        else:
            live_status[label] = state
            live_detail[label] = detail
        tuples = [
            (name, live_status.get(name, "pending"), live_detail.get(name, ""))
            for name in current_steps
        ]
        trail_slot.markdown(render_step_trail(tuples), unsafe_allow_html=True)
        time.sleep(STEP_DWELL_FLOOR)

    if demo_case:
        result = run_pipeline_waterfall_demo(
            loaded_sample["request"], account_holder_type=loaded_sample["account_holder_type"],
            on_step=on_step,
        )
    else:
        result = run_pipeline(
            loaded_sample["request"], account_holder_type=loaded_sample["account_holder_type"],
            on_step=on_step,
        )
    trail_slot.empty()  # the final block below re-renders the finished trail
    st.session_state["result"] = result
    st.session_state["step_details"] = dict(live_detail)

    from data.case_store import save_case
    save_case(
        request=loaded_sample["request"],
        result=result,
        flow="waterfall_demo" if demo_case else "agentic",
        transfer_type=result.get("transfer_type"),
        demo_scenario=demo_case,
    )

result = st.session_state.get("result")

# ---------------------------------------------------------------------------
# (5) Decision + scores (unchanged), (6) LLM explainability directly below,
# with Annex collapsible at the end.
# ---------------------------------------------------------------------------
if result:
    if result.get("waterfall_trail") and any(hop["outcome"] == "outage" for hop in result["waterfall_trail"]):
        outage_hop = next(hop for hop in result["waterfall_trail"] if hop["outcome"] == "outage")
        fallback_hop = next((hop for hop in result["waterfall_trail"] if hop["outcome"] == "success"), None)
        fallback_name = fallback_hop["vendor"] if fallback_hop else "the next source"
        st.markdown(f"""
        <div style="background:#fdecea; border:1px solid #f3b8b0; border-radius:10px; padding:14px 18px; margin-bottom:16px;">
            <div style="font-weight:700; color:#b23a3a; font-size:15px;">\u26a1 Automatic Failover \u2014 No Manual Intervention</div>
            <div style="color:#7a3030; font-size:13px; margin-top:4px;">
                {outage_hop['vendor']} was unavailable ({outage_hop['reason'].split('\u2014', 1)[-1].strip()}).
                The waterfall detected this and automatically rerouted to <b>{fallback_name}</b> \u2014
                no operator action, no ticket, no manual retry.
            </div>
        </div>
        """, unsafe_allow_html=True)

    is_vendor_score_model = "vendor_decision" in result
    cached_details = st.session_state.get("step_details", {})
    final_tuples = [
        (step["step"], step["status"], cached_details.get(step["step"], ""))
        for step in result["steps"]
    ]

    if is_vendor_score_model:
        # --- TOP LAYER: vendor's own score/code, exactly as received ---
        st.markdown(f'<div style="font-weight:700; color:{PRIMARY}; font-size:13px; '
                    f'text-transform:uppercase; letter-spacing:.5px; margin-bottom:6px;">'
                    f'As received from vendor</div>', unsafe_allow_html=True)

        vd = result["vendor_decision"]
        decision = vd["decision"]
        bg, color, icon, label = DECISION_STYLE.get(decision, ("#f2f3f5", SUB, "?", decision))
        native_text = str(vd["native_score"]) if vd["native_score"] is not None else "\u2014"

        st.markdown(f"""
        <div class="decision-card" style="background:{bg}; border:1px solid {color}44;">
            <div class="top-row">
                <div class="title" style="color:{color};">{icon} {label}</div>
                <div>
                    <div class="score" style="color:{color};">{native_text}</div>
                    <div class="score-label">{vd['vendor'] or 'Vendor'} native score/code</div>
                </div>
            </div>
            <div class="sub-scores">
                <div class="sub">
                    <div class="sub-label">Native Scale</div>
                    <div class="sub-value" style="color:{PRIMARY}; font-size:13px;">{vd['native_scale'] or '\u2014'}</div>
                </div>
                <div class="sub">
                    <div class="sub-label">Normalized (0-10)</div>
                    <div class="sub-value" style="color:{PRIMARY};">{vd['normalized_score'] if vd['normalized_score'] is not None else '\u2014'}</div>
                </div>
                <div class="sub">
                    <div class="sub-label">Decision Band</div>
                    <div class="sub-value" style="color:{PRIMARY}; font-size:13px;">{vd['band_range']}</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        if vd["checks"]:
            with st.expander(f"{vd['vendor'] or 'Vendor'} \u2014 fields as received ({len(vd['checks'])})"):
                for c in vd["checks"]:
                    st.markdown(f'<div style="font-size:13px; padding:3px 0;">'
                                f'<b>{c["label"]}:</b> {c["value"]}</div>', unsafe_allow_html=True)

        # Generic multi-vendor display: normal scenarios only ever have ONE
        # successful entry here (the terminal vendor, already shown above),
        # so this section renders nothing extra for them. The Random
        # scenario (engine/waterfall.py's call_all mode) is the only one
        # where more than one vendor actually answered in the same run --
        # this shows every one of them, each still attributed only to
        # itself ("never blended" holds here too).
        other_vendors = [
            (tool, info) for tool, info in result["vendor_scores"].items()
            if info.get("status") == "success" and info.get("vendor") != vd["vendor"]
        ]
        if other_vendors:
            st.write("")
            st.markdown(f'<div style="font-weight:700; color:{SUB}; font-size:11px; '
                        f'text-transform:uppercase; letter-spacing:.3px; margin-bottom:8px;">'
                        f'Also called this run \u2014 every vendor\'s own response, unblended</div>', unsafe_allow_html=True)
            cols = st.columns(len(other_vendors))
            for col, (tool, info) in zip(cols, other_vendors):
                other_native = str(info["native_score"]) if info["native_score"] is not None else "\u2014"
                other_norm = info["normalized_score"] if info["normalized_score"] is not None else "\u2014"
                with col:
                    st.markdown(f"""
                    <div style="background:#fafbfc; border:1px solid #e3e8ef; border-radius:8px; padding:12px 14px; text-align:center;">
                        <div style="font-weight:700; color:{PRIMARY}; font-size:13px;">{info['vendor']}</div>
                        <div style="font-size:24px; font-weight:800; color:{PRIMARY}; margin:4px 0;">{other_native}</div>
                        <div style="font-size:10.5px; color:{SUB};">{info.get('native_scale') or '\u2014'}</div>
                        <div style="font-size:11px; color:{SUB}; margin-top:4px;">Normalized: {other_norm}/10</div>
                    </div>
                    """, unsafe_allow_html=True)
                    if info.get("checks"):
                        with st.expander(f"{info['vendor']} \u2014 fields as received ({len(info['checks'])})"):
                            for c in info["checks"]:
                                st.markdown(f'<div style="font-size:13px; padding:3px 0;">'
                                            f'<b>{c["label"]}:</b> {c["value"]}</div>', unsafe_allow_html=True)

        st.write("")
        st.markdown('<div style="height:1px; background:#e3e8ef; margin:8px 0 16px 0;"></div>', unsafe_allow_html=True)

        # --- Orchestration flow -- moved above the composite view on request ---
        st.markdown(render_step_trail(final_tuples), unsafe_allow_html=True)
        st.write("")

        # --- BOTTOM LAYER: deterministic + probabilistic composite ---
        # Deliberately small and muted -- an alternate view, not a second
        # headline decision (team decision, 2026-08-06 follow-up).
        st.markdown(f'<div style="font-weight:700; color:{SUB}; font-size:12px; '
                    f'text-transform:uppercase; letter-spacing:.5px; margin-bottom:6px;">'
                    f'Composite view (deterministic + probabilistic) \u2014 another way to look at it</div>', unsafe_allow_html=True)

        c_decision = result["composite"]["final_decision"]
        c_bg, c_color, c_icon, c_label = DECISION_STYLE.get(c_decision, ("#f2f3f5", SUB, "?", c_decision))
        st.markdown(f"""
        <div style="background:#fafbfc; border:1px solid #e3e8ef; border-radius:8px; padding:10px 14px;">
            <div style="display:flex; align-items:center; justify-content:space-between;">
                <div style="font-size:13px; font-weight:600; color:{c_color};">{c_icon} {c_label}</div>
                <div style="font-size:18px; font-weight:700; color:{c_color};">{result['composite']['composite_score']}<span style="font-size:11px; color:{SUB}; font-weight:400;"> /100</span></div>
            </div>
            <div style="display:flex; gap:18px; margin-top:8px; font-size:11.5px; color:{SUB};">
                <div>Deterministic: <b style="color:{PRIMARY};">{result['composite']['rules_score']}</b></div>
                <div>Risk Probability: <b style="color:{PRIMARY};">{result['composite']['fraud_probability']:.1%}</b></div>
                <div>Reason Codes: <b style="color:{PRIMARY};">{len(result['composite']['reason_codes'])}</b></div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.caption(f"\U0001F4A1 {result['explanation'].get('business_reasoning', '')}")
    else:
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

    if not is_vendor_score_model:
        st.markdown(render_step_trail(final_tuples), unsafe_allow_html=True)

    st.write("")
    st.write("")

    if "llm_trace" in result:
        plan_source = result["llm_trace"].get("tool_plan_source", "unknown")
        plan_color = "#2f8f5b" if plan_source == "llm" else "#c98a1e"
        plan_text = f"Claude ({result['llm_trace'].get('tool_plan_model', '')})" if plan_source == "llm" else "Config fallback \u2014 Claude was not reached"
        st.markdown(f'<div style="font-size:12px; color:{plan_color}; font-weight:600; margin-bottom:8px;">'
                   f'\U0001F916 Vendor call plan decided by: {plan_text}</div>', unsafe_allow_html=True)

    if is_vendor_score_model:
        bottom_one_liner = result["explanation"].get("bottom_explanation", "")
        reason_codes = result.get("reason_codes_enriched") or []

        st.markdown(f"""
        <div style="background:#fafbfc; border:1px solid #e3e8ef; border-radius:8px; padding:10px 14px;">
            <div style="font-weight:700; color:{SUB}; font-size:11px; text-transform:uppercase; letter-spacing:.3px; margin-bottom:4px;">Composite Explanation</div>
            <div style="font-size:12px; color:#333;">{bottom_one_liner}</div>
        </div>
        """, unsafe_allow_html=True)

        if reason_codes:
            with st.expander(f"Reason codes ({len(reason_codes)})"):
                for rc in reason_codes:
                    source_suffix = f" (Source: {rc['source']})" if rc["source"] != "\u2014" else ""
                    st.markdown(f'<div style="font-size:12.5px; padding:4px 0;">'
                                f'<b style="color:{PRIMARY};">{rc["code"]}</b><br>{rc["description"]}{source_suffix}</div>',
                                unsafe_allow_html=True)
    else:
        one_liner = (
            f"The recommendation is {result['composite']['final_decision']}. "
            f"Composite score is {result['composite']['composite_score']}/100 "
            f"(rules component {result['composite']['rules_score']}/100, "
            f"estimated fraud probability {result['composite']['fraud_probability']:.1%})."
        )
        reason_codes = enrich_reason_codes(
            result["composite"]["reason_codes"], result["canonical"].get("_provenance", {})
        )

        reason_html = ""
        if reason_codes:
            rows = []
            for rc in reason_codes:
                source_suffix = f" (Source: {rc['source']})" if rc["source"] != "\u2014" else ""
                rows.append(
                    f'<div style="margin-top:10px;">'
                    f'<div style="font-weight:700; color:{PRIMARY}; font-size:13px;">{rc["code"]}</div>'
                    f'<div style="font-size:13px; color:#1c2430;">{rc["description"]}{source_suffix}</div>'
                    f'</div>'
                )
            reason_html = (
                f'<div style="margin-top:12px; font-weight:700; color:#1c2430; font-size:13px;">'
                f'Below are the reason codes ({len(reason_codes)})</div>' + "".join(rows)
            )

        st.markdown(f"""
        <div class="explain-card">
            <div class="explain-title">\U0001F4AC LLM Explainability</div>
            <div class="explain-text">{one_liner}</div>
            {reason_html}
        </div>
        """, unsafe_allow_html=True)
        st.caption(f"Source: {result['explanation']['source']}")

    st.markdown('<div style="height:64px;"></div>', unsafe_allow_html=True)

    with st.expander("Annex"):
        annex_view = st.radio(
            "View", ["Raw vendor responses", "Canonical model", "Vendor Data Changes", "Full API response"],
            horizontal=True, label_visibility="collapsed",
        )
        if annex_view == "Raw vendor responses":
            st.json(result["vendor_results"])
        elif annex_view == "Canonical model":
            st.caption("Field-level detail \u2014 which canonical field came from which vendor.")
            import pandas as pd
            from engine.reason_codes import SOURCE_DISPLAY
            canonical = result["canonical"]
            provenance = canonical.get("_provenance", {})
            rows = []
            for domain_key, domain_val in canonical.items():
                if domain_key in ("_provenance", "unique_id") or not isinstance(domain_val, dict):
                    continue
                for field, value in domain_val.items():
                    full_key = f"{domain_key}.{field}"
                    source = provenance.get(full_key) if value is not None else None
                    rows.append({
                        "Domain": domain_key.title(),
                        "Field": field,
                        "Value": value,
                        "Source": SOURCE_DISPLAY.get(source, source.upper() if source else "\u2014"),
                    })
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)
        elif annex_view == "Vendor Data Changes":
            st.caption("Audit trail \u2014 vendor-returned data that changed since the last time this "
                       "identity was checked (data/case_store.py:check_and_log_vendor_changes).")
            changes = result.get("vendor_data_changes") or []
            if not changes:
                st.info("No changes detected vs. the most recent prior request for this identity "
                        "(or this is the first time it's been seen).")
            else:
                for c in changes:
                    st.markdown(f"**{c['tool_name']}** \u2014 changed since {c['previous_seen_at']}")
                    for f in c["changed_fields"]:
                        st.markdown(f'<div style="font-size:13px; padding:2px 0;">'
                                    f'<code>{f["field"]}</code>: {f["old"]} \u2192 <b>{f["new"]}</b></div>',
                                    unsafe_allow_html=True)
        else:
            st.json(result)
elif loaded_sample:
    st.info("Click **Run Payee Validation** to process the loaded record.")