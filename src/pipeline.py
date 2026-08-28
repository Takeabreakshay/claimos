"""Orchestrator - generate -> train -> (route -> evaluate).

Phase 3 scope: train the cost / fraud / escalation models, calibrate the two
classifiers, build graph features, save artifacts to ``models/`` and a metrics
snapshot to ``reports/``. Routing + evaluation are wired in Phases 4/6.

Entry point: ``poetry run claimos-pipeline``.
"""

from __future__ import annotations

import json

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

from src import constants
from src.calibration import ProbabilityCalibrator, brier_score, expected_calibration_error
from src.constants import SEED
from src.data_gen import generate_claims
from src.features import cost_features, escalation_features, fraud_features
from src.models import cost as cost_m
from src.models import escalation as esc_m
from src.models import fraud as fraud_m
from src.models.coverage import coverage_frame
from src.models.graph import build_graph_features, detect_rings
from src.rails import enrich_frame

# Artifact paths
COST_PKL = constants.MODELS_DIR / "cost_models.pkl"
FRAUD_PKL = constants.MODELS_DIR / "fraud_model.pkl"
FRAUD_CAL_PKL = constants.MODELS_DIR / "fraud_calibrator.pkl"
ESC_PKL = constants.MODELS_DIR / "escalation_model.pkl"
ESC_CAL_PKL = constants.MODELS_DIR / "escalation_calibrator.pkl"
FEATURES_PKL = constants.MODELS_DIR / "feature_cols.pkl"
OOD_PKL = constants.MODELS_DIR / "ood_detector.pkl"
TRAIN_METRICS_JSON = constants.REPORTS_DIR / "train_metrics.json"


