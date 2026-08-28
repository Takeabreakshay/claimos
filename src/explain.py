"""Phase 5 - explainability (CLAUDE.md §3 rule 9, §8, LOGIC §Explainability).

Every routed claim returns a structured, non-empty explanation:
  * lane + why (the thresholds/triggers that fired, from triage)
  * top SHAP contributors for fraud / cost / escalation (TreeSHAP on the LGBMs)
  * coverage rule hits
  * a plain-English, template-based reason (no LLM key needed)
  * the legal-check flag (delayed-intimation -> human, not reject)

PwC lesson: trust needs the *why*, not just the *what*.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from src.constants import Lane
from src.features import cost_features, escalation_features, fraud_features
from src.triage import COVERAGE_REJECT, RETAKE, route_claim

_LANE_LABEL = {
    Lane.TOUCHLESS.value: "Lane 1 · Touchless (straight-through)",
    Lane.ASSISTED.value: "Lane 2 · Assisted (officer approves)",
    Lane.INVESTIGATIVE.value: "Lane 3 · Investigative (surveyor + fraud)",
    RETAKE: "Evidence-gap retake (bounded resubmission)",
    COVERAGE_REJECT: "Coverage decline (human-reviewed)",
}


@dataclass
class Explanation:
    claim_id: str
    outcome: str
    lane_label: str
    lane_reasons: list[str]
    fraud_drivers: list[tuple[str, float]] = field(default_factory=list)
    cost_drivers: list[tuple[str, float]] = field(default_factory=list)
    escalation_drivers: list[tuple[str, float]] = field(default_factory=list)
    rule_hits: list[str] = field(default_factory=list)
    legal_check_flag: bool = False
    plain_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _top_shap(explainer, x_row: pd.DataFrame, top_k: int = 3) -> list[tuple[str, float]]:
    """Top-k features by |SHAP value| for a single-row feature frame."""
    vals = explainer.shap_values(x_row)
    # Binary LGBMClassifier TreeSHAP may return a list [class0, class1]; take positive class.
    if isinstance(vals, list):
        vals = vals[-1]
    contrib = np.asarray(vals).reshape(-1)
    order = np.argsort(np.abs(contrib))[::-1][:top_k]
    return [(str(x_row.columns[i]), round(float(contrib[i]), 4)) for i in order]


class Explainer:
    """Holds TreeSHAP explainers for the three models; explains routed claims."""

    def __init__(self, models: dict):
        import shap

        self.models = models
        self.fraud_expl = shap.TreeExplainer(models["fraud_model"])
        self.cost_expl = shap.TreeExplainer(models["cost_models"]["P50"])
        self.esc_expl = shap.TreeExplainer(models["escalation_model"])

    def explain_claim(
        self,
        raw_claim: Mapping[str, Any],
        scored: Mapping[str, Any],
        rails_row: Mapping[str, Any] | None = None,
    ) -> Explanation:
        """Explain one claim. ``raw_claim`` = raw fields; ``scored`` = score_frame
        row (p_fraud, coverage, etc.); ``rails_row`` = enrich_claim output (for the
        fraud feature row)."""
        rec = {**dict(raw_claim), **dict(scored)}
        decision = route_claim(rec)

        df1 = pd.DataFrame([dict(raw_claim)])
        cost_pred = pd.DataFrame(
            [{"P10": scored["cost_p10"], "P50": scored["cost_p50"], "P90": scored["cost_p90"]}]
        )
        rails_df = pd.DataFrame([dict(rails_row)]) if rails_row is not None else None
        graph_df = pd.DataFrame(
            [
                {
                    c: rec.get(c, 0)
                    for c in [
                        "component_size",
                        "shared_garage_count",
                        "shared_surveyor_count",
                        "shared_bank_count",
                        "ring_risk",
                    ]
                }
            ]
        )

        fraud_drivers: list[tuple[str, float]] = []
        if rails_df is not None:
            xf = fraud_features(df1, rails_df, graph_df, cost_pred)
            fraud_drivers = _top_shap(self.fraud_expl, xf)
        cost_drivers = _top_shap(self.cost_expl, cost_features(df1))
        esc_drivers = _top_shap(self.esc_expl, escalation_features(df1))

        rule_hits: list[str] = []
        if str(scored.get("coverage_reason", "none")) != "none":
            rule_hits.append(f"coverage:{scored['coverage_reason']}")
        rule_hits.extend(decision.reasons)

        legal = bool(decision.legal_check)
        plain = self._plain_reason(decision, rec, fraud_drivers, cost_drivers, legal)

        return Explanation(
            claim_id=str(raw_claim.get("claim_id", "UNKNOWN")),
            outcome=decision.outcome,
            lane_label=_LANE_LABEL.get(decision.outcome, decision.outcome),
            lane_reasons=decision.reasons,
            fraud_drivers=fraud_drivers,
            cost_drivers=cost_drivers,
            escalation_drivers=esc_drivers,
            rule_hits=rule_hits,
            legal_check_flag=legal,
            plain_reason=plain,
        )

    @staticmethod
    def _plain_reason(decision, rec, fraud_drivers, cost_drivers, legal) -> str:
        amt = float(rec.get("claim_amount", 0))
        pf = float(rec.get("p_fraud", 0))
        p50 = float(rec.get("cost_p50", 0))
        parts = []
        if decision.outcome == Lane.TOUCHLESS.value:
            parts.append(
                f"Auto-settled (Touchless): claim Rs{amt:,.0f} is under the Rs50,000 seam, "
                f"fraud probability {pf:.0%} is low, and model confidence is high."
            )
        elif decision.outcome == Lane.INVESTIGATIVE.value:
            parts.append("Routed to Investigative: " + "; ".join(decision.reasons) + ".")
        elif decision.outcome == COVERAGE_REJECT:
            parts.append("Coverage decline (human-reviewed): " + "; ".join(decision.reasons) + ".")
        elif decision.outcome == RETAKE:
            parts.append("Sent back for evidence retake: " + "; ".join(decision.reasons) + ".")
        else:
            parts.append(
                "Routed to Assisted (officer review): " + "; ".join(decision.reasons) + "."
            )
        if legal:
            parts.append(
                "Legal-check flag: late intimation with a valid reason is NOT auto-rejected "
                "(SC rulings) - a human confirms the decision."
            )
        if fraud_drivers:
            parts.append("Top fraud signals: " + ", ".join(f"{k}" for k, _ in fraud_drivers) + ".")
        parts.append(f"Estimated repair (P50): Rs{p50:,.0f}.")
        return " ".join(parts)


def explain_frame(raw: pd.DataFrame, scored: pd.DataFrame, models: dict) -> pd.DataFrame:
    """Batch explanations (uses the raw+scored joined records). Returns a frame of
    outcome / lane_label / plain_reason / rule_hits / legal_check per claim.

    For speed at scale this uses route reasons + coverage hits (no per-row SHAP);
    per-claim SHAP is available via ``Explainer.explain_claim`` for the demo.
    """
    from src.triage import route_frame

    routed = route_frame(raw, scored)
    out = pd.DataFrame(index=raw.index)
    out["claim_id"] = raw["claim_id"].to_numpy()
    out["outcome"] = routed["outcome"].to_numpy()
    out["lane_label"] = routed["outcome"].map(_LANE_LABEL).to_numpy()
    out["primary_reason"] = routed["primary_reason"].to_numpy()
    out["coverage_reason"] = scored["coverage_reason"].to_numpy()
    out["legal_check_flag"] = scored["legal_weak_reject_flag"].to_numpy().astype(int)
    return out
