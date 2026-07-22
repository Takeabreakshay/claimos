"""Phase 7 — interactive triage simulator (Streamlit) (CLAUDE.md §10 Phase 7).

Enter a claim -> see its lane, calibrated confidence, reason codes, cost band,
fraud/escalation probabilities, and the legal-check flag.

Run: ``poetry run streamlit run demo/app.py``  (needs models/ artifacts — run
``poetry run claimos-pipeline`` first).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.constants import SEED, Lane
from src.explain import Explainer
from src.pipeline import load_models, score_frame
from src.rails import enrich_claim
from src.triage import COVERAGE_REJECT, RETAKE, route_claim

st.set_page_config(page_title="ClaimOS — Triage Simulator", page_icon="🚗", layout="wide")

_LANE_COLOR = {
    Lane.TOUCHLESS.value: "#2f7d56",
    Lane.ASSISTED.value: "#c98a1a",
    Lane.INVESTIGATIVE.value: "#b04a3a",
    COVERAGE_REJECT: "#6b6b6b",
    RETAKE: "#3a6ea5",
}


@st.cache_resource
def _models():
    return load_models()


@st.cache_resource
def _explainer():
    return Explainer(load_models())


def _build_claim(inp: dict) -> dict:
    """Assemble a full raw-claim record from the form inputs (defaults filled)."""
    theft = inp["claim_type"] == "theft_total"
    severity = "total" if theft else inp["incident_severity"]
    fir_required = int(
        inp["claim_type"] in ("TP", "theft_total") or severity in ("severe", "total")
    )
    return {
        "claim_id": "DEMO-0000001",
        "customer_id": "DEMO-CUST",
        "garage_id": "DEMO-GAR",
        "surveyor_id": "DEMO-SUR",
        "claim_type": inp["claim_type"],
        "idv": inp["idv"],
        "claim_amount": inp["claim_amount"],
        "incident_severity": severity,
        "policy_status": inp["policy_status"],
        "garage_type": inp["garage_type"],
        "geo": inp["geo"],
        "driver_valid_license": int(inp["driver_valid_license"]),
        "dui_flag": int(inp["dui_flag"]),
        "modification_actual": int(inp["modification_undeclared"]),
        "modification_declared": 0,
        "modification_undeclared": int(inp["modification_undeclared"]),
        "non_network_garage": int(inp["garage_type"] == "non_network"),
        "intimation_delay_hours": 72.0 if inp["intimation_gt_48h"] else 5.0,
        "intimation_gt_48h": int(inp["intimation_gt_48h"]),
        "intimation_reason_valid": int(inp["intimation_reason_valid"]),
        "fir_required": fir_required,
        "fir_filed": int(inp["fir_filed"]),
        "num_photos": inp["num_photos"],
        "photo_quality_score": inp["photo_quality_score"],
        "photo_reuse_flag": int(inp["photo_reuse_flag"]),
        "tp_linkage": 0,
        "ambiguous_liability": 0,
        "injury_hint": int(inp["injury_hint"]),
        "is_fraud": 0,
        "fraud_type": "none",
        "is_ring_claim": 0,
        "ring_id": -1,
        "vehicle_age_years": inp["vehicle_age_years"],
    }


def main() -> None:
    st.title("🚗 ClaimOS — Risk-Triage Decision Layer")
    st.caption(
        "Enter a motor claim; ClaimOS scores it and routes it to Touchless / Assisted / "
        "Investigative. Synthetic data, mocked rails, calibrated confidence. (Bajaj ATOM S9)"
    )

    with st.sidebar:
        st.header("Claim inputs")
        claim_type = st.selectbox("Claim type", ["OD", "TP", "theft_total"])
        claim_amount = st.number_input("Claimed amount (₹)", 1000, 5_000_000, 22000, step=1000)
        idv = st.number_input("IDV (₹)", 50000, 5_000_000, 450000, step=10000)
        incident_severity = st.selectbox("Severity", ["minor", "moderate", "severe"])
        policy_status = st.selectbox("Policy status", ["active", "lapsed"])
        garage_type = st.selectbox("Garage", ["network", "non_network"])
        geo = st.selectbox("Geography", ["metro", "urban", "rural"])
        vehicle_age_years = st.slider("Vehicle age (yrs)", 0.0, 15.0, 3.0, 0.5)
        num_photos = st.slider("Photos submitted", 0, 8, 5)
        photo_quality_score = st.slider("Photo quality", 0.0, 1.0, 0.8, 0.05)
        col1, col2 = st.columns(2)
        with col1:
            driver_valid_license = st.checkbox("Valid licence", True)
            dui_flag = st.checkbox("DUI flag", False)
            injury_hint = st.checkbox("Injury (TP)", False)
            fir_filed = st.checkbox("FIR filed", True)
        with col2:
            intimation_gt_48h = st.checkbox("Late intimation (>48h)", False)
            intimation_reason_valid = st.checkbox("Late reason valid", True)
            modification_undeclared = st.checkbox("Undeclared mod", False)
            photo_reuse_flag = st.checkbox("Photo reuse", False)

    inp = dict(
        claim_type=claim_type,
        claim_amount=claim_amount,
        idv=idv,
        incident_severity=incident_severity,
        policy_status=policy_status,
        garage_type=garage_type,
        geo=geo,
        vehicle_age_years=vehicle_age_years,
        num_photos=num_photos,
        photo_quality_score=photo_quality_score,
        driver_valid_license=driver_valid_license,
        dui_flag=dui_flag,
        injury_hint=injury_hint,
        fir_filed=fir_filed,
        intimation_gt_48h=intimation_gt_48h,
        intimation_reason_valid=intimation_reason_valid,
        modification_undeclared=modification_undeclared,
        photo_reuse_flag=photo_reuse_flag,
    )
    claim = _build_claim(inp)
    df1 = pd.DataFrame([claim])

    try:
        models = _models()
    except Exception:
        st.error("No trained models found. Run `poetry run claimos-pipeline` first.")
        return

    scored = score_frame(df1, models=models).iloc[0].to_dict()
    decision = route_claim({**claim, **scored})

    color = _LANE_COLOR.get(decision.outcome, "#333")
    st.markdown(
        f"<h2 style='color:{color}'>➡ {decision.outcome.replace('_', ' ').title()}</h2>",
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Fraud prob", f"{scored['p_fraud']:.1%}")
    c2.metric("Escalation prob", f"{scored['p_escalation']:.1%}")
    c3.metric("Confidence", f"{scored['model_confidence']:.2f}")
    c4.metric("Cost P50", f"₹{scored['cost_p50']:,.0f}")

    st.write(f"**Cost band:** ₹{scored['cost_p10']:,.0f} — ₹{scored['cost_p90']:,.0f} (P10–P90)")
    if decision.legal_check:
        st.warning(
            "⚖ Legal-check flag: late intimation with a valid reason — routed to a human, "
            "NOT auto-rejected (SC rulings)."
        )

    st.subheader("Why this decision")
    for r in decision.reasons:
        st.write(f"• {r}")

    with st.expander("Model explanation (SHAP top drivers)"):
        try:
            expl = _explainer()
            e = expl.explain_claim(claim, scored, rails_row=enrich_claim(claim))
            st.write("**Plain reason:**", e.plain_reason)
            st.write("**Fraud drivers:**", e.fraud_drivers)
            st.write("**Cost drivers:**", e.cost_drivers)
            st.write("**Escalation drivers:**", e.escalation_drivers)
        except Exception as exc:
            st.info(f"SHAP explanation unavailable: {exc}")

    st.caption(f"Deterministic (SEED={SEED}). Rails mocked; zero external API keys.")


main()
