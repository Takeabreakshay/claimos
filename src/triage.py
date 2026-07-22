"""Phase 4 — THE WEDGE: the risk-triage routing policy (CLAUDE.md §6, LOGIC Phase 4).

Reads ``config/thresholds.yaml`` and routes each *scored* claim. Deterministic;
emits the triggered reasons at every step (rule 9). Routing order (rule 6/8):

  A. evidence-gap gate   -> RETAKE (bounded) if confidence is below floor
  B. coverage reject     -> COVERAGE_REJECT on hard eligibility failure
  C. Lane 3 (ANY)        -> Investigative
  D. legal-weak override -> Lane 2 (human legal check; never auto-settle a late claim)
  E. Lane 1 (ALL)        -> Touchless  (< Rs50k anchor)
  F. else                -> Lane 2 (Assisted, the default)

Notes on the two gate outcomes (Rule-13 interpretation, tied to the §10 test):
  * COVERAGE_REJECT — a lapsed policy / ineligible driver / missing FIR is a
    hard rule failure; it is not auto-settled. (Late intimation is NOT here — SC
    rulings, handled by the legal-weak override.)
  * legal-weak override — any late intimation (>48h), even with a valid reason,
    goes to a human with a legal-check note instead of Lane 1. This is the
    fairness / appeal-rate win (§7/§8). Late-alone is never an auto-reject.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from src import constants
from src.constants import Lane
from src.models.coverage import CLEAR, NOT_CLEAR

# Outcomes beyond the three lanes.
RETAKE = "retake"
COVERAGE_REJECT = "coverage_reject"


@dataclass
class RouteDecision:
    outcome: str  # Lane value | 'retake' | 'coverage_reject'
    reasons: list[str] = field(default_factory=list)
    legal_check: bool = False
    retake_requested: bool = False


def _get(rec: Mapping[str, Any], key: str, default: Any = 0) -> Any:
    val = rec[key] if key in rec else default
    return default if val is None or (isinstance(val, float) and np.isnan(val)) else val


def route_claim(
    rec: Mapping[str, Any],
    thresholds: dict | None = None,
    loops_used: int = 0,
) -> RouteDecision:
    """Route one scored claim record through the wedge.

    ``rec`` must carry the raw fields {claim_amount, incident_severity,
    claim_type, injury_hint, intimation_gt_48h} and the scored fields {p_fraud,
    p_escalation, model_confidence, coverage_clear, coverage_reason,
    legal_weak_reject_flag}. Optional: required_doc_missing.
    """
    thr = thresholds if thresholds is not None else constants.load_thresholds()
    eg = thr["evidence_gap"]
    l1 = thr["lane1_touchless"]
    l3 = thr["lane3_investigative"]

    amount = float(_get(rec, "claim_amount", 0.0))
    severity = str(_get(rec, "incident_severity", "minor"))
    claim_type = str(_get(rec, "claim_type", "OD"))
    injury = int(_get(rec, "injury_hint", 0))
    late = int(_get(rec, "intimation_gt_48h", 0))
    p_fraud = float(_get(rec, "p_fraud", 0.0))
    p_esc = float(_get(rec, "p_escalation", 0.0))
    conf = float(_get(rec, "model_confidence", 1.0))
    coverage_clear = str(_get(rec, "coverage_clear", CLEAR))
    coverage_reason = str(_get(rec, "coverage_reason", "none"))
    legal_weak = bool(int(_get(rec, "legal_weak_reject_flag", 0)))
    doc_missing = bool(int(_get(rec, "required_doc_missing", 0)))

    # ----- A. evidence-gap gate ---------------------------------------------
    if (conf < eg["min_component_confidence"] or doc_missing) and loops_used < eg[
        "max_retake_loops"
    ]:
        reason = (
            "doc_missing"
            if doc_missing
            else f"low_confidence({conf:.2f}<{eg['min_component_confidence']})"
        )
        return RouteDecision(RETAKE, [reason], legal_check=legal_weak, retake_requested=True)

    # ----- B. coverage reject (hard eligibility failure) --------------------
    if coverage_clear == NOT_CLEAR:
        return RouteDecision(
            COVERAGE_REJECT, [f"coverage:{coverage_reason}"], legal_check=legal_weak
        )

    # ----- C. Lane 3 (Investigative): ANY trigger ---------------------------
    l3_reasons: list[str] = []
    if p_fraud >= l3["min_fraud_prob"]:
        l3_reasons.append(f"fraud_prob>={l3['min_fraud_prob']}")
    if amount >= l3["high_value_threshold"]:
        l3_reasons.append(f"high_value>={l3['high_value_threshold']}")
    if severity in set(l3["severities"]):
        l3_reasons.append(f"severity={severity}")
    if p_esc >= l3["min_escalation_prob"]:
        l3_reasons.append(f"escalation_prob>={l3['min_escalation_prob']}")
    if l3.get("tp_with_injury") and claim_type == "TP" and injury == 1:
        l3_reasons.append("tp_with_injury")
    if l3_reasons:
        return RouteDecision(Lane.INVESTIGATIVE.value, l3_reasons, legal_check=legal_weak)

    # ----- D. legal-weak override -> human legal check (Lane 2) -------------
    if legal_weak:
        return RouteDecision(
            Lane.ASSISTED.value, ["legal_check:delayed_intimation_valid_reason"], legal_check=True
        )

    # ----- E. Lane 1 (Touchless): ALL must hold -----------------------------
    l1_fail: list[str] = []
    if not amount < l1["max_claim_amount"]:
        l1_fail.append(f"amount>={l1['max_claim_amount']}")
    if not p_fraud < l1["max_fraud_prob"]:
        l1_fail.append(f"fraud_prob>={l1['max_fraud_prob']}")
    if not conf >= l1["min_confidence"]:
        l1_fail.append(f"confidence<{l1['min_confidence']}")
    if not p_esc < l1["max_escalation_prob"]:
        l1_fail.append(f"escalation_prob>={l1['max_escalation_prob']}")
    if severity not in set(l1["allowed_severity"]):
        l1_fail.append(f"severity={severity}")
    if l1["require_coverage_clear"] and coverage_clear != CLEAR:
        l1_fail.append(f"coverage={coverage_clear}")
    if l1["require_intimation_ok"] and late == 1:
        l1_fail.append("intimation_late")

    if not l1_fail:
        return RouteDecision(Lane.TOUCHLESS.value, ["all_lane1_conditions_met"], legal_check=False)

    # ----- F. else Lane 2 (Assisted) ----------------------------------------
    return RouteDecision(
        Lane.ASSISTED.value, ["default_assisted:" + ",".join(l1_fail[:3])], legal_check=legal_weak
    )


def route_frame(
    raw: pd.DataFrame,
    scored: pd.DataFrame,
    thresholds: dict | None = None,
) -> pd.DataFrame:
    """Vectorized routing over a frame. Returns outcome + primary_reason +
    legal_check, index-aligned to ``raw``.

    ``raw`` supplies claim_amount/incident_severity/claim_type/injury_hint/
    intimation_gt_48h; ``scored`` supplies the model/coverage signals
    (pipeline.score_frame output).
    """
    thr = thresholds if thresholds is not None else constants.load_thresholds()
    eg, l1, l3 = thr["evidence_gap"], thr["lane1_touchless"], thr["lane3_investigative"]
    n = len(raw)

    amount = raw["claim_amount"].to_numpy(dtype=float)
    severity = raw["incident_severity"].to_numpy()
    claim_type = raw["claim_type"].to_numpy()
    injury = raw["injury_hint"].to_numpy()
    late = raw["intimation_gt_48h"].to_numpy()

    p_fraud = scored["p_fraud"].to_numpy(dtype=float)
    p_esc = scored["p_escalation"].to_numpy(dtype=float)
    conf = scored["model_confidence"].to_numpy(dtype=float)
    coverage_clear = scored["coverage_clear"].to_numpy()
    coverage_reason = scored["coverage_reason"].to_numpy()
    legal_weak = scored["legal_weak_reject_flag"].to_numpy().astype(bool)

    outcome = np.empty(n, dtype=object)
    reason = np.empty(n, dtype=object)
    decided = np.zeros(n, dtype=bool)

    # A. evidence gap
    m = (conf < eg["min_component_confidence"]) & ~decided
    outcome[m], reason[m], decided[m] = RETAKE, "low_confidence", True

    # B. coverage reject
    m = (coverage_clear == NOT_CLEAR) & ~decided
    outcome[m] = COVERAGE_REJECT
    reason[m] = np.char.add("coverage:", coverage_reason[m].astype(str))
    decided[m] = True

    # C. Lane 3 (ANY)
    l3_trigger = (
        (p_fraud >= l3["min_fraud_prob"])
        | (amount >= l3["high_value_threshold"])
        | np.isin(severity, list(l3["severities"]))
        | (p_esc >= l3["min_escalation_prob"])
        | ((claim_type == "TP") & (injury == 1) & bool(l3.get("tp_with_injury")))
    )
    m = l3_trigger & ~decided
    outcome[m], reason[m], decided[m] = Lane.INVESTIGATIVE.value, "lane3_trigger", True

    # D. legal-weak override
    m = legal_weak & ~decided
    outcome[m], reason[m], decided[m] = Lane.ASSISTED.value, "legal_check:delayed_intimation", True

    # E. Lane 1 (ALL)
    lane1_ok = (
        (amount < l1["max_claim_amount"])
        & (p_fraud < l1["max_fraud_prob"])
        & (conf >= l1["min_confidence"])
        & (p_esc < l1["max_escalation_prob"])
        & np.isin(severity, list(l1["allowed_severity"]))
        & (coverage_clear == CLEAR)
        & (late == 0)
    )
    m = lane1_ok & ~decided
    outcome[m], reason[m], decided[m] = Lane.TOUCHLESS.value, "all_lane1_conditions_met", True

    # F. else Lane 2
    m = ~decided
    outcome[m], reason[m] = Lane.ASSISTED.value, "default_assisted"

    out = pd.DataFrame(index=raw.index)
    out["outcome"] = outcome
    out["primary_reason"] = reason
    out["legal_check"] = legal_weak.astype(int)
    return out
