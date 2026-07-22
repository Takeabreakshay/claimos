"""Phase 3 — feature engineering from raw claim + rails (CLAUDE.md §7, LOGIC §3).

One source of truth for the model feature matrices, used identically in training
(pipeline) and inference (triage / demo). Three per-model feature sets, each
built to avoid label leakage:

  * cost_features       — vehicle/claim attributes; EXCLUDES ``claim_amount`` and
                          every label (LOGIC §1.1/§3.1: cost model must not see the
                          claimed figure, else the inflation gap leaks).
  * fraud_features      — observable fields + rails + graph + cost-derived ratios.
  * escalation_features — FNOL-visible only; EXCLUDES the hidden latent signals
                          (tp_linkage / ambiguous_liability / injury_hint) that
                          generated the label (LOGIC §3.3), so AUC stays ~0.70.

All transforms are deterministic; trees don't need scaling.
"""

from __future__ import annotations

import pandas as pd

SEVERITY_ORDER = {"minor": 0, "moderate": 1, "severe": 2, "total": 3}

# Rail columns consumed by the fraud model (produced by rails.enrich_frame).
_RAIL_FRAUD_COLS = [
    "rail_prism_score",
    "rail_prism_percentile",
    "rail_quest_hit",
    "rail_engine_chassis_match",
    "rail_registration_valid",
    "rail_license_valid",
    "rail_prior_claims_3y",
    "rail_prior_fraud_flags",
    "rail_days_since_last_claim",
]
_GRAPH_COLS = [
    "component_size",
    "shared_garage_count",
    "shared_surveyor_count",
    "shared_bank_count",
    "ring_risk",
]


def _common_engineered(df: pd.DataFrame) -> pd.DataFrame:
    """Attributes shared by every model (no label, no claim_amount)."""
    out = pd.DataFrame(index=df.index)
    out["idv"] = df["idv"].astype(float)
    out["vehicle_age_years"] = df["vehicle_age_years"].astype(float)
    out["num_photos"] = df["num_photos"].astype(float)
    out["photo_quality_score"] = df["photo_quality_score"].astype(float)
    out["severity_ord"] = df["incident_severity"].map(SEVERITY_ORDER).astype(float)
    out["is_OD"] = (df["claim_type"] == "OD").astype(int)
    out["is_TP"] = (df["claim_type"] == "TP").astype(int)
    out["is_theft_total"] = (df["claim_type"] == "theft_total").astype(int)
    out["geo_metro"] = (df["geo"] == "metro").astype(int)
    out["geo_urban"] = (df["geo"] == "urban").astype(int)
    out["geo_rural"] = (df["geo"] == "rural").astype(int)
    out["is_non_network"] = (df["garage_type"] == "non_network").astype(int)
    return out


def cost_features(df: pd.DataFrame) -> pd.DataFrame:
    """Cost-model features. Excludes claim_amount (the claimed figure) and labels."""
    return _common_engineered(df)


def escalation_features(df: pd.DataFrame) -> pd.DataFrame:
    """Escalation features — FNOL-visible only, no hidden latent signals."""
    out = _common_engineered(df)
    out["claim_amount"] = df["claim_amount"].astype(float)
    out["claim_to_idv_ratio"] = (df["claim_amount"] / df["idv"]).astype(float)
    out["low_amount"] = (df["claim_amount"] < 50000).astype(int)
    out["intimation_gt_48h"] = df["intimation_gt_48h"].astype(int)
    out["fir_required"] = df["fir_required"].astype(int)
    return out


def fraud_features(
    df: pd.DataFrame,
    rails_df: pd.DataFrame,
    graph_df: pd.DataFrame,
    cost_pred: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Fraud features — observable fields + rails + graph + cost-derived ratios."""
    out = _common_engineered(df)
    out["claim_amount"] = df["claim_amount"].astype(float)
    out["claim_to_idv_ratio"] = (df["claim_amount"] / df["idv"]).astype(float)
    out["intimation_delay_hours"] = df["intimation_delay_hours"].astype(float)
    out["intimation_gt_48h"] = df["intimation_gt_48h"].astype(int)
    out["intimation_reason_valid"] = df["intimation_reason_valid"].astype(int)
    out["mod_mismatch"] = df["modification_undeclared"].astype(int)
    out["non_network_garage"] = df["non_network_garage"].astype(int)
    out["photo_reuse_flag"] = df["photo_reuse_flag"].astype(int)
    out["fir_required"] = df["fir_required"].astype(int)
    out["fir_ok"] = ((df["fir_required"] == 0) | (df["fir_filed"] == 1)).astype(int)
    out["dui_flag"] = df["dui_flag"].astype(int)
    out["driver_valid_license"] = df["driver_valid_license"].astype(int)

    for col in _RAIL_FRAUD_COLS:
        out[col] = rails_df[col].to_numpy()
    for col in _GRAPH_COLS:
        out[col] = graph_df[col].to_numpy()

    if cost_pred is not None:
        p50 = cost_pred["P50"].clip(lower=1.0).to_numpy()
        p10 = cost_pred["P10"].to_numpy()
        p90 = cost_pred["P90"].to_numpy()
        # claim-vs-predicted gap: >1 => possible inflation (LOGIC §3).
        out["claim_to_predicted_ratio"] = df["claim_amount"].to_numpy() / p50
        out["cost_band_width"] = (p90 - p10) / p50
    return out
