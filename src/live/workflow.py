"""LIVE claim workflow — the company-perspective state machine.

  intake -> evidence -> verifying -> scored -> (retake | lane1/2/3 | coverage_reject)
                                            -> decision -> settled

Every step persists to the store and writes an audit event. The scoring step
calls the REAL trained models + triage policy, with live evidence signals
(photo quality, photo reuse, OCR-extracted amounts) folded in.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from src import constants, rate_card
from src.constants import Lane
from src.live import vision
from src.live.ocr import run_ocr
from src.live.store import get_store, new_claim_id
from src.models import coverage as cov
from src.pipeline import load_models, score_frame
from src.rails import enrich_claim
from src.triage import RETAKE, RouteDecision, route_claim

# Lane strictness order for the "escalate-only" ladder — a signal may make a
# routing decision STRICTER, never looser (leakage-first, CLAUDE.md §3 rule 8).
_LANE_ORDER = {
    RETAKE: 0, Lane.TOUCHLESS.value: 1, Lane.ASSISTED.value: 2,
    Lane.INVESTIGATIVE.value: 3, "coverage_reject": 4,
}

# Model feature defaults for fields a real FNOL doesn't collect directly.
_DEFAULTS = {
    "tp_linkage": 0, "ambiguous_liability": 0,
    "is_fraud": 0, "fraud_type": "none", "is_ring_claim": 0, "ring_id": -1,
    "component_size": 1, "shared_garage_count": 0, "shared_surveyor_count": 0,
    "shared_bank_count": 0, "ring_risk": 0.0,
}

_models = None


def models():
    global _models
    if _models is None:
        _models = load_models()
    return _models


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# 1. INTAKE
# --------------------------------------------------------------------------- #
def create_claim(intake: dict[str, Any], actor: str = "OFFICER") -> str:
    """Open a claim from FNOL data. Returns claim_id."""
    st = get_store()
    cid = new_claim_id()

    # Resolve the policy master first. In production this is a read-through to the
    # core policy DB (mocked rail); here we upsert what FNOL knows so the claim's
    # foreign key resolves and the policy is queryable alongside the claim.
    policy_id = intake.get("policy_id")
    if policy_id:
        try:
            # PRODUCTION: replace with a read from the core policy DB rail.
            st.upsert("policies", {
                "policy_id": policy_id,
                "customer_id": intake.get("customer_id") or "UNKNOWN",
                "owner_geo": intake.get("geo"),
                "idv": float(intake.get("idv") or 0),
                "policy_status": intake.get("policy_status", "active"),
            }, on_conflict="policy_id")
        except Exception as exc:  # never block intake on the policy master
            st.event(cid, "policy_upsert_failed", {"policy_id": policy_id,
                                                   "error": str(exc)[:200]}, actor)

    severity = "total" if intake.get("claim_type") == "theft_total" else intake.get(
        "incident_severity", "minor")
    fir_required = bool(
        intake.get("claim_type") in ("TP", "theft_total")
        or severity in ("severe", "total"))

    delay = float(intake.get("intimation_delay_hours", 0) or 0)
    row = {
        "claim_id": cid,
        "policy_id": intake.get("policy_id"),
        "customer_id": intake.get("customer_id"),
        "claim_type": intake.get("claim_type", "OD"),
        "incident_date": intake.get("incident_date"),
        "fnol_timestamp": _now(),
        "intimation_delay_hours": delay,
        "intimation_gt_48h": delay > 48,
        "intimation_reason_valid": bool(intake.get("intimation_reason_valid", True)),
        "intimation_reason_text": intake.get("intimation_reason_text"),
        "incident_description": intake.get("incident_description"),
        "incident_lat": intake.get("incident_lat"),
        "incident_lng": intake.get("incident_lng"),
        "geo": intake.get("geo", "urban"),
        "incident_severity": severity,
        "claim_amount": float(intake.get("claim_amount") or 0),
        "idv": float(intake.get("idv") or 0),
        "vehicle_age_years": float(intake.get("vehicle_age_years") or 0),
        "garage_id": intake.get("garage_id"),
        "garage_type": intake.get("garage_type", "network"),
        "surveyor_id": intake.get("surveyor_id"),
        "bank_account": intake.get("bank_account"),
        "driver_valid_license": bool(intake.get("driver_valid_license", True)),
        "dui_flag": bool(intake.get("dui_flag", False)),
        "modification_actual": bool(intake.get("modification_actual", False)),
        "modification_declared": bool(intake.get("modification_declared", False)),
        "fir_required": fir_required,
        "fir_filed": bool(intake.get("fir_filed", False)),
        "third_party_involved": bool(intake.get("third_party_involved", False)),
        "injury_hint": bool(intake.get("injury_hint", False)),
        # --- coverage matrix inputs (LOGIC §2) ---
        "policy_status": intake.get("policy_status", "active"),
        "product_type": intake.get("product_type", "comprehensive"),
        "period_from": intake.get("period_from"),
        "period_to": intake.get("period_to"),
        "cubic_capacity": float(intake.get("cubic_capacity") or 0),
        "vehicle_type": intake.get("vehicle_type", "private_car"),
        "voluntary_excess": float(intake.get("voluntary_excess") or 0),
        "add_ons": intake.get("add_ons") or intake.get("addons") or [],
        "usage_class": intake.get("usage_class", "private"),
        "claim_free_years": int(intake.get("claim_free_years") or 0),
        "od_premium_next_year": float(intake.get("od_premium_next_year") or 0),
        "claims_this_year": int(intake.get("claims_this_year") or 0),
        "invoice_value": float(intake.get("invoice_value") or 0),
        "engine_damage": bool(intake.get("engine_damage", False)),
        # --- rate-card inputs (LOGIC §1) ---
        "make": intake.get("make"),
        "model": intake.get("model"),
        "segment": intake.get("segment"),
        "city_tier": intake.get("city_tier", "metro"),
        "is_ev": bool(intake.get("is_ev", False)),
        "is_import": bool(intake.get("is_import", False)),
        "num_photos": 0,
        "status": "intake",
        "created_at": _now(),
    }
    st.insert("claims", row)
    st.event(cid, "claim_opened", {"policy_id": row["policy_id"],
                                   "claim_type": row["claim_type"]}, actor)
    return cid


# --------------------------------------------------------------------------- #
# 2. EVIDENCE — photos (live vision) and documents (live OCR)
# --------------------------------------------------------------------------- #
def add_photo(claim_id: str, filename: str, data: bytes,
              angle_label: str | None = None) -> dict[str, Any]:
    """Analyse + store a damage photo. Runs REAL quality + reuse detection."""
    st = get_store()
    an = vision.analyse_photo(data)

    known = st.known_phashes(exclude_claim=claim_id)
    verdict, dist, matched = ("unique", 999, None)
    if an.phash and known:
        verdict, dist, matched = vision.match_photo(an.phash, [k for k, _ in known])
    matched_claim = next((c for k, c in known if k == matched), None) if matched else None

    # AI DAMAGE ASSESSMENT (PS deliverable #3). Read severity + parts straight
    # from the pixels with the vision model. Only attempt it on a usable photo —
    # assessing a blurred frame is worse than admitting we can't. Degrades to {}
    # (severity stays operator-declared) if no vision key / model is available.
    damage: dict[str, Any] = {}
    damage_err: str | None = None
    if not an.is_blurry and an.quality_score >= 0.45:
        try:
            from src.live import nvidia

            damage = nvidia.severity_from_photo(data) or {}
            if not damage:
                damage_err = "vision returned empty"
        except Exception as exc:
            damage_err = f"{type(exc).__name__}: {exc}"
            damage = {}
    else:
        damage_err = f"skipped (blurry={an.is_blurry}, quality={an.quality_score})"

    path = st.upload(claim_id, filename, data, "image/jpeg")
    row = {
        "claim_id": claim_id, "storage_path": path, "angle_label": angle_label,
        "phash": an.phash, "quality_score": an.quality_score,
        "blur_variance": an.blur_variance, "is_blurry": an.is_blurry,
        "exif_timestamp": an.exif_timestamp, "exif_lat": an.exif_lat,
        "exif_lng": an.exif_lng, "width": an.width, "height": an.height,
        "cv_severity": damage.get("severity"),
        "cv_parts": damage.get("damaged_parts"),
        "cv_confidence": damage.get("confidence"),
        "created_at": _now(),
    }
    st.insert("claim_photos", row)

    photos = st.list_child("claim_photos", claim_id)
    qualities = [p.get("quality_score") or 0 for p in photos]
    reuse_hard = verdict == "reused"
    patch = {
        "num_photos": len(photos),
        "photo_quality_score": round(sum(qualities) / len(qualities), 3) if qualities else 0,
        "status": "evidence",
    }
    if reuse_hard:
        patch["photo_reuse_flag"] = True

    # Aggregate CV severity to the claim = the worst severity any photo shows
    # (a total-loss corner still totals the car). Declared severity is kept; the
    # assessed value is stored alongside so a mismatch is visible, not silent.
    _SEV_RANK = {"minor": 0, "moderate": 1, "severe": 2, "total": 3}
    cv_sevs = [p.get("cv_severity") for p in photos if p.get("cv_severity") in _SEV_RANK]
    if cv_sevs:
        worst = max(cv_sevs, key=lambda s: _SEV_RANK[s])
        patch["cv_severity"] = worst
        declared = str(claim.get("incident_severity") if (claim := st.get_claim(claim_id)) else "")
        patch["cv_severity_mismatch"] = bool(declared and declared != worst)
    # Union of damaged parts across photos + the min vision confidence — the
    # scorer turns these into a line-item estimate (LOGIC §1) and applies the
    # value-tiered confidence floor (LOGIC §5).
    parts_union: list[str] = []
    for p in photos:
        for part in (p.get("cv_parts") or []):
            if part not in parts_union:
                parts_union.append(part)
    if parts_union:
        patch["cv_parts_all"] = parts_union
    cv_confs = [float(p["cv_confidence"]) for p in photos if p.get("cv_confidence") is not None]
    if cv_confs:
        patch["cv_confidence"] = round(min(cv_confs), 3)
    st.update_claim(claim_id, patch)

    st.event(claim_id, "photo_added", {
        "quality": an.quality_score, "blurry": an.is_blurry,
        "reuse_verdict": verdict, "reuse_distance": dist,
        "matched_claim": matched_claim,
        "cv_severity": damage.get("severity"), "cv_parts": damage.get("damaged_parts")})

    return {**row, "reuse_verdict": verdict, "reuse_distance": dist,
            "matched_claim": matched_claim, "analysis": an.to_dict(),
            "damage": damage, "damage_err": damage_err}


def add_document(claim_id: str, filename: str, data: bytes,
                 doc_type: str = "other") -> dict[str, Any]:
    """Store + OCR a document. Extracted fields update the claim live."""
    st = get_store()
    res = run_ocr(data, doc_type)
    path = st.upload(claim_id, filename, data, "application/octet-stream")

    row = {
        "claim_id": claim_id, "doc_type": doc_type, "storage_path": path,
        "ocr_text": res.text[:20000], "ocr_fields": res.fields,
        "ocr_confidence": res.confidence, "created_at": _now(),
    }
    st.insert("claim_documents", row)

    # Live field application: a repair estimate sets the claimed amount; an FIR
    # number satisfies the coverage rule.
    patch: dict[str, Any] = {}
    f = res.fields or {}
    if doc_type in ("repair_estimate", "final_bill") and f.get("amount"):
        patch["claim_amount"] = float(f["amount"])
    if doc_type == "fir" and f.get("fir_number"):
        patch["fir_filed"] = True
        patch["fir_number"] = f["fir_number"]
    if patch:
        st.update_claim(claim_id, patch)

    st.event(claim_id, "document_ocr", {"doc_type": doc_type, "engine": res.engine,
                                        "fields": f, "applied": patch,
                                        "error": res.error})
    return {**row, "engine": res.engine, "error": res.error, "applied": patch}


# --------------------------------------------------------------------------- #
# 3. SCORE + ROUTE  (real models + real triage policy)
# --------------------------------------------------------------------------- #
def _to_model_row(claim: dict[str, Any]) -> dict[str, Any]:
    sev = claim.get("incident_severity") or "minor"
    row = {
        "claim_id": claim["claim_id"],
        "customer_id": claim.get("customer_id") or "CUST",
        "garage_id": claim.get("garage_id") or f"GAR-{claim['claim_id']}",
        "surveyor_id": claim.get("surveyor_id") or f"SUR-{claim['claim_id']}",
        # graph link entities — must exist as columns for the graph feature builder
        "bank_account": claim.get("bank_account") or f"AC-{claim['claim_id']}",
        "phone": claim.get("phone") or f"PH-{claim['claim_id']}",
        "claim_type": claim.get("claim_type") or "OD",
        "idv": float(claim.get("idv") or 450000),
        "claim_amount": float(claim.get("claim_amount") or 0),
        "incident_severity": sev,
        "policy_status": claim.get("policy_status") or "active",
        "garage_type": claim.get("garage_type") or "network",
        "geo": claim.get("geo") or "urban",
        "driver_valid_license": int(bool(claim.get("driver_valid_license", True))),
        "dui_flag": int(bool(claim.get("dui_flag"))),
        "modification_actual": int(bool(claim.get("modification_actual"))),
        "modification_declared": int(bool(claim.get("modification_declared"))),
        "modification_undeclared": int(bool(claim.get("modification_actual"))
                                       and not bool(claim.get("modification_declared"))),
        "non_network_garage": int(claim.get("garage_type") == "non_network"),
        "intimation_delay_hours": float(claim.get("intimation_delay_hours") or 0),
        "intimation_gt_48h": int(bool(claim.get("intimation_gt_48h"))),
        "intimation_reason_valid": int(bool(claim.get("intimation_reason_valid", True))),
        "fir_required": int(bool(claim.get("fir_required"))),
        "fir_filed": int(bool(claim.get("fir_filed"))),
        "num_photos": int(claim.get("num_photos") or 0),
        "photo_quality_score": float(claim.get("photo_quality_score") or 0.5),
        "photo_reuse_flag": int(bool(claim.get("photo_reuse_flag"))),
        "injury_hint": int(bool(claim.get("injury_hint"))),
        "vehicle_age_years": float(claim.get("vehicle_age_years") or 0),
    }
    row.update({k: v for k, v in _DEFAULTS.items() if k not in row})
    return row


def score_and_route(claim_id: str, actor: str = "SYSTEM") -> dict[str, Any]:
    """Run the real models + wedge on the live claim, persist, return decision."""
    st = get_store()
    claim = st.get_claim(claim_id)
    if not claim:
        raise ValueError(f"unknown claim {claim_id}")

    mrow = _to_model_row(claim)

    # Live cross-claim graph signal from the actual book.
    links = _entity_links(st, claim)
    mrow.update(links)

    df = pd.DataFrame([mrow])
    # Collusion is a property of the whole book, so hand the scorer the real
    # cross-claim graph features rather than letting it recompute on one row.
    graph_df = pd.DataFrame([{
        "component_size": links["component_size"],
        "shared_garage_count": links["shared_garage_count"],
        "shared_surveyor_count": links["shared_surveyor_count"],
        "shared_bank_count": links["shared_bank_count"],
        "ring_risk": links["ring_risk"],
    }], index=df.index)
    scored = score_frame(df, models=models(), graph_df=graph_df).iloc[0].to_dict()

    # Evidence-gap: a genuinely unusable photo set forces the retake loop.
    if mrow["num_photos"] == 0 or mrow["photo_quality_score"] < 0.35:
        scored["model_confidence"] = min(float(scored["model_confidence"]), 0.30)

    # ------------------------------------------------------------------ #
    # LINE-ITEM REPAIR ESTIMATE (LOGIC §1) — deterministic rate-card cost
    # from the vision-detected parts. Stacks with the GBT: it is a strong
    # cost prior, and the divergence claim-vs-estimate is a padding signal.
    # ------------------------------------------------------------------ #
    parts = claim.get("cv_parts_all") or claim.get("cv_parts") or []
    seg = rate_card.segment_for(claim.get("make"), claim.get("model"),
                                claim.get("segment"))
    li = rate_card.estimate(
        parts, segment=seg, garage_type=mrow["garage_type"],
        city_tier=str(claim.get("city_tier") or "metro"),
        vehicle_age_years=mrow["vehicle_age_years"],
        is_ev=bool(claim.get("is_ev")), is_import=bool(claim.get("is_import")),
    ) if parts else None
    recon = (rate_card.reconciliation_flag(
        mrow["claim_amount"], li["line_item_estimate"], mrow["garage_type"])
        if li and li["n_parts"] else None)

    # ------------------------------------------------------------------ #
    # DAMAGE-MISMATCH HARD RULES (LOGIC §4) — vision severity vs declared.
    # May bump p_fraud BEFORE routing so the fraud gate sees it.
    # ------------------------------------------------------------------ #
    mm = _damage_mismatch(claim)
    if mm.get("fraud_bump"):
        scored["p_fraud"] = min(1.0, float(scored["p_fraud"]) + mm["fraud_bump"])

    # ------------------------------------------------------------------ #
    # COVERAGE MATRIX v2 (LOGIC §2) is authoritative in the live path — it is
    # richer than the batch coverage (in-force-on-incident-date, cover-type,
    # usage class, engine-peril, 4 states). Map its verdict onto the routing
    # inputs so route_claim produces the right base outcome, then the batch
    # coverage_clear is not allowed to independently reject.
    # ------------------------------------------------------------------ #
    cstate = cov.coverage_state(claim)
    _cov_route = {
        cov.STATE_CLEAR: ("clear", False),
        cov.STATE_FLAG: ("flag", False),          # blocks Lane 1, defaults to Lane 2
        cov.STATE_LEGAL_WEAK: ("clear", True),    # legal-weak override -> human
        cov.STATE_HARD_DECLINE: ("not_clear", False),
    }[cstate["state"]]
    rec = {**mrow, **scored,
           "coverage_clear": _cov_route[0],
           "coverage_reason": cstate["reason"],
           "legal_weak_reject_flag": int(_cov_route[1])}

    decision = route_claim(rec)

    # ------------------------------------------------------------------ #
    # ESCALATE-ONLY LADDER — every hard signal can make routing stricter,
    # never looser. Collect (min_lane, reason) and apply the strictest.
    # ------------------------------------------------------------------ #
    escalations: list[tuple[str, str]] = []
    if li and li.get("escalate_min_lane"):
        why = "airbag deployed" if li["has_airbag"] else "structural damage"
        escalations.append((li["escalate_min_lane"], f"{why} -> never touchless"))
    if li and li.get("total_loss_trigger"):
        escalations.append((Lane.INVESTIGATIVE.value,
                            "total-loss trigger (engine/gearbox rebuild)"))
    if recon and recon.get("min_lane"):
        tag = "inflation" if recon.get("inflation_flag") else "over-estimate"
        escalations.append((recon["min_lane"],
                            f"claim {recon['ratio']}x line-item estimate ({tag})"))
    if recon and recon.get("non_network_tell"):
        escalations.append((Lane.ASSISTED.value,
                            "non-network garage with above-estimate claim"))
    for min_lane, why in mm.get("escalations", []):
        escalations.append((min_lane, why))
    # Vision confidence floor (LOGIC §5) — an untrusted damage read can't be
    # the basis of a touchless settlement.
    vf = _vision_floor_block(claim, mrow)
    if vf:
        escalations.append((Lane.ASSISTED.value, vf))

    # Coverage-state reason, made readable in the reason chain (routing already
    # reflects it via the mapped inputs above — a hard decline can't be loosened).
    if cstate["state"] != cov.STATE_CLEAR and decision.outcome != "coverage_reject":
        tag = {cov.STATE_FLAG: "coverage_flag", cov.STATE_LEGAL_WEAK: "legal_weak",
               cov.STATE_HARD_DECLINE: "coverage_hard_decline"}[cstate["state"]]
        escalations.append((None, f"{tag}:{cstate['reason']}"))

    decision = _apply_escalations(decision, escalations)

    settlement = _settlement_preview(claim, mrow, li)

    # DUPLICATE / RE-FILING DETECTION (LOGIC §3, four-tier).
    dup = check_duplicate(st, claim)
    if dup["is_duplicate"]:
        decision = _apply_escalations(
            decision, [(dup["min_lane"], f"duplicate_claim:{dup['basis']}")])
        st.event(claim_id, "duplicate_detected", dup, actor)

    srow = {
        "claim_id": claim_id,
        "p_fraud": float(scored["p_fraud"]),
        "p_escalation": float(scored["p_escalation"]),
        "model_confidence": float(scored["model_confidence"]),
        "c_fraud": float(scored["c_fraud"]),
        "c_escalation": float(scored["c_escalation"]),
        "c_cost": float(scored["c_cost"]),
        "cost_p10": float(scored["cost_p10"]),
        "cost_p50": float(scored["cost_p50"]),
        "cost_p90": float(scored["cost_p90"]),
        "ring_risk": float(scored["ring_risk"]),
        "component_size": int(links.get("component_size", 1)),
        "coverage_clear": scored["coverage_clear"],
        "coverage_reason": scored["coverage_reason"],
        "coverage_state": cstate["state"],
        "coverage_state_reasons": cstate["reasons"],
        "legal_weak_reject_flag": bool(scored["legal_weak_reject_flag"]) or (
            cstate["state"] == cov.STATE_LEGAL_WEAK),
        "line_item_estimate": (li or {}).get("line_item_estimate"),
        "line_item_p10": (li or {}).get("cost_p10"),
        "line_item_p90": (li or {}).get("cost_p90"),
        "line_items": (li or {}).get("line_items"),
        "reconciliation_ratio": (recon or {}).get("ratio"),
        "inflation_flag": bool((recon or {}).get("inflation_flag")),
        "has_structural": bool((li or {}).get("has_structural")),
        "has_airbag": bool((li or {}).get("has_airbag")),
        "damage_mismatch": mm.get("summary"),
        "settlement": settlement,
        "lane": decision.outcome,
        "lane_reasons": decision.reasons,
        "scored_at": _now(),
    }
    st.insert("claim_scores", srow)

    status = {
        "lane1_touchless": "approved",
        "lane2_assisted": "awaiting_officer",
        "lane3_investigative": "investigating",
        "retake": "retake",
        "coverage_reject": "declined",
    }.get(decision.outcome, "scored")
    st.update_claim(claim_id, {"lane": decision.outcome, "status": status})
    st.event(claim_id, "scored", {"lane": decision.outcome,
                                  "reasons": decision.reasons,
                                  "p_fraud": srow["p_fraud"]}, actor)

    return {"score": srow, "decision": decision, "claim": st.get_claim(claim_id)}


# --------------------------------------------------------------------------- #
# Escalate-only lane ladder + hard-rule helpers (LOGIC §1/§4/§5)
# --------------------------------------------------------------------------- #
def _apply_escalations(decision: "RouteDecision",
                       escalations: list[tuple[str, str]]) -> "RouteDecision":
    """Bump the decision to the strictest requested lane; never loosen it."""
    outcome, reasons = decision.outcome, list(decision.reasons)
    for min_lane, why in escalations:
        if min_lane and _LANE_ORDER.get(min_lane, 0) > _LANE_ORDER.get(outcome, 0):
            outcome = min_lane
        if why and why not in reasons:
            reasons.insert(0, why)
    if outcome == decision.outcome and reasons == decision.reasons:
        return decision
    return RouteDecision(outcome, reasons, legal_check=decision.legal_check)


def _damage_mismatch(claim: dict[str, Any]) -> dict[str, Any]:
    """Vision-vs-declared severity delta → hard routing rules (LOGIC §4)."""
    thr = constants.load_thresholds().get("damage_mismatch", {})
    rank = thr.get("severity_rank", {"minor": 0, "moderate": 1, "severe": 2, "total": 3})
    tol_conf = float(thr.get("silent_tolerance_confidence", 0.75))
    bump = float(thr.get("fraud_bump_on_over_declared", 0.15))

    dv = rank.get(claim.get("cv_severity"))
    dd = rank.get(claim.get("incident_severity"))
    if dv is None or dd is None:
        return {"escalations": [], "fraud_bump": 0.0, "summary": None}

    delta = dv - dd
    conf = float(claim.get("cv_confidence") or 0)
    escalations: list[tuple[str, str]] = []
    fraud_bump = 0.0
    if delta >= 2:
        escalations.append((Lane.INVESTIGATIVE.value,
                            f"damage far worse than declared (vision {claim['cv_severity']} vs {claim['incident_severity']})"))
    elif delta == 1:
        # one rank silent only if the model is genuinely unsure (low conf)
        if conf >= tol_conf:
            escalations.append((Lane.ASSISTED.value,
                                f"damage worse than declared (vision {claim['cv_severity']})"))
    elif delta == -1:
        escalations.append((Lane.ASSISTED.value,
                            f"declared worse than visible damage — possible inflation"))
    elif delta <= -2:
        fraud_bump = bump
        escalations.append((Lane.INVESTIGATIVE.value,
                            f"declared far worse than visible (vision {claim['cv_severity']} vs {claim['incident_severity']})"))
    summary = None
    if delta != 0:
        summary = {"declared": claim.get("incident_severity"),
                   "assessed": claim.get("cv_severity"), "delta": delta,
                   "confidence": conf}
    return {"escalations": escalations, "fraud_bump": fraud_bump, "summary": summary}


def _vision_floor_block(claim: dict[str, Any], mrow: dict[str, Any]) -> str | None:
    """Value-tiered vision-confidence floor (LOGIC §5). Returns a reason if the
    damage read is too weak to support Touchless, else None."""
    conf = claim.get("cv_confidence")
    if conf is None:
        return None
    thr = constants.load_thresholds()
    floors = thr.get("vision_confidence_floor", [])
    amt = float(mrow.get("claim_amount") or 0)
    floor = None
    for band in floors:
        if amt <= band["max_value"]:
            floor = float(band["floor"])
            break
    if floor is None:
        return None  # >50k never touchless anyway; other rules govern
    if (mrow.get("garage_type") or "").lower() == "non_network":
        floor += float(thr.get("non_network_confidence_penalty", 0.0))
    if float(conf) < floor:
        return f"vision confidence {float(conf):.2f} < floor {floor:.2f} for Rs {int(amt):,}"
    return None


def _settlement_preview(claim: dict[str, Any], mrow: dict[str, Any],
                        li: dict[str, Any] | None) -> dict[str, Any] | None:
    """Run the settlement waterfall + advise-withdraw for a live preview (LOGIC §2)."""
    est = (li or {}).get("line_item_estimate") or mrow.get("claim_amount")
    if not est:
        return None
    ded = cov.total_deductible(
        cubic_capacity=float(claim.get("cubic_capacity") or 0),
        vehicle_type=str(claim.get("vehicle_type") or "private_car"),
        voluntary_excess=float(claim.get("voluntary_excess") or 0),
    )
    w = cov.settlement_waterfall(
        claimed=float(mrow.get("claim_amount") or 0),
        line_item_estimate=float(est),
        idv=float(mrow.get("idv") or 0),
        vehicle_age_years=float(mrow.get("vehicle_age_years") or 0),
        parts_depreciation=float((li or {}).get("parts_depreciation") or 0),
        consumables=float((li or {}).get("consumables") or 0),
        deductible_total=ded["total"],
        zero_dep_active=cov.addon_active(claim, "zero_depreciation"),
        consumables_covered=cov.addon_active(claim, "consumables"),
        rti_active=cov.addon_active(claim, "return_to_invoice"),
        invoice_value=float(claim.get("invoice_value") or 0) or None,
    )
    aw = cov.advise_withdraw(
        net_payable=float(w["net_payable"]),
        od_premium_next_year=float(claim.get("od_premium_next_year") or 0),
        claim_free_years=int(claim.get("claim_free_years") or 0),
        ncb_protect_active=cov.addon_active(claim, "ncb_protect"),
        claims_this_year=int(claim.get("claims_this_year") or 0),
    )
    return {**w, "deductible": ded, "advise_withdraw": aw}


def check_duplicate(st, claim: dict[str, Any]) -> dict[str, Any]:
    """Four-tier duplicate / re-filing detection (LOGIC §3).

    T1 exact  -> Lane 3 (auto-block/merge)  · T2 near  -> Lane 3
    T3 re-file after rejection -> Lane 2     · T4 same-part repeat -> Lane 2
    Plus frequency-anomaly and near-inception/expiry backdating flags.
    Returns {is_duplicate, tier, matches, basis, min_lane}.
    """
    from datetime import datetime

    thr = constants.load_thresholds().get("duplicate_detection", {})
    jac_min = float(thr.get("parts_overlap_jaccard", 0.60))
    t2_win = float(thr.get("t2_window_days", 7))
    t2_tol = float(thr.get("t2_amount_tolerance", 0.25))
    t3_win = float(thr.get("t3_window_days", 90))
    t4_win = float(thr.get("t4_window_days", 180))
    freq12 = int(thr.get("frequency_12m_flag", 3))
    freq30 = int(thr.get("frequency_30d_lane3", 2))

    cid = claim["claim_id"]
    amt = float(claim.get("claim_amount") or 0)
    parts = set(_norm_parts(claim.get("cv_parts_all") or claim.get("cv_parts") or []))

    def _parse(x):
        try:
            dt = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
        except Exception:
            return None

    def _jaccard(a: set, b: set) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    inc_dt = _parse(claim.get("incident_date"))
    recent_30 = recent_12m = 0
    tier = None
    matches: list[str] = []
    basis = ""

    for other in st.list_claims(limit=5000):
        if other.get("claim_id") == cid:
            continue
        same_party = (
            (claim.get("policy_id") and other.get("policy_id") == claim.get("policy_id"))
            or (claim.get("customer_id") and other.get("customer_id") == claim.get("customer_id"))
        )
        if not same_party:
            continue

        o_dt = _parse(other.get("incident_date"))
        day_gap = abs((inc_dt - o_dt).days) if (inc_dt and o_dt) else None
        if day_gap is not None and day_gap <= 30:
            recent_30 += 1
        if day_gap is not None and day_gap <= 365:
            recent_12m += 1

        o_amt = float(other.get("claim_amount") or 0)
        o_parts = set(_norm_parts(other.get("cv_parts_all") or other.get("cv_parts") or []))
        overlap = _jaccard(parts, o_parts)
        same_incident_date = (inc_dt and o_dt and inc_dt.date() == o_dt.date())
        amt_close_t2 = amt > 0 and abs(o_amt - amt) <= t2_tol * amt
        o_status = str(other.get("status") or "")

        # T1 exact duplicate: same incident date + same parts set.
        if same_incident_date and parts and parts == o_parts:
            tier, basis = "T1", "exact duplicate: same incident date and damaged parts"
            matches.append(other["claim_id"]); break
        # T2 near duplicate: within 7d + parts overlap >= 0.60 + amount within 25%.
        if (day_gap is not None and day_gap <= t2_win and overlap >= jac_min and amt_close_t2):
            tier, basis = "T2", f"near-duplicate: {int(overlap*100)}% parts overlap within {int(day_gap)}d"
            matches.append(other["claim_id"])
        # T3 re-filing after rejection/withdrawal.
        elif (o_status in ("declined", "rejected", "withdrawn")
              and day_gap is not None and day_gap <= t3_win and same_incident_date):
            if tier not in ("T1", "T2"):
                tier, basis = "T3", "re-filing of a previously rejected/withdrawn claim"
            matches.append(other["claim_id"])
        # T4 same-part repeat with no repair evidence between.
        elif (parts and overlap >= jac_min and day_gap is not None and day_gap <= t4_win):
            if tier not in ("T1", "T2", "T3"):
                tier, basis = "T4", "same part claimed again within 180d"
            matches.append(other["claim_id"])

    # Frequency anomalies.
    freq_flag = None
    if recent_30 + 1 >= freq30:
        freq_flag = f"{recent_30 + 1} claims within 30 days"
        if not tier:
            tier, basis = "FREQ", freq_flag
    elif recent_12m + 1 >= freq12:
        freq_flag = f"{recent_12m + 1} claims in 12 months"

    # Backdating: incident near policy inception or just before expiry.
    backdate = _near_inception_or_expiry(claim, thr)

    min_lane_by_tier = {
        "T1": Lane.INVESTIGATIVE.value, "T2": Lane.INVESTIGATIVE.value,
        "T3": Lane.ASSISTED.value, "T4": Lane.ASSISTED.value,
        "FREQ": Lane.INVESTIGATIVE.value,
    }
    is_dup = bool(tier)
    return {
        "is_duplicate": is_dup, "tier": tier, "matches": matches[:5],
        "basis": basis, "min_lane": min_lane_by_tier.get(tier, Lane.ASSISTED.value),
        "frequency_flag": freq_flag, "backdating_flag": backdate,
    }


def _norm_parts(parts) -> list[str]:
    out = []
    for p in parts or []:
        k = rate_card.normalize_part(p)
        if k and k not in out:
            out.append(k)
    return out


def _near_inception_or_expiry(claim: dict[str, Any], thr: dict) -> str | None:
    from datetime import datetime

    def _p(x):
        try:
            dt = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
        except Exception:
            return None

    inc = _p(claim.get("incident_date"))
    pf, pt = _p(claim.get("period_from")), _p(claim.get("period_to"))
    if not inc:
        return None
    if pf and 0 <= (inc - pf).days <= int(thr.get("near_inception_days", 15)):
        return f"incident within {(inc - pf).days}d of policy inception (backdating tell)"
    if pt and 0 <= (pt - inc).days <= int(thr.get("near_expiry_days", 7)):
        return f"incident within {(pt - inc).days}d before policy expiry"
    return None


def _entity_links(st, claim: dict[str, Any]) -> dict[str, Any]:
    """Live collusion signal: how many OTHER claims share this claim's entities."""
    all_claims = st.list_claims(limit=5000)
    cid = claim["claim_id"]
    shared = {"garage_id": 0, "surveyor_id": 0, "bank_account": 0}
    linked: set[str] = set()
    for key in shared:
        val = claim.get(key)
        if not val:
            continue
        for other in all_claims:
            if other.get("claim_id") != cid and other.get(key) == val:
                shared[key] += 1
                linked.add(other["claim_id"])
    size = len(linked) + 1
    import math

    return {
        "shared_garage_count": shared["garage_id"],
        "shared_surveyor_count": shared["surveyor_id"],
        "shared_bank_count": shared["bank_account"],
        "component_size": size,
        "ring_risk": round(1 - math.exp(-0.3 * (size - 1)), 4),
        "is_ring_claim": int(size >= 5),
        "_linked_claims": sorted(linked),
    }