def train_and_save_models(df: pd.DataFrame | None = None, save: bool = True) -> dict:
    """Train all Phase-3 models, evaluate on a holdout, optionally persist.

    Returns a dict with the trained objects and the holdout metrics.
    """
    if df is None:
        df = generate_claims()

    mp = constants.load_distributions()["model_params"]
    rails_df = enrich_frame(df)
    graph_df = build_graph_features(df)

    # ----- splits: train / calibration / test (stratified on fraud) ----------
    idx = np.arange(len(df))
    train_idx, test_idx = train_test_split(
        idx, test_size=mp["test_size"], random_state=SEED, stratify=df["is_fraud"].to_numpy()
    )
    train_core_idx, calib_idx = train_test_split(
        train_idx,
        test_size=mp["calibration_size"],
        random_state=SEED,
        stratify=df["is_fraud"].to_numpy()[train_idx],
    )

    def rows(frame: pd.DataFrame, sel: np.ndarray) -> pd.DataFrame:
        return frame.iloc[sel]

    # ----- cost model --------------------------------------------------------
    x_cost = cost_features(df)
    y_cost = df["true_repair_cost"].to_numpy()
    cost_models = cost_m.train_cost(rows(x_cost, train_core_idx), y_cost[train_core_idx])
    cost_pred_all = cost_m.predict_cost(cost_models, x_cost)
    cost_mape = cost_m.mape(y_cost[test_idx], cost_pred_all["P50"].to_numpy()[test_idx])

    # ----- fraud model (+ calibration) ---------------------------------------
    x_fraud = fraud_features(df, rails_df, graph_df, cost_pred_all)
    y_fraud = df["is_fraud"].to_numpy()
    fraud_model = fraud_m.train_fraud(rows(x_fraud, train_core_idx), y_fraud[train_core_idx])
    fraud_cal = ProbabilityCalibrator("isotonic").fit(
        fraud_m.predict_fraud(fraud_model, rows(x_fraud, calib_idx)), y_fraud[calib_idx]
    )
    p_fraud_test = fraud_cal.predict(fraud_m.predict_fraud(fraud_model, rows(x_fraud, test_idx)))
    fraud_auc = roc_auc_score(y_fraud[test_idx], p_fraud_test)
    fraud_brier = brier_score(p_fraud_test, y_fraud[test_idx])
    fraud_ece = expected_calibration_error(p_fraud_test, y_fraud[test_idx])

    # ----- escalation model (+ calibration) ----------------------------------
    x_esc = escalation_features(df)
    y_esc = df["escalated_at_90d"].to_numpy()
    esc_model = esc_m.train_escalation(rows(x_esc, train_core_idx), y_esc[train_core_idx])
    esc_cal = ProbabilityCalibrator("isotonic").fit(
        esc_m.predict_escalation(esc_model, rows(x_esc, calib_idx)), y_esc[calib_idx]
    )
    p_esc_test = esc_cal.predict(esc_m.predict_escalation(esc_model, rows(x_esc, test_idx)))
    esc_auc = roc_auc_score(y_esc[test_idx], p_esc_test)
    esc_brier = brier_score(p_esc_test, y_esc[test_idx])

    # ----- out-of-distribution detector (BRAIN L3 metacognition) -------------
    # Lets the brain answer "is this claim familiar?" at inference. Without this
    # it would score a never-seen claim with false confidence - the exact failure
    # the abstention invariant exists to prevent.
    from sklearn.ensemble import IsolationForest

    ood = IsolationForest(
        n_estimators=200, contamination="auto", random_state=SEED, n_jobs=-1
    ).fit(rows(x_fraud, train_core_idx))
    ood_scores = ood.score_samples(rows(x_fraud, train_core_idx))
    ood_p01 = float(np.percentile(ood_scores, 1))    # unusual end of training data
    ood_p99 = float(np.percentile(ood_scores, 99))   # most typical end

    # ----- graph ring recovery ----------------------------------------------
    ring = detect_rings(df, graph_df)

    metrics = {
        "n_claims": int(len(df)),
        "n_test": int(len(test_idx)),
        "cost_mape": round(float(cost_mape), 4),
        "fraud_auc": round(float(fraud_auc), 4),
        "fraud_brier": round(float(fraud_brier), 4),
        "fraud_ece": round(float(fraud_ece), 4),
        "escalation_auc": round(float(esc_auc), 4),
        "escalation_brier": round(float(esc_brier), 4),
        "ring_recall": ring["ring_recall"],
        "n_seeded_rings": ring["n_seeded_rings"],
        "targets": {
            "cost_mape": "<0.25",
            "fraud_auc": ">0.80",
            "escalation_auc": ">0.70",
            "brier": "<0.15",
            "ring_recall": ">=0.80",
        },
    }

    feature_cols = {
        "cost": list(x_cost.columns),
        "fraud": list(x_fraud.columns),
        "escalation": list(x_esc.columns),
    }

    if save:
        constants.MODELS_DIR.mkdir(parents=True, exist_ok=True)
        constants.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump(cost_models, COST_PKL)
        joblib.dump(fraud_model, FRAUD_PKL)
        joblib.dump(fraud_cal, FRAUD_CAL_PKL)
        joblib.dump(esc_model, ESC_PKL)
        joblib.dump(esc_cal, ESC_CAL_PKL)
        joblib.dump(feature_cols, FEATURES_PKL)
        joblib.dump({"detector": ood, "features": list(x_fraud.columns),
                     "p01": ood_p01, "p99": ood_p99}, OOD_PKL)
        TRAIN_METRICS_JSON.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    return {
        "cost_models": cost_models,
        "fraud_model": fraud_model,
        "fraud_calibrator": fraud_cal,
        "escalation_model": esc_model,
        "escalation_calibrator": esc_cal,
        "feature_cols": feature_cols,
        "ood_detector": ood,
        "ood_features": list(x_fraud.columns),
        "ood_p01": ood_p01,
        "ood_p99": ood_p99,
        "metrics": metrics,
    }


def load_models() -> dict:
    """Load persisted model artifacts (raises if training hasn't run)."""
    out = {
        "cost_models": joblib.load(COST_PKL),
        "fraud_model": joblib.load(FRAUD_PKL),
        "fraud_calibrator": joblib.load(FRAUD_CAL_PKL),
        "escalation_model": joblib.load(ESC_PKL),
        "escalation_calibrator": joblib.load(ESC_CAL_PKL),
        "feature_cols": joblib.load(FEATURES_PKL),
    }
    # Optional: absent until a training run that includes the OOD step. The brain
    # reports novelty as "unavailable" rather than pretending a claim is familiar.
    if OOD_PKL.exists():
        try:
            blob = joblib.load(OOD_PKL)
            out.update({"ood_detector": blob["detector"], "ood_features": blob["features"],
                        "ood_p01": blob["p01"], "ood_p99": blob["p99"]})
        except Exception:
            pass
    return out


