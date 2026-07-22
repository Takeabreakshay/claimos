"""Fraud classifier — LightGBM binary + graph features (CLAUDE.md §7, LOGIC §3.2).

Objective: binary log-loss. Uses observable fields + rails + graph features +
the claim-vs-predicted-cost ratio. Raw score is CALIBRATED (calibration.py)
before use in routing. Target: ROC-AUC > 0.80 (learnable-not-trivial).
"""

from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd

from src import constants


def _params() -> dict:
    mp = constants.load_distributions()["model_params"]["common"]
    return {
        "objective": "binary",
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


def train_fraud(x: pd.DataFrame, y: np.ndarray) -> lgb.LGBMClassifier:
    model = lgb.LGBMClassifier(**_params())
    model.fit(x, y)
    return model


def predict_fraud(model: lgb.LGBMClassifier, x: pd.DataFrame) -> np.ndarray:
    """Raw (uncalibrated) fraud probability."""
    return model.predict_proba(x)[:, 1]