# --------------------------------------------------------------------------- #
# 4. DECISION / SETTLEMENT
# --------------------------------------------------------------------------- #
def record_decision(claim_id: str, action: str, actor: str,
                    override_reason: str | None = None,
                    to_lane: str | None = None,
                    settlement_amount: float | None = None) -> dict[str, Any]:
    st = get_store()
    claim = st.get_claim(claim_id) or {}
    row = {
        "claim_id": claim_id, "actor": actor, "action": action,
        "from_lane": claim.get("lane"), "to_lane": to_lane,
        "override_reason": override_reason,
        "settlement_amount": settlement_amount, "created_at": _now(),
    }
    st.insert("claim_decisions", row)

    patch: dict[str, Any] = {}
    if action == "approve":
        patch["status"] = "approved"
    elif action == "decline":
        patch["status"] = "declined"
    elif action == "settle":
        patch["status"] = "paid"
    elif action == "request_evidence":
        patch["status"] = "retake"
    elif action == "override" and to_lane:
        patch["lane"] = to_lane
        patch["status"] = {"lane1_touchless": "approved",
                           "lane2_assisted": "awaiting_officer",
                           "lane3_investigative": "investigating"}.get(to_lane, "scored")
    if patch:
        st.update_claim(claim_id, patch)
    st.event(claim_id, f"decision_{action}", row, actor)
    return row


