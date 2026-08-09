"""
ui/pages/1_Observability.py

Observability dashboard for the Payee Verification Engine demo. Reads
directly from the same SQLite `cases` table that data/case_store.py's
save_case() writes to on every run (see ui/app.py) -- nothing new is
written here, this page is read-only.

Assumption worth flagging: the `cases` table does not persist a "vendor"
column of its own (save_case() only stores composite-level fields plus
the raw request/canonical/vendor_results/reason_codes JSON blobs). To
answer "approved by vendor" we infer a per-record *primary vendor* from
canonical_json["_provenance"] -- whichever vendor supplied the most
canonical fields for that record (ews / giact / ekata -> EWS / LSEG /
Ekata). That's a reasonable proxy for "which vendor's answer drove this
case," but it is an inference, not a stored fact -- if a persisted vendor
column gets added to the schema later, swap primary_vendor_from_provenance()
out for a direct column read.
"""
import sys
import json
import sqlite3
from pathlib import Path
from collections import Counter
from datetime import datetime, timedelta, timezone

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Observability | Payee Verification Engine", page_icon="\U0001F4CA", layout="wide")

# ---------------------------------------------------------------------------
# Theme -- mirrors ui/app.py so the two pages feel like one product.
# ---------------------------------------------------------------------------
PAGE_BG = "#10166d"
SIDEBAR_BG = "#ffffff"
CARD_BG = "#ffffff"
INNER_BG = "#fafbfc"
CARD_BORDER = "#e3e8ef"
PRIMARY = "#235ae4"
SUB = "#5b7089"

DECISION_COLORS = {
    "APPROVED": ("#e8f5ee", "#2f8f5b"),
    "MANUAL_REVIEW": ("#fbf1de", "#c98a1e"),
    "SOFT_DECLINE": ("#fbeee2", "#c8600f"),
    "HARD_DECLINE": ("#fbe9e9", "#b23a3a"),
}
VENDOR_CODE_TO_NAME = {"ews": "EWS", "giact": "LSEG", "ekata": "Ekata"}

st.markdown(f"""
<style>
    html, body {{ background-color: {PAGE_BG}; }}
    .stApp {{ background-color: {PAGE_BG}; }}
    [data-testid="stAppViewContainer"] {{ background-color: {PAGE_BG}; }}
    [data-testid="stHeader"] {{ background-color: {PAGE_BG}; }}
    [data-testid="stToolbar"] {{ background-color: {PAGE_BG}; }}
    [data-testid="stSidebar"] {{ background-color: {SIDEBAR_BG}; border-right: 1px solid {CARD_BORDER}; }}
    .block-container {{
        max-width: 1280px; padding: 2rem 2.5rem 3rem; margin-top: 1.5rem;
        background: {CARD_BG}; border-radius: 16px;
        box-shadow: 0 8px 28px rgba(16,22,109,0.28);
        font-family: 'Segoe UI', Arial, sans-serif;
    }}
    h1, h2, h3 {{ color: {PRIMARY}; font-family: 'Segoe UI', Arial, sans-serif; }}
    body, p, div, span {{ font-family: 'Segoe UI', Arial, sans-serif; }}

    .stButton>button {{
        background-color: {PRIMARY}; color: #fff; border: 1px solid {PRIMARY}; font-weight: 600;
    }}
    .stButton>button:hover {{ background-color: #1a46b8; border-color: #1a46b8; color: #fff; }}

    .metric-card {{
        background: {INNER_BG}; border: 1px solid {CARD_BORDER}; border-radius: 10px;
        padding: 14px 16px; text-align: center; height: 100%;
    }}
    .metric-card .label {{ font-size: 12px; color: {SUB}; font-weight: 600; letter-spacing:.3px; text-transform:uppercase; }}
    .metric-card .value {{ font-size: 26px; font-weight: 700; color: {PRIMARY}; margin-top: 4px; }}
    .metric-card .sub {{ font-size: 11px; color: {SUB}; margin-top: 2px; }}

    .section-card {{
        background: {INNER_BG}; border: 1px solid {CARD_BORDER}; border-radius: 10px;
        padding: 18px 20px; margin-bottom: 18px;
    }}
    .section-title {{
        font-size: 12px; font-weight: 700; letter-spacing: .4px; text-transform: uppercase;
        color: {SUB}; margin-bottom: 12px;
    }}
</style>
""", unsafe_allow_html=True)

