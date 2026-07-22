"""Repair-cost estimator — LightGBM quantile regression (CLAUDE.md §7, LOGIC §3.1).

Three boosters at tau in {0.10, 0.50, 0.90} trained with the pinball loss. P50 is
the point estimate; P10/P90 the uncertainty band (sorted so P10<=P50<=P90).
Target: MAPE(P50) < 0.25.

Cost certainty (used by the triage confidence combination, LOGIC §3.1):
  c_cost = 1 - clip((P90 - P10) / (2 * P50), 0, 1)
"""

from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd

from src import constants


def _params(alpha: float) -> dict:
    mp = constants.load_distributions()["model_params"]["common"]
    return {
        "objective": "quantile",
        "alpha": alpha,
        "learning_rate": mp["learning_rate"],
        "num_leaves": mp["num_leaves"],
        "min_child_samples": mp["min_child_samples"],
        "subsample": mp["subsample"],
        "colsample_bytree": mp["colsample_bytree"],
        "n_estimators": mp["n_estimators"],
        "random_state": mp["random_state"],
        "n_jobs": -1,
        "verbosity": -1,
    }


def train_cost(x: pd.DataFrame, y: np.ndarray) -> dict[str, lgb.LGBMRegressor]:
    """Train the P10/P50/P90 quantile boosters."""
    quantiles = constants.load_distributions()["model_params"]["cost_quantiles"]
    models: dict[str, lgb.LGBMRegressor] = {}
    for q in quantiles:
        key = f"P{int(q * 100)}"
        model = lgb.LGBMRegressor(**_params(q))
        model.fit(x, y)
        models[key] = model
    return models


def predict_cost(models: dict[str, lgb.LGBMRegressor], x: pd.DataFrame) -> pd.DataFrame:
    """Predict P10/P50/P90, enforcing monotonicity (P10<=P50<=P90)."""
    preds = np.column_stack(
        [models["P10"].predict(x), models["P50"].predict(x), models["P90"].predict(x)]
    )
    preds = np.sort(preds, axis=1)  # guarantee ordering per row
    preds = np.clip(preds, 1.0, None)
    return pd.DataFrame(preds, columns=["P10", "P50", "P90"], index=x.index)


def cost_certainty(cost_pred: pd.DataFrame) -> np.ndarray:
    """c_cost in [0,1]; tight band => high certainty (LOGIC §3.1)."""
    p10 = cost_pred["P10"].to_numpy()
    p50 = np.clip(cost_pred["P50"].to_numpy(), 1.0, None)
    p90 = cost_pred["P90"].to_numpy()
    return 1.0 - np.clip((p90 - p10) / (2.0 * p50), 0.0, 1.0)


def mape(y_true: np.ndarray, y_pred: np.ndarray, eps: float = 1.0) -> float:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    return float(np.mean(np.abs(y_true - y_pred) / np.maximum(np.abs(y_true), eps)))
