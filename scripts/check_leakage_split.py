"""Is our headline leakage measured on data the models trained on?

reports/eval.json computes Lane-1 leakage across the whole book. If the models
saw most of those rows in training, that number is optimistically biased and the
honest out-of-sample figure is worse. This splits it.

Run:  poetry run python scripts/check_leakage_split.py
"""

from __future__ import annotations

import numpy as np
from sklearn.model_selection import train_test_split

from src import constants
from src.constants import SEED
from src.data_gen import generate_claims
from src.pipeline import load_models, score_frame
from src.triage import route_frame


def leak(df, routed, mask) -> tuple[float, int, int]:
    lane1 = (routed["outcome"].to_numpy() == "lane1_touchless") & mask
    n1 = int(lane1.sum())
    if not n1:
        return 0.0, 0, 0
    frauds = int((lane1 & (df["is_fraud"].to_numpy() == 1)).sum())
    return frauds / n1, frauds, n1


def main() -> None:
    df = generate_claims()
    models = load_models()
    mp = constants.load_distributions()["model_params"]

    print(f"scoring {len(df):,} claims…")
    scored = score_frame(df, models=models)
    routed = route_frame(df, scored)

    idx = np.arange(len(df))
    train_idx, test_idx = train_test_split(
        idx, test_size=mp["test_size"], random_state=SEED,
        stratify=df["is_fraud"].to_numpy())
    train_core_idx, calib_idx = train_test_split(
        train_idx, test_size=mp["calibration_size"], random_state=SEED,
        stratify=df["is_fraud"].to_numpy()[train_idx])

    masks = {
        "WHOLE BOOK (what eval.json reports)": np.ones(len(df), bool),
        "train rows (models memorised these)": np.isin(idx, train_core_idx),
        "calibration rows": np.isin(idx, calib_idx),
        "HELD-OUT test rows (the honest number)": np.isin(idx, test_idx),
    }
    ceiling = constants.load_thresholds()["guardrails"]["lane1_leakage_ceiling"]

    print(f"\n{'segment':<40} {'leakage':>9}  {'fraud/lane1':>13}   vs {ceiling:.1%} ceiling")
    print("-" * 84)
    for label, m in masks.items():
        rate, f, n1 = leak(df, routed, m)
        verdict = "OK" if rate <= ceiling else "BREACH"
        print(f"{label:<40} {rate:>8.3%}  {f:>5}/{n1:<7}   {verdict}")

    rate_all, _, _ = leak(df, routed, masks["WHOLE BOOK (what eval.json reports)"])
    rate_test, _, _ = leak(df, routed, masks["HELD-OUT test rows (the honest number)"])
    print(f"\noptimism from reporting on training data: "
          f"{rate_all:.3%} -> {rate_test:.3%}  ({rate_test - rate_all:+.3%})")


if __name__ == "__main__":
    main()