st.title("\U0001F4CA Observability")
st.caption("Live view over every record processed by the engine \u2014 volume, decisions, vendor mix, and scores.")

# ---------------------------------------------------------------------------
# Data access -- read-only against the same DB save_case() writes to.
# ---------------------------------------------------------------------------
try:
    from data.case_store import _connect
except ImportError:
    _connect = None


@st.cache_data(ttl=15, show_spinner=False)
def load_cases(limit: int) -> pd.DataFrame:
    if _connect is None:
        raise RuntimeError("Could not import data.case_store._connect")
    conn = _connect()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM cases ORDER BY created_at DESC LIMIT ?", conn, params=(limit,)
        )
    finally:
        conn.close()
    return df


def primary_vendor_from_provenance(canonical_json: str) -> str:
    """Best-effort 'which vendor drove this case' -- the mode of the
    canonical model's field-level provenance map. Returns 'Unknown' if the
    record has no canonical JSON or no provenance (e.g. save failed to
    capture it)."""
    if not canonical_json:
        return "Unknown"
    try:
        canonical = json.loads(canonical_json)
    except (TypeError, json.JSONDecodeError):
        return "Unknown"
    provenance = (canonical or {}).get("_provenance") or {}
    if not provenance:
        return "Unknown"
    top_code, _ = Counter(provenance.values()).most_common(1)[0]
    if not isinstance(top_code, str):
        return "Unknown"
    return VENDOR_CODE_TO_NAME.get(top_code, top_code.upper())


def metric_card(label: str, value: str, sub: str = "") -> str:
    sub_html = f'<div class="sub">{sub}</div>' if sub else ""
    return (
        f'<div class="metric-card"><div class="label">{label}</div>'
        f'<div class="value">{value}</div>{sub_html}</div>'
    )


# ---------------------------------------------------------------------------
# Sidebar controls
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(f'<div style="font-weight:700; color:{PRIMARY}; font-size:13px; '
                f'text-transform:uppercase; letter-spacing:.5px; margin-bottom:10px;">Filters</div>',
                unsafe_allow_html=True)
    row_limit = st.selectbox("Records to load", [200, 500, 1000, 5000, 20000], index=2)
    if st.button("Refresh data", use_container_width=True):
        load_cases.clear()

try:
    raw_df = load_cases(row_limit)
except Exception as exc:
    st.info(
        "No records to show yet \u2014 either nothing has been processed through "
        "**Run Payee Validation** on the main page, or the cases table isn't "
        f"reachable ({exc})."
    )
    st.stop()

if raw_df.empty:
    st.info("No records to show yet \u2014 process a request on the main page and it'll show up here.")
    st.stop()

# ---------------------------------------------------------------------------
# Enrich
# ---------------------------------------------------------------------------
df = raw_df.copy()
df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
df["primary_vendor"] = df.get("canonical_json", pd.Series([None] * len(df))).apply(primary_vendor_from_provenance)
df["composite_score"] = pd.to_numeric(df.get("composite_score"), errors="coerce")
df["rules_score"] = pd.to_numeric(df.get("rules_score"), errors="coerce")
df["fraud_probability"] = pd.to_numeric(df.get("fraud_probability"), errors="coerce")
df["decision"] = df.get("decision", pd.Series(["UNKNOWN"] * len(df))).fillna("UNKNOWN")

with st.sidebar:
    decisions_avail = sorted(df["decision"].unique().tolist())
    decision_filter = st.multiselect("Decision", decisions_avail, default=decisions_avail)

    flows_avail = sorted([f for f in df["flow"].dropna().unique().tolist()]) if "flow" in df else []
    flow_filter = st.multiselect("Flow", flows_avail, default=flows_avail) if flows_avail else []

    types_avail = sorted([t for t in df["transfer_type"].dropna().unique().tolist()]) if "transfer_type" in df else []
    type_filter = st.multiselect("Transfer type", types_avail, default=types_avail) if types_avail else []

    vendors_avail = sorted(df["primary_vendor"].unique().tolist())
    vendor_filter = st.multiselect("Primary vendor", vendors_avail, default=vendors_avail)

    search_id = st.text_input("Search unique ID")

mask = df["decision"].isin(decision_filter) & df["primary_vendor"].isin(vendor_filter)
if flow_filter:
    mask &= df["flow"].isin(flow_filter)
if type_filter:
    mask &= df["transfer_type"].isin(type_filter)