def settle(claim_id: str, actor: str = "SYSTEM") -> dict[str, Any]:
    """Compute settlement using the REAL IRDAI depreciation grid from config."""
    st = get_store()
    claim = st.get_claim(claim_id) or {}
    score = st.latest_score(claim_id) or {}
    ref = constants.load_distributions()["reference_data"]

    gross = float(score.get("cost_p50") or claim.get("claim_amount") or 0)
    idv = float(claim.get("idv") or 0)
    age = float(claim.get("vehicle_age_years") or 0)

    # Constructive total loss (SOURCED: repair > 75% of IDV)
    total_loss = bool(idv and gross > ref["total_loss_threshold_pct"] * idv)
    if total_loss:
        dep = next(r["dep"] for r in ref["idv_depreciation_by_age"] if age <= r["max_years"])
        net = idv * (1 - dep)
        deprec = idv - net
        consum = 0.0
    else:
        dep_rate = next(r["dep"] for r in ref["parts_depreciation"]["metal_by_age"]
                        if age <= r["max_years"])
        deprec = gross * dep_rate
        consum = gross * ref["consumables_pct"][0]
        net = gross - deprec - consum

    deductible = float(claim.get("deductible") or ref["voluntary_deductible_tiers"][0])
    net = max(0.0, net - deductible)

    row = {"claim_id": claim_id, "gross_amount": round(gross, 2),
           "depreciation": round(deprec, 2), "consumables": round(consum, 2),
           "deductible": deductible, "net_payable": round(net, 2),
           "total_loss": total_loss,
           "utr_reference": "UTR" + claim_id.replace("CLM-", ""),
           "paid_at": _now(), "created_at": _now()}
    st.insert("settlements", row)
    record_decision(claim_id, "settle", actor, settlement_amount=row["net_payable"])
    st.event(claim_id, "settled", row, actor)
    return row


def timeline(claim_id: str) -> list[dict[str, Any]]:
    st = get_store()
    evs = st.list_child("claim_events", claim_id)
    return sorted(evs, key=lambda e: e.get("created_at", ""))
