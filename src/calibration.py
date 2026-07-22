"""Phase 3 — calibration layer (CLAUDE.md §3 rule 7, §7, LOGIC §3.6).

Turns raw classifier scores into TRUSTWORTHY probabilities on a held-out split.
Routing must consume calibrated probabilities, never raw scores (rule 7).

  * Isotonic (preferred at 50k rows): monotonic non-decreasing g(s) -> p.
  * Platt (fallback): p = sigmoid(a*s + b).

Quality: Brier (target < 0.15) and ECE over 10 bins; reliability curves saved
to reports/.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression


class ProbabilityCalibrator:
    """Wraps isotonic (default) or Platt calibration behind fit/predict."""

    def __init__(self, method: str = "isotonic"):
        if method not in {"isotonic", "platt"}:
            raise ValueError(f"unknown calibration method: {method}")
        self.method = method
        self._model: IsotonicRegression | LogisticRegression | None = None

    def fit(self, scores: np.ndarray, y: np.ndarray) -> ProbabilityCalibrator:
        scores = np.asarray(scores, dtype=float)
        y = np.asarray(y, dtype=int)
        if self.method == "isotonic":
            m = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            m.fit(scores, y)
        else:
            m = LogisticRegression(C=1e6, solver="lbfgs")
            m.fit(scores.reshape(-1, 1), y)
        self._model = m
        return self

    def predict(self, scores: np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("calibrator not fitted")
        scores = np.asarray(scores, dtype=float)
        if self.method == "isotonic":
            return np.clip(self._model.predict(scores), 0.0, 1.0)
        return self._model.predict_proba(scores.reshape(-1, 1))[:, 1]


def brier_score(p: np.ndarray, y: np.ndarray) -> float:
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    return float(np.mean((p - y) ** 2))


def expected_calibration_error(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> float:
    """ECE over ``n_bins`` equal-width confidence bins (LOGIC §3.6)."""
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    n = len(p)
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (p > lo) & (p <= hi) if i > 0 else (p >= lo) & (p <= hi)
        if mask.any():
            conf = p[mask].mean()
            acc = y[mask].mean()
            ece += (mask.sum() / n) * abs(acc - conf)
    return float(ece)


def reliability_curve(
    p: np.ndarray, y: np.ndarray, out_path: Path, title: str, n_bins: int = 10
) -> Path:
    """Save a reliability (calibration) curve to ``out_path``."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    centers, accs = [], []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (p > lo) & (p <= hi) if i > 0 else (p >= lo) & (p <= hi)
        if mask.any():
            centers.append(p[mask].mean())
            accs.append(y[mask].mean())

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.plot([0, 1], [0, 1], "--", color="gray", label="perfect")
    ax.plot(centers, accs, "o-", label="model")
    ax.set_xlabel("mean predicted probability")
    ax.set_ylabel("observed frequency")
    ax.set_title(title)
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return out_path