if search_id:
    mask &= df["unique_id"].astype(str).str.contains(search_id, case=False, na=False)
fdf = df[mask].copy()

if fdf.empty:
    st.warning("No records match the current filters.")
    st.stop()

# ---------------------------------------------------------------------------
# KPI row
# ---------------------------------------------------------------------------
total = len(fdf)
approved = int((fdf["decision"] == "APPROVED").sum())
manual_review = int((fdf["decision"] == "MANUAL_REVIEW").sum())
declined = int(fdf["decision"].isin(["SOFT_DECLINE", "HARD_DECLINE"]).sum())
approval_rate = approved / total * 100 if total else 0
avg_composite = fdf["composite_score"].mean()
avg_fraud_prob = fdf["fraud_probability"].mean()

k1, k2, k3, k4, k5 = st.columns(5)
k1.markdown(metric_card("Total Processed", f"{total:,}"), unsafe_allow_html=True)
k2.markdown(metric_card("Approved", f"{approved:,}", f"{approval_rate:.1f}% approval rate"), unsafe_allow_html=True)
k3.markdown(metric_card("Manual Review", f"{manual_review:,}",
                         f"{manual_review/total*100:.1f}%" if total else ""), unsafe_allow_html=True)
k4.markdown(metric_card("Declined", f"{declined:,}",
                         f"{declined/total*100:.1f}%" if total else ""), unsafe_allow_html=True)
k5.markdown(metric_card("Avg Composite Score", f"{avg_composite:.1f}" if pd.notna(avg_composite) else "\u2014",
                         f"Avg fraud prob {avg_fraud_prob:.1%}" if pd.notna(avg_fraud_prob) else ""),
            unsafe_allow_html=True)

st.write("")

# ---------------------------------------------------------------------------
# Decision breakdown + volume trend
# ---------------------------------------------------------------------------
col_a, col_b = st.columns([1, 1.4])