def score_frame(df: pd.DataFrame, models: dict | None = None,
                graph_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Score claims: calibrated fraud/escalation probs, cost band, confidence,
    coverage, graph risk. The per-claim inputs the triage engine consumes.

    ``graph_df`` lets a caller supply PRE-COMPUTED graph features. The live
    workflow does this because collusion is a property of the whole book - a
    single-row frame would always look isolated (component_size 1).
    """
    if models is None:
        models = load_models()

    rails_df = enrich_frame(df)
    if graph_df is None:
        graph_df = build_graph_features(df)
    cost_pred = cost_m.predict_cost(models["cost_models"], cost_features(df))
    p_fraud = models["fraud_calibrator"].predict(
        fraud_m.predict_fraud(
            models["fraud_model"], fraud_features(df, rails_df, graph_df, cost_pred)
        )
    )
    p_esc = models["escalation_calibrator"].predict(
        esc_m.predict_escalation(models["escalation_model"], escalation_features(df))
    )
    cov = coverage_frame(df)

    # Confidence combination (CLAUDE §7): the calibrated certainty of the signals
    # RELEVANT to the auto-clear decision, with a PENALTY for a wide cost band.
    # Fraud is the auto-clear safety signal (leakage = auto-clearing a fraud);
    # escalation RISK is governed by its own lane gate, so escalation UNCERTAINTY
    # is excluded from the confidence unless escalation_in_confidence is set.
    mp = constants.load_distributions()["model_params"]
    w = mp["cost_band_penalty_weight"]
    c_cost = cost_m.cost_certainty(cost_pred)
    c_fraud = 2.0 * np.abs(p_fraud - 0.5)
    c_esc = 2.0 * np.abs(p_esc - 0.5)
    cost_penalty = 1.0 - c_cost  # 0 = tight band, 1 = very wide
    base_conf = np.minimum(c_fraud, c_esc) if mp.get("escalation_in_confidence", True) else c_fraud
    model_confidence = base_conf * (1.0 - w * cost_penalty)

    out = pd.DataFrame(index=df.index)
    out["p_fraud"] = p_fraud
    out["p_escalation"] = p_esc
    out["cost_p10"] = cost_pred["P10"].to_numpy()
    out["cost_p50"] = cost_pred["P50"].to_numpy()
    out["cost_p90"] = cost_pred["P90"].to_numpy()
    out["c_cost"] = c_cost
    out["c_fraud"] = c_fraud
    out["c_escalation"] = c_esc
    out["model_confidence"] = model_confidence
    out["ring_risk"] = graph_df["ring_risk"].to_numpy()
    out["coverage_clear"] = cov["coverage_clear"].to_numpy()
    out["coverage_reason"] = cov["coverage_reason"].to_numpy()
    out["legal_weak_reject_flag"] = cov["legal_weak_reject_flag"].to_numpy()
    return out


def main() -> None:
    """End-to-end: generate -> train -> route -> evaluate (CLAUDE.md §4 pipeline)."""
    df = generate_claims()
    result = train_and_save_models(df)
    m = result["metrics"]
    print("[claimos-pipeline] training complete. Holdout metrics:")
    print(
        f"  cost MAPE {m['cost_mape']}  fraud AUC {m['fraud_auc']}  "
        f"esc AUC {m['escalation_auc']}  ring recall {m['ring_recall']}"
    )

    from src.evaluate import run_evaluation

    ev = run_evaluation(df, models=result)
    print("[claimos-pipeline] evaluation complete.")
    print(
        f"  Lane-1 leakage {ev['lane1_leakage_count']:.4%} "
        f"(ceiling {ev['lane1_leakage_ceiling']:.1%}) -> "
        f"{'OK' if ev['leakage_under_ceiling'] else 'BREACH'}; "
        f"touchless {ev['touchless_share']:.1%}; "
        f"TAT {ev['tat']['baseline_median_days']}d->{ev['tat']['triaged_median_days']}d"
    )
    print(f"  artifacts -> {constants.MODELS_DIR} ; reports -> {constants.REPORTS_DIR}")


if __name__ == "__main__":
    main()
