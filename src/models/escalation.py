"""Escalation (jumper/sleeper) classifier — LightGBM binary (CLAUDE.md §7, LOGIC §3.3).

Objective: binary log-loss on ``escalated_at_90d``. Uses ONLY FNOL-visible
features (no leakage of the hidden latent signals that generated the label), so
the task stays hard: target ROC-AUC > 0.70. Raw score is CALIBRATED before use.
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


def train_escalation(x: pd.DataFrame, y: np.ndarray) -> lgb.LGBMClassifier:
    model = lgb.LGBMClassifier(**_params())
    model.fit(x, y)
    return model


def predict_escalation(model: lgb.LGBMClassifier, x: pd.DataFrame) -> np.ndarray:
    """Raw (uncalibrated) escalation probability."""
    return model.predict_proba(x)[:, 1]