with col_a:
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Decisions</div>', unsafe_allow_html=True)
    decision_counts = fdf["decision"].value_counts()
    for dec, count in decision_counts.items():
        bg, color = DECISION_COLORS.get(dec, ("#f2f3f5", SUB))
        pct = count / total * 100
        st.markdown(f"""
        <div style="display:flex; align-items:center; gap:10px; margin-bottom:8px;">
            <div style="width:90px; font-size:12.5px; font-weight:600; color:{color};">{dec}</div>
            <div style="flex:1; background:{bg}; border-radius:6px; height:18px; position:relative;">
                <div style="width:{pct:.1f}%; background:{color}; height:100%; border-radius:6px;"></div>
            </div>
            <div style="width:70px; text-align:right; font-size:12px; color:{SUB};">{count} ({pct:.1f}%)</div>
        </div>
        """, unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

with col_b:
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Daily volume</div>', unsafe_allow_html=True)
    daily = (
        fdf.dropna(subset=["created_at"])
        .assign(day=lambda d: d["created_at"].dt.date)
        .groupby("day")
        .agg(processed=("unique_id", "count"), approved=("decision", lambda s: (s == "APPROVED").sum()))
    )
    if not daily.empty:
        st.line_chart(daily, height=220)
    else:
        st.caption("No timestamped records to chart.")
    st.markdown('</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Vendor breakdown -- approvals by primary vendor + full vendor summary
# ---------------------------------------------------------------------------
col_c, col_d = st.columns([1, 1.4])

with col_c:
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Approved requests by vendor</div>', unsafe_allow_html=True)
    approved_by_vendor = fdf[fdf["decision"] == "APPROVED"]["primary_vendor"].value_counts()
    if not approved_by_vendor.empty:
        st.bar_chart(approved_by_vendor, height=240, color=PRIMARY)
    else:
        st.caption("No approved records in the current filter.")
    st.markdown('</div>', unsafe_allow_html=True)

with col_d:
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Vendor performance summary</div>', unsafe_allow_html=True)
    vendor_summary = (
        fdf.groupby("primary_vendor")
        .agg(
            total=("unique_id", "count"),
            approved=("decision", lambda s: (s == "APPROVED").sum()),
            manual_review=("decision", lambda s: (s == "MANUAL_REVIEW").sum()),
            declined=("decision", lambda s: s.isin(["SOFT_DECLINE", "HARD_DECLINE"]).sum()),
            avg_composite_score=("composite_score", "mean"),
            avg_fraud_probability=("fraud_probability", "mean"),
        )
        .sort_values("total", ascending=False)
    )
    vendor_summary["approval_rate"] = (vendor_summary["approved"] / vendor_summary["total"] * 100).round(1)
    vendor_summary["avg_composite_score"] = vendor_summary["avg_composite_score"].round(1)
    vendor_summary["avg_fraud_probability"] = (vendor_summary["avg_fraud_probability"] * 100).round(2)
    st.dataframe(
        vendor_summary.rename(columns={
            "avg_fraud_probability": "avg_fraud_prob_%",
        }),
        use_container_width=True,
    )
    st.markdown('</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Score distributions
# ---------------------------------------------------------------------------
col_e, col_f = st.columns(2)

with col_e:
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Composite score distribution</div>', unsafe_allow_html=True)
    scores = fdf["composite_score"].dropna()
    if not scores.empty:
        bins = pd.cut(scores, bins=[0, 20, 40, 60, 70, 80, 90, 100], include_lowest=True)
        hist = bins.value_counts().sort_index()
        hist.index = hist.index.astype(str)
        st.bar_chart(hist, height=220, color=PRIMARY)
    else:
        st.caption("No composite scores in the current filter.")
    st.markdown('</div>', unsafe_allow_html=True)

with col_f:
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Fraud probability distribution</div>', unsafe_allow_html=True)
    probs = fdf["fraud_probability"].dropna()
    if not probs.empty:
        bins = pd.cut(probs, bins=[0, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0], include_lowest=True)
        hist = bins.value_counts().sort_index()
        hist.index = hist.index.astype(str)
        st.bar_chart(hist, height=220, color="#c98a1e")
    else:
        st.caption("No fraud probabilities in the current filter.")
    st.markdown('</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Records table + drill-down
# ---------------------------------------------------------------------------
st.markdown('<div class="section-card">', unsafe_allow_html=True)
st.markdown(f'<div class="section-title">Records ({len(fdf):,})</div>', unsafe_allow_html=True)

display_cols = [c for c in [
    "unique_id", "created_at", "flow", "transfer_type", "demo_scenario",
    "primary_vendor", "decision", "composite_score", "rules_score", "fraud_probability",
] if c in fdf.columns]
table_df = fdf[display_cols].sort_values("created_at", ascending=False)


def _highlight_decision(row):
    bg, color = DECISION_COLORS.get(row["decision"], ("", ""))
    return [f"background-color: {bg}; color: {color};" if col == "decision" else "" for col in row.index]


st.dataframe(
    table_df.style.apply(_highlight_decision, axis=1),
    use_container_width=True,
    height=360,
)

st.download_button(
    "Download filtered records (CSV)",
    data=table_df.to_csv(index=False).encode("utf-8"),
    file_name=f"observability_export_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv",
    mime="text/csv",
)

st.write("")
st.caption("Drill into a single record")
selected_id = st.selectbox("Unique ID", table_df["unique_id"].tolist()) if not table_df.empty else None
if selected_id:
    record = fdf[fdf["unique_id"] == selected_id].iloc[0]
    bg, color = DECISION_COLORS.get(record["decision"], ("#f2f3f5", SUB))
    st.markdown(f"""
    <div style="background:{bg}; border:1px solid {color}44; border-radius:8px; padding:10px 14px; margin-bottom:10px;">
        <b style="color:{color};">{record['decision']}</b>
        &nbsp;\u2022&nbsp; Composite: <b>{record['composite_score']}</b>
        &nbsp;\u2022&nbsp; Rules: <b>{record['rules_score']}</b>
        &nbsp;\u2022&nbsp; Fraud prob: <b>{record['fraud_probability']:.1%}</b>
        &nbsp;\u2022&nbsp; Primary vendor: <b>{record['primary_vendor']}</b>
    </div>
    """, unsafe_allow_html=True)

    tabs = st.tabs(["Request", "Canonical", "Vendor Results", "Reason Codes"])
    json_cols = {
        "Request": "request_json",
        "Canonical": "canonical_json",
        "Vendor Results": "vendor_results_json",
        "Reason Codes": "reason_codes_json",
    }
    for tab, col in zip(tabs, json_cols.values()):
        with tab:
            raw = record.get(col)
            if raw:
                try:
                    st.json(json.loads(raw))
                except (TypeError, json.JSONDecodeError):
                    st.code(str(raw))
            else:
                st.caption("Not available for this record.")

st.markdown('</div>', unsafe_allow_html=True)