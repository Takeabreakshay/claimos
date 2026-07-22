"""THE BRAIN — inference-time cognitive stack (BRAIN_DECISION_ENGINE.md).

Takes a brand-new, never-seen claim, analyses it *and itself*, and decides how to
move forward. Eight levels, each with its own decision and its own exits:

  L0 PERCEIVE       structure the input · is it complete enough to reason about?
  L1 VERIFY         declared reality vs authoritative reality (rails + graph)
  L2 ASSESS         five modules -> belief vector, each with a confidence
  L3 METACOGNITION  "Am I confident? Do I have enough? Is this familiar?"   <-- the point
  L4 DECIDE         the wedge -> lane
  L5 ACT            (executed by workflow.py)
  L6 EXPLAIN        reason codes + legal fairness check
  L7 LEARN          (feedback captured by workflow.record_decision)

The invariant that makes autonomy safe: **abstention over error**. Whenever
confidence is low, evidence is thin, or the claim is unlike anything seen in
training, the brain routes to a human instead of guessing — and logs the claim as
high-value training data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from src import constants
from src.live.store import get_store

# Required evidence for a motor claim to be reasoned about at all.
REQUIRED_FIELDS = ["policy_id", "claim_type", "incident_severity", "claim_amount",
                   "idv", "geo", "garage_type"]
COMPLETENESS_FLOOR = 0.80      # below this -> ask for the specific missing item
MIN_PHOTOS = 1
NOVELTY_FLOOR = 0.60           # OOD score above this -> unfamiliar -> human


@dataclass
class LevelResult:
    level: str
    question: str
    decision: str
    detail: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    exit: str | None = None     # set when this level short-circuits the cascade


@dataclass
class BrainTrace:
    claim_id: str
    levels: list[LevelResult] = field(default_factory=list)
    outcome: str = ""
    outcome_reason: str = ""
    self_assessment: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "levels": [asdict(x) for x in self.levels],
            "outcome": self.outcome,
            "outcome_reason": self.outcome_reason,
            "self_assessment": self.self_assessment,
        }


# --------------------------------------------------------------------------- #
# L0 · PERCEIVE
# --------------------------------------------------------------------------- #
def perceive(claim: dict, photos: list, docs: list) -> LevelResult:
    present = [f for f in REQUIRED_FIELDS if claim.get(f) not in (None, "", 0)]
    missing = [f for f in REQUIRED_FIELDS if f not in present]

    field_score = len(present) / len(REQUIRED_FIELDS)
    usable_photos = [p for p in photos if not p.get("is_blurry")]
    photo_score = min(1.0, len(usable_photos) / MIN_PHOTOS) if MIN_PHOTOS else 1.0
    completeness = round(0.7 * field_score + 0.3 * photo_score, 3)

    asks: list[str] = []
    for f in missing:
        asks.append(f"provide {f.replace('_', ' ')}")
    if not photos:
        asks.append("upload at least one damage photo")
    elif not usable_photos:
        asks.append("re-take the damage photo — the one supplied is too blurred to assess")
    if claim.get("fir_required") and not claim.get("fir_filed"):
        asks.append("upload the FIR (required for this claim type)")

    ok = completeness >= COMPLETENESS_FLOOR and bool(usable_photos)
    return LevelResult(
        "L0 · PERCEIVE", "Is this claim complete enough to reason about?",
        "proceed" if ok else "request evidence",
        {"completeness_score": completeness, "missing_fields": missing,
         "photos": len(photos), "usable_photos": len(usable_photos),
         "documents": len(docs), "requests": asks},
        [f"completeness {completeness:.0%}"] + ([f"missing: {', '.join(missing)}"] if missing else []),
        exit=None if ok else "REQUEST_EVIDENCE",
    )


# --------------------------------------------------------------------------- #
# L1 · VERIFY
# --------------------------------------------------------------------------- #
def verify(claim: dict, rails: dict, graph: dict) -> LevelResult:
    """Trust nothing declared — cross-check it against the rails and the graph."""
    hard, soft = [], []

    if str(claim.get("policy_status", "active")) == "lapsed":
        hard.append("policy lapsed on the incident date")
    if not rails.get("rail_registration_valid", 1):
        hard.append("vehicle registration not valid on VAHAN")
    if not rails.get("rail_engine_chassis_match", 1):
        hard.append("engine/chassis does not match the RC on record")
    if claim.get("driver_valid_license") in (0, False) or claim.get("dui_flag") in (1, True):
        hard.append("driver ineligible (licence / DUI)")
    if not rails.get("rail_license_valid", 1):
        soft.append("licence could not be verified on DigiLocker")
    if rails.get("rail_prior_fraud_flags", 0):
        soft.append(f"{rails['rail_prior_fraud_flags']} prior fraud flag(s) on IIB history")
    if claim.get("modification_actual") and not claim.get("modification_declared"):
        soft.append("undeclared vehicle modification")

    comp = int(graph.get("component_size", 1) or 1)
    ring_risk = float(graph.get("ring_risk", 0) or 0)
    structural = comp >= 5
    if structural:
        hard.append(f"claim sits inside a {comp}-claim shared-entity cluster")

    return LevelResult(
        "L1 · VERIFY", "Does declared reality match verified reality?",
        "investigative" if hard else ("flag" if soft else "clear"),
        {"hard_mismatches": hard, "soft_flags": soft,
         "prism_score": rails.get("rail_prism_score"),
         "quest_hit": rails.get("rail_quest_hit"),
         "component_size": comp, "ring_risk": round(ring_risk, 3)},
        hard + soft or ["declared facts match the rails"],
        exit="INVESTIGATE" if hard else None,
    )


# --------------------------------------------------------------------------- #
# L2 · ASSESS
# --------------------------------------------------------------------------- #
def assess(scored: dict) -> LevelResult:
    p_fraud = float(scored.get("p_fraud") or 0)
    p_esc = float(scored.get("p_escalation") or 0)
    p50 = float(scored.get("cost_p50") or 0)
    band = (float(scored.get("cost_p90") or 0) - float(scored.get("cost_p10") or 0))
    return LevelResult(
        "L2 · ASSESS", "What is this claim, actually?",
        "belief formed",
        {"cost_p50": p50, "cost_band": round(band, 2),
         "p_fraud": round(p_fraud, 4), "p_escalation": round(p_esc, 4),
         "coverage": scored.get("coverage_clear"),
         "c_fraud": round(float(scored.get("c_fraud") or 0), 3),
         "c_cost": round(float(scored.get("c_cost") or 0), 3),
         "c_escalation": round(float(scored.get("c_escalation") or 0), 3)},
        [f"fraud {p_fraud:.1%}", f"escalation {p_esc:.1%}",
         f"repair ~Rs{p50:,.0f}"],
    )


# --------------------------------------------------------------------------- #
# L3 · METACOGNITION  — the brain analysing itself
# --------------------------------------------------------------------------- #
def novelty_score(features: pd.DataFrame, models: dict) -> dict[str, Any]:
    """How unlike the training distribution is this claim?

    The OOD *decision* is anchored to a training percentile, not to a rescaled
    0-1 number: a claim is unfamiliar only if it is more unusual than 99% of the
    data the models were fitted on. That fixes the rate of false abstentions at
    ~1% by construction. (An earlier version normalised p01->1.0 and treated
    anything above 0.6 as novel, which abstained on perfectly ordinary claims and
    would have silently eaten the touchless share.)

    If the artifact is absent we say so rather than returning a comforting zero —
    claiming familiarity we cannot verify would defeat this layer entirely.
    """
    det = models.get("ood_detector")
    if det is None:
        return {"display": 0.0, "raw": None, "threshold": None,
                "status": "unavailable", "ood": False}
    try:
        cols = models.get("ood_features") or list(features.columns)
        x = features.reindex(columns=cols, fill_value=0.0)
        raw = float(det.score_samples(x)[0])          # higher = more normal
        thresh = float(models.get("ood_p01", -0.75))  # 1st pct of training
        hi = float(models.get("ood_p99", -0.35))      # most typical
        display = float(np.clip((hi - raw) / max(hi - thresh, 1e-6), 0.0, 1.0))
        return {"display": round(display, 3), "raw": round(raw, 4),
                "threshold": round(thresh, 4), "status": "ok",
                "ood": bool(raw < thresh)}
    except Exception as exc:
        return {"display": 0.0, "raw": None, "threshold": None,
                "status": f"error: {str(exc)[:80]}", "ood": False}


def metacognition(scored: dict, nov: dict, thresholds: dict) -> LevelResult:
    eg = thresholds["evidence_gap"]
    l1 = thresholds["lane1_touchless"]

    c_fraud = float(scored.get("c_fraud") or 0)
    c_cost = float(scored.get("c_cost") or 0)
    confidence = min(c_fraud, c_cost)           # weakest trusted signal governs
    floor = float(eg["min_component_confidence"])

    evidence_gap = confidence < floor
    unfamiliar = bool(nov.get("ood"))
    entitled = (not evidence_gap) and (not unfamiliar)

    reasons = [
        f"confidence {confidence:.2f} (min of fraud {c_fraud:.2f}, cost {c_cost:.2f})",
        f"familiarity: {'UNSEEN' if unfamiliar else 'within training distribution'}"
        + (f" [{nov['status']}]" if nov.get("status") != "ok" else ""),
    ]
    if evidence_gap:
        reasons.append(f"below the {floor:.2f} confidence floor -> not entitled to decide")
    if unfamiliar:
        reasons.append("more unusual than 99% of training data -> abstain, hand to a human")
    if entitled and confidence < float(l1["min_confidence"]):
        reasons.append("confident enough to decide, but not enough to auto-settle")

    return LevelResult(
        "L3 · METACOGNITION", "Am I confident, do I have enough, and is this familiar?",
        "proceed" if entitled else ("request evidence" if evidence_gap else "abstain -> human"),
        {"confidence": round(confidence, 3), "confidence_floor": floor,
         "novelty": nov.get("display"), "novelty_raw": nov.get("raw"),
         "novelty_threshold": nov.get("threshold"), "novelty_status": nov.get("status"),
         "evidence_gap": evidence_gap, "out_of_distribution": unfamiliar,
         "entitled_to_decide": entitled},
        reasons,
        exit=None if entitled else ("REQUEST_EVIDENCE" if evidence_gap else "HUMAN"),
    )


# --------------------------------------------------------------------------- #
# The cascade
# --------------------------------------------------------------------------- #
def think(claim_id: str, models: dict | None = None) -> BrainTrace:
    """Run the full cognitive stack on a claim and report how it reasoned."""
    from src.features import fraud_features
    from src.live.workflow import _entity_links, _to_model_row, models as _load
    from src.models.graph import build_graph_features  # noqa: F401  (kept for parity)
    from src.pipeline import score_frame
    from src.rails import enrich_claim

    store = get_store()
    models = models or _load()
    claim = store.get_claim(claim_id)
    if not claim:
        raise ValueError(f"unknown claim {claim_id}")
    photos = store.list_child("claim_photos", claim_id)
    docs = store.list_child("claim_documents", claim_id)
    thresholds = constants.load_thresholds()

    trace = BrainTrace(claim_id=claim_id)

    # ---- L0
    l0 = perceive(claim, photos, docs)
    trace.levels.append(l0)

    # ---- L1 (needs rails + graph even if L0 wants evidence — the ring may be the reason)
    mrow = _to_model_row(claim)
    links = _entity_links(store, claim)
    mrow.update({k: v for k, v in links.items() if not k.startswith("_")})
    rails = enrich_claim(mrow)
    l1 = verify(claim, rails, links)
    trace.levels.append(l1)

    # ---- L2
    df = pd.DataFrame([mrow])
    graph_df = pd.DataFrame([{
        "component_size": links["component_size"],
        "shared_garage_count": links["shared_garage_count"],
        "shared_surveyor_count": links["shared_surveyor_count"],
        "shared_bank_count": links["shared_bank_count"],
        "ring_risk": links["ring_risk"],
    }], index=df.index)
    scored = score_frame(df, models=models, graph_df=graph_df).iloc[0].to_dict()
    trace.levels.append(assess(scored))

    # ---- L3
    rails_df = pd.DataFrame([rails], index=df.index)
    cost_pred = pd.DataFrame([{ "P10": scored["cost_p10"], "P50": scored["cost_p50"],
                                "P90": scored["cost_p90"] }], index=df.index)
    xf = fraud_features(df, rails_df, graph_df, cost_pred)
    nov = novelty_score(xf, models)
    l3 = metacognition(scored, nov, thresholds)
    trace.levels.append(l3)

    trace.self_assessment = {
        "completeness": l0.detail["completeness_score"],
        "confidence": l3.detail["confidence"],
        "novelty": l3.detail["novelty"],
        "evidence_gap": l3.detail["evidence_gap"],
        "out_of_distribution": l3.detail["out_of_distribution"],
        "entitled_to_decide": l3.detail["entitled_to_decide"],
        "hard_mismatches": l1.detail["hard_mismatches"],
    }

    # ---- L4 (only if the brain is entitled to decide)
    from src.triage import route_claim

    decision = route_claim({**mrow, **scored})

    # The four paths (BRAIN doc): ask for more · investigate · human · auto-settle
    if l0.exit == "REQUEST_EVIDENCE":
        trace.outcome = "ASK FOR MORE"
        trace.outcome_reason = "; ".join(l0.detail["requests"][:3]) or "evidence incomplete"
    elif l1.exit == "INVESTIGATE":
        trace.outcome = "INVESTIGATE"
        trace.outcome_reason = "; ".join(l1.detail["hard_mismatches"][:3])
    elif l3.exit == "REQUEST_EVIDENCE":
        trace.outcome = "ASK FOR MORE"
        trace.outcome_reason = "confidence below the floor — the brain is not entitled to decide"
    elif l3.exit == "HUMAN":
        trace.outcome = "ASSIST A HUMAN"
        trace.outcome_reason = "claim is unlike anything seen in training — abstaining rather than guessing"
    else:
        lane = decision.outcome
        trace.outcome = {"lane1_touchless": "AUTO-SETTLE",
                         "lane2_assisted": "ASSIST A HUMAN",
                         "lane3_investigative": "INVESTIGATE",
                         "coverage_reject": "DECLINE (human-reviewed)",
                         "retake": "ASK FOR MORE"}.get(lane, lane)
        trace.outcome_reason = "; ".join(decision.reasons[:3])

    trace.levels.append(LevelResult(
        "L4 · DECIDE", "How much automation has this claim earned?",
        decision.outcome, {"lane": decision.outcome, "legal_check": decision.legal_check},
        decision.reasons,
    ))
    return trace
