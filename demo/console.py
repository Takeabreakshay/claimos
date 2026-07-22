"""ClaimOS — LIVE claims-ops console (company perspective).

The real workflow, end to end:
  FNOL intake -> photo upload (live quality/blur/reuse) -> document upload
  (live Nemotron OCR) -> real model scoring -> triage routing -> officer
  decision -> settlement (real IRDAI grids) -> audit trail.

Run:
    poetry run streamlit run demo/console.py
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from src.live import nvidia, workflow as wf          # noqa: E402
from src.live.store import get_store                  # noqa: E402

st.set_page_config(page_title="ClaimOS — Claims Ops", page_icon="🚗", layout="wide")

LANE_UI = {
    "lane1_touchless":    ("Lane 1 · Touchless",     "#14532D", "#E4F5E9"),
    "lane2_assisted":     ("Lane 2 · Assisted",      "#7A5A00", "#FFF4D6"),
    "lane3_investigative":("Lane 3 · Investigative", "#7B1B12", "#FDE7E7"),
    "retake":             ("Evidence retake",        "#0B2A5B", "#E6EEFB"),
    "coverage_reject":    ("Coverage decline",       "#3F3F46", "#EEEEEF"),
}


def lane_chip(lane: str | None) -> str:
    label, fg, bg = LANE_UI.get(lane or "", ("Unscored", "#5A6A80", "#EEF2F8"))
    return (f"<span style='background:{bg};color:{fg};padding:4px 12px;border-radius:999px;"
            f"font-weight:700;font-size:13px'>{label}</span>")


def money(x) -> str:
    try:
        return f"₹{float(x):,.0f}"
    except Exception:
        return "—"


# --------------------------------------------------------------------------- #
# Sidebar — live system status
# --------------------------------------------------------------------------- #
store = get_store()

with st.sidebar:
    st.markdown("### ClaimOS")
    st.caption("Claims-ops console · live workflow")
    officer = st.text_input("Officer", value=st.session_state.get("officer", "officer.demo"))
    st.session_state["officer"] = officer

    st.markdown("---")
    st.markdown("**System status**")
    st.write(("🟢 Supabase" if store.mode == "supabase" else "🟡 Local store")
             + f" · `{store.mode}`")
    st.write(("🟢 Nemotron OCR" if os.getenv("NVIDIA_OCR_KEY") else "⚪ OCR: local only"))
    st.write(("🟢 Kimi K2 (narrative)" if os.getenv("NVIDIA_LLM_KEY") else "⚪ LLM: templates"))
    try:
        wf.models()
        st.write("🟢 Models loaded")
    except Exception as exc:
        st.error(f"Models missing — run `poetry run claimos-pipeline`\n\n{exc}")

    st.caption("VAHAN · DigiLocker · IIB PRISM/QUEST are mocked "
               "(regulator-gated, not obtainable).")

    if st.button("Test NVIDIA connectivity"):
        with st.spinner("calling NIM…"):
            h = nvidia.health()
        st.json(h)

    if store.mode == "local":
        st.info("Add SUPABASE_URL + SUPABASE_SERVICE_KEY to `.env` and run "
                "`supabase/schema.sql` to persist to Supabase.")

st.title("ClaimOS — Claims Operations")

tab_queue, tab_new, tab_evi, tab_dec, tab_dash = st.tabs(
    ["📋 Queue", "➕ New claim (FNOL)", "📸 Evidence & OCR", "⚖ Decision", "📊 Dashboard"])


# --------------------------------------------------------------------------- #
# QUEUE
# --------------------------------------------------------------------------- #
with tab_queue:
    claims = store.list_claims()
    if not claims:
        st.info("No claims yet. Open one in **New claim (FNOL)**.")
    else:
        rows = []
        for c in claims:
            sc = store.latest_score(c["claim_id"]) or {}
            rows.append({
                "claim_id": c["claim_id"],
                "type": c.get("claim_type"),
                "claimed": c.get("claim_amount"),
                "severity": c.get("incident_severity"),
                "status": c.get("status"),
                "lane": c.get("lane") or "—",
                "p_fraud": sc.get("p_fraud"),
                "confidence": sc.get("model_confidence"),
                "cost_p50": sc.get("cost_p50"),
                "created": (c.get("created_at") or "")[:19],
            })
        df = pd.DataFrame(rows)
        c1, c2 = st.columns([3, 1])
        with c2:
            lanes = ["(all)"] + sorted({r["lane"] for r in rows})
            pick = st.selectbox("Filter lane", lanes)
        view = df if pick == "(all)" else df[df["lane"] == pick]
        st.dataframe(view, use_container_width=True, hide_index=True)

        sel = st.selectbox("Open claim", [r["claim_id"] for r in rows])
        if st.button("Load into Evidence / Decision", type="primary"):
            st.session_state["claim_id"] = sel
            st.success(f"Loaded {sel} — go to Evidence or Decision tab.")


# --------------------------------------------------------------------------- #
# NEW CLAIM
# --------------------------------------------------------------------------- #
with tab_new:
    st.subheader("First Notice of Loss")
    with st.form("fnol"):
        a, b, c = st.columns(3)
        with a:
            policy_id = st.text_input("Policy number", "POL-2026-000141")
            customer_id = st.text_input("Customer id", "CUST-000141")
            claim_type = st.selectbox("Claim type", ["OD", "TP", "theft_total"])
            severity = st.selectbox("Severity (declared)",
                                    ["minor", "moderate", "severe"])
        with b:
            claim_amount = st.number_input("Claimed amount (₹)", 0, 5_000_000, 24000, 1000)
            idv = st.number_input("IDV (₹)", 10000, 5_000_000, 450000, 10000)
            vehicle_age = st.number_input("Vehicle age (years)", 0.0, 25.0, 3.0, 0.5)
            geo = st.selectbox("Geography", ["metro", "urban", "rural"])
        with c:
            garage_type = st.selectbox("Garage", ["network", "non_network"])
            garage_id = st.text_input("Garage id", "GAR-1042")
            surveyor_id = st.text_input("Surveyor id", "SUR-204")
            bank_account = st.text_input("Payout account", "AC-99881")

        st.markdown("**Intimation & eligibility**")
        d, e, f = st.columns(3)
        with d:
            delay = st.number_input("Intimation delay (hours)", 0.0, 2000.0, 6.0, 1.0)
            reason_valid = st.checkbox("Late reason legally valid", True)
            reason_text = st.text_input("Late reason", "")
        with e:
            lic = st.checkbox("Valid driving licence", True)
            dui = st.checkbox("DUI indicated", False)
            fir_filed = st.checkbox("FIR filed", False)
        with f:
            mod_actual = st.checkbox("Vehicle modified", False)
            mod_declared = st.checkbox("Modification declared", False)
            tp = st.checkbox("Third party involved", False)
            injury = st.checkbox("Injury reported", False)

        desc = st.text_area("Incident description", "Rear bumper damage in slow-speed collision.")
        submitted = st.form_submit_button("Open claim", type="primary")

    if submitted:
        cid = wf.create_claim({
            "policy_id": policy_id, "customer_id": customer_id, "claim_type": claim_type,
            "incident_severity": severity, "claim_amount": claim_amount, "idv": idv,
            "vehicle_age_years": vehicle_age, "geo": geo, "garage_type": garage_type,
            "garage_id": garage_id, "surveyor_id": surveyor_id, "bank_account": bank_account,
            "intimation_delay_hours": delay, "intimation_reason_valid": reason_valid,
            "intimation_reason_text": reason_text, "driver_valid_license": lic,
            "dui_flag": dui, "fir_filed": fir_filed, "modification_actual": mod_actual,
            "modification_declared": mod_declared, "third_party_involved": tp,
            "injury_hint": injury, "incident_description": desc,
            "incident_date": datetime.now(timezone.utc).isoformat(),
        }, actor=officer)
        st.session_state["claim_id"] = cid
        st.success(f"Claim **{cid}** opened. Go to **Evidence & OCR**.")


# --------------------------------------------------------------------------- #
# EVIDENCE & OCR
# --------------------------------------------------------------------------- #
with tab_evi:
    cid = st.session_state.get("claim_id")
    if not cid:
        st.info("Open or load a claim first.")
    else:
        claim = store.get_claim(cid) or {}
        st.subheader(f"Evidence · {cid}")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Photos", claim.get("num_photos", 0))
        m2.metric("Avg photo quality", f"{(claim.get('photo_quality_score') or 0):.2f}")
        m3.metric("Reuse flag", "YES" if claim.get("photo_reuse_flag") else "no")
        m4.metric("Status", claim.get("status", "—"))

        st.markdown("#### Damage photos")
        st.caption("Live on upload: sharpness/blur, exposure, resolution, EXIF time & GPS, "
                   "and a perceptual-hash check against every photo already in the book.")
        ups = st.file_uploader("Upload damage photos", type=["jpg", "jpeg", "png"],
                               accept_multiple_files=True, key="photoup")
        angle = st.selectbox("Angle label",
                             ["front-left", "front-right", "rear-left", "rear-right",
                              "number-plate", "odometer", "wide"], key="angle")
        if ups and st.button("Analyse & attach photos", type="primary"):
            for up in ups:
                r = wf.add_photo(cid, up.name, up.getvalue(), angle)
                cols = st.columns([1, 3])
                with cols[0]:
                    st.image(up.getvalue(), use_container_width=True)
                with cols[1]:
                    q = r["quality_score"]
                    st.write(f"**{up.name}** — quality **{q:.2f}** "
                             f"({'blurry ⚠' if r['is_blurry'] else 'sharp ✓'}), "
                             f"{r['width']}×{r['height']}")
                    st.progress(min(1.0, max(0.0, q)))
                    if r["reuse_verdict"] == "reused":
                        st.error(f"⚠ PHOTO REUSE — matches claim **{r['matched_claim']}** "
                                 f"(hash distance {r['reuse_distance']}). Fraud signal set.")
                    elif r["reuse_verdict"] == "similar":
                        st.warning(f"Near-duplicate of {r['matched_claim']} "
                                   f"(distance {r['reuse_distance']}) — flagged for review.")
                    else:
                        st.success("Unique image — no reuse detected.")
                    if r["analysis"].get("exif_timestamp"):
                        st.caption(f"EXIF: {r['analysis']['exif_timestamp']} · "
                                   f"GPS {r['analysis'].get('exif_lat')},"
                                   f"{r['analysis'].get('exif_lng')}")
            st.rerun()

        st.markdown("---")
        st.markdown("#### Documents — live OCR")
        st.caption("Nemotron OCR v2 via NVIDIA NIM, with a local engine fallback. "
                   "Extracted fields update the claim automatically.")
        doc_type = st.selectbox("Document type",
                                ["rc_copy", "driving_licence", "policy_copy", "fir",
                                 "repair_estimate", "final_bill", "bank_details", "other"])
        dup = st.file_uploader("Upload document", type=["jpg", "jpeg", "png", "pdf"],
                               key="docup")
        if dup and st.button("Run OCR", type="primary"):
            with st.spinner("Extracting…"):
                r = wf.add_document(cid, dup.name, dup.getvalue(), doc_type)
            st.write(f"Engine: `{r['engine']}` · confidence {r.get('ocr_confidence', 0):.2f}")
            if r.get("error"):
                st.caption(f"engine notes: {r['error']}")
            if r["ocr_fields"]:
                st.success("Extracted fields")
                st.json(r["ocr_fields"])
            if r.get("applied"):
                st.info(f"Applied to claim: {r['applied']}")
            with st.expander("Raw OCR text"):
                st.text(r["ocr_text"][:4000] or "(no text)")
            st.rerun()

        docs = store.list_child("claim_documents", cid)
        if docs:
            st.markdown("**Attached documents**")
            st.dataframe(pd.DataFrame([{
                "type": d.get("doc_type"), "engine_conf": d.get("ocr_confidence"),
                "fields": ", ".join((d.get("ocr_fields") or {}).keys()),
            } for d in docs]), use_container_width=True, hide_index=True)


# --------------------------------------------------------------------------- #
# DECISION
# --------------------------------------------------------------------------- #
with tab_dec:
    cid = st.session_state.get("claim_id")
    if not cid:
        st.info("Open or load a claim first.")
    else:
        claim = store.get_claim(cid) or {}
        st.subheader(f"Decision · {cid}")

        if st.button("▶ Score & route this claim", type="primary"):
            with st.spinner("Running cost / fraud / escalation models + triage…"):
                wf.score_and_route(cid, actor=officer)
            st.rerun()

        sc = store.latest_score(cid)
        if not sc:
            st.info("Not scored yet — click **Score & route**.")
        else:
            st.markdown(lane_chip(sc.get("lane")), unsafe_allow_html=True)
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Fraud probability", f"{(sc['p_fraud'] or 0):.1%}")
            k2.metric("Escalation risk", f"{(sc['p_escalation'] or 0):.1%}")
            k3.metric("Confidence", f"{(sc['model_confidence'] or 0):.2f}")
            k4.metric("Cost P50", money(sc["cost_p50"]))

            st.write(f"**Cost band** {money(sc['cost_p10'])} — {money(sc['cost_p90'])}  ·  "
                     f"**Claimed** {money(claim.get('claim_amount'))}")
            if claim.get("claim_amount") and sc.get("cost_p50"):
                ratio = float(claim["claim_amount"]) / max(float(sc["cost_p50"]), 1)
                if ratio > 1.25:
                    st.warning(f"Claimed is **{ratio:.2f}×** the predicted repair — "
                               "possible inflation.")

            if sc.get("ring_risk", 0) > 0.5:
                st.error(f"Collusion signal — ring risk {sc['ring_risk']:.2f}, "
                         f"component size {sc.get('component_size')}")
            if sc.get("legal_weak_reject_flag"):
                st.warning("⚖ Legal-check: late intimation with a valid reason — "
                           "must NOT be auto-rejected (SC rulings). Routed to a human.")

            st.markdown("**Why this decision**")
            for r in (sc.get("lane_reasons") or []):
                st.write(f"• {r}")
            st.caption(f"Coverage: {sc.get('coverage_clear')} · {sc.get('coverage_reason')}")

            if os.getenv("NVIDIA_LLM_KEY") and st.button("Draft officer note (Kimi K2)"):
                with st.spinner("Writing…"):
                    res = nvidia.claim_narrative({
                        "claim_id": cid, "lane": sc.get("lane"),
                        "reasons": sc.get("lane_reasons"),
                        "p_fraud": sc.get("p_fraud"), "confidence": sc.get("model_confidence"),
                        "cost_p50": sc.get("cost_p50"),
                        "claimed": claim.get("claim_amount"),
                        "coverage": sc.get("coverage_reason"),
                        "legal_check": sc.get("legal_weak_reject_flag"),
                    })
                if res.ok:
                    st.success(res.text)
                else:
                    st.error(f"NIM error: {res.error}")

            st.markdown("---")
            st.markdown("**Actions**")
            a1, a2, a3, a4 = st.columns(4)
            if a1.button("✅ Approve"):
                wf.record_decision(cid, "approve", officer); st.rerun()
            if a2.button("📄 Request evidence"):
                wf.record_decision(cid, "request_evidence", officer); st.rerun()
            if a3.button("🔍 Assign investigator"):
                wf.record_decision(cid, "assign_investigator", officer); st.rerun()
            if a4.button("💰 Settle"):
                s = wf.settle(cid, officer)
                st.success(f"Settled — net payable {money(s['net_payable'])} "
                           f"(UTR {s['utr_reference']})")
                st.json(s)

            with st.expander("Override lane (captured as a training label)"):
                new_lane = st.selectbox("Route to", list(LANE_UI.keys()))
                why = st.text_input("Override reason (required)")
                if st.button("Apply override") and why.strip():
                    wf.record_decision(cid, "override", officer,
                                       override_reason=why, to_lane=new_lane)
                    st.rerun()

            with st.expander("Audit trail"):
                for e in wf.timeline(cid):
                    st.write(f"`{(e.get('created_at') or '')[:19]}` **{e.get('event')}** "
                             f"— {e.get('actor')}")
                    if e.get("detail"):
                        st.caption(str(e["detail"])[:400])


# --------------------------------------------------------------------------- #
# DASHBOARD — live over the actual book
# --------------------------------------------------------------------------- #
with tab_dash:
    claims = store.list_claims(limit=5000)
    st.subheader("Portfolio")
    if not claims:
        st.info("No claims yet.")
    else:
        scored = [(c, store.latest_score(c["claim_id"])) for c in claims]
        lanes = [c.get("lane") for c, _ in scored if c.get("lane")]
        n = len(claims)
        touchless = lanes.count("lane1_touchless")

        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Claims", n)
        d2.metric("Touchless", f"{(touchless / n * 100 if n else 0):.1f}%")
        settled = [c for c, _ in scored if c.get("status") == "paid"]
        d3.metric("Settled", len(settled))
        flagged = [s for _, s in scored if s and (s.get("p_fraud") or 0) >= 0.5]
        d4.metric("Fraud-flagged", len(flagged))

        if lanes:
            mix = pd.Series(lanes).value_counts().rename_axis("lane").reset_index(name="claims")
            st.bar_chart(mix.set_index("lane"))

        st.markdown("**Live leakage watch** — fraud-flagged claims that landed in Lane 1")
        leak = [c["claim_id"] for c, s in scored
                if c.get("lane") == "lane1_touchless" and s and (s.get("p_fraud") or 0) >= 0.5]
        if leak:
            st.error(f"{len(leak)} claim(s) breach the touchless safety rule: {leak}")
        else:
            st.success("No fraud-flagged claim has been auto-settled. Ceiling holding.")
