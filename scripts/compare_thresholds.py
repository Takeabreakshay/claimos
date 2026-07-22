"""Compare Lane-1 max_fraud_prob settings on the real 50k book.

The brief asks for p_fraud < 0.07; our config ships 0.10. Rather than argue,
score once and re-route under each setting, then report the trade:
touchless share vs Lane-1 leakage (the hard 1.5% ceiling).

Run:  poetry run python scripts/compare_thresholds.py
"""

from __future__ import annotations

import copy
import json

import numpy as np

from src import constants
from src.data_gen import generate_claims
from src.evaluate import _lane1_leakage, _realized_tat
from src.pipeline import load_models, score_frame
from src.triage import route_frame


def main() -> None:
    df = generate_claims()
    models = load_models()
    print(f"scoring {len(df):,} claims once…")
    scored = score_frame(df, models=models)
    base = constants.load_thresholds()
    tat_base = df["baseline_tat_days"].to_numpy()

    rows = []
    for mfp in (0.10, 0.09, 0.08, 0.07, 0.06, 0.05):
        thr = copy.deepcopy(base)
        thr["lane1_touchless"]["max_fraud_prob"] = mfp
        routed = route_frame(df, scored, thr)
        out = routed["outcome"].to_numpy()
        leak_c, leak_v, n1 = _lane1_leakage(df, out)
        tat_after = _realized_tat(df, out)
        rows.append({
            "max_fraud_prob": mfp,
            "touchless_share": round(n1 / len(df), 4),
            "leakage_count": round(leak_c, 5),
            "leakage_value": round(leak_v, 5),
            "under_ceiling": leak_c <= base["guardrails"]["lane1_leakage_ceiling"],
            "within_7d": round(float((tat_after <= 7).mean()), 4),
        })

    print("\n max_fraud   touchless   leakage    value-leak  <=7d    safe")
    print(" " + "-" * 62)
    for r in rows:
        print(f"  {r['max_fraud_prob']:.2f}       {r['touchless_share']:>7.2%}   "
              f"{r['leakage_count']:>7.3%}   {r['leakage_value']:>7.3%}   "
              f"{r['within_7d']:>5.1%}   {'OK' if r['under_ceiling'] else 'BREACH'}")

    base_row = next(r for r in rows if r["max_fraud_prob"] == 0.10)
    brief_row = next(r for r in rows if r["max_fraud_prob"] == 0.07)
    d_touch = brief_row["touchless_share"] - base_row["touchless_share"]
    d_leak = brief_row["leakage_count"] - base_row["leakage_count"]
    print(f"\n 0.10 -> 0.07:  touchless {d_touch:+.2%}   leakage {d_leak:+.3%}")
    print(f" ceiling = {base['guardrails']['lane1_leakage_ceiling']:.1%}")

    (constants.REPORTS_DIR / "threshold_sweep.json").write_text(
        json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\n wrote {constants.REPORTS_DIR / 'threshold_sweep.json'}")


if __name__ == "__main__":
    main()
