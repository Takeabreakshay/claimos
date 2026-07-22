"""L7 §6 — the envelope-widening backtest.

Sweeps candidate Lane-1 gates and records, for each, the touchless share it would
buy and the leakage it would cost — measured ONLY on held-out claims. The
adopted point is the one that automates the most while staying under the ceiling
with margin.

This produces the real safety frontier, so the demo can show the trade-off with
measured numbers instead of an illustrative "28% -> 34% -> 41%".

Run:  poetry run python scripts/envelope_backtest.py
"""

from __future__ import annotations

import copy
import json

import numpy as np

from src import constants
from src.data_gen import generate_claims
from src.evaluate import _holdout_mask, _lane1_leakage
from src.pipeline import load_models, score_frame
from src.triage import route_frame

OUT = constants.ROOT_DIR / "web" / "data" / "envelope.json"


def main() -> None:
    df = generate_claims()
    models = load_models()
    print(f"scoring {len(df):,} claims…")
    scored = score_frame(df, models=models)

    base = constants.load_thresholds()
    ceiling = base["guardrails"]["lane1_leakage_ceiling"]
    margin = 0.8 * ceiling
    hold = _holdout_mask(df)

    frontier = []
    for conf in np.arange(0.80, 0.961, 0.02):
        for mf in (0.01, 0.02, 0.03, 0.05, 0.07, 0.10):
            thr = copy.deepcopy(base)
            thr["lane1_touchless"]["min_confidence"] = round(float(conf), 3)
            thr["lane1_touchless"]["max_fraud_prob"] = mf
            out = route_frame(df, scored, thr)["outcome"].to_numpy()
            leak, _, n1h = _lane1_leakage(df, out, hold)
            share = float((out == "lane1_touchless").mean())
            frontier.append({
                "min_conf": round(float(conf), 3), "max_fraud": mf,
                "touchless": round(share, 4), "leakage": round(leak, 5),
                "n_lane1_holdout": int(n1h),
                "safe": bool(leak <= margin),
            })

    safe = [f for f in frontier if f["safe"] and f["n_lane1_holdout"] >= 30]
    adopted = max(safe, key=lambda f: f["touchless"]) if safe else None

    payload = {
        "ceiling": ceiling, "margin": margin,
        "measured_on": "held-out test rows only",
        "frontier": frontier,
        "adopted": adopted,
        "note": ("Each point is a candidate Lane-1 gate backtested on claims the "
                 "models never saw. A candidate is adopted only if projected "
                 "leakage stays under the ceiling with margin — automation is "
                 "earned by evidence, never assumed."),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload), encoding="utf-8")

    print(f"\n candidates: {len(frontier)}  ·  safe: {len(safe)}")
    if adopted:
        print(f" adopted: conf {adopted['min_conf']} / fraud {adopted['max_fraud']}"
              f"  -> touchless {adopted['touchless']:.1%}, leakage {adopted['leakage']:.3%}")
    print("\n best touchless at each leakage budget (held-out):")
    for cap in (0.005, 0.010, 0.012, 0.015, 0.020, 0.030):
        ok = [f for f in frontier if f["leakage"] <= cap and f["n_lane1_holdout"] >= 30]
        if ok:
            b = max(ok, key=lambda f: f["touchless"])
            mark = "  <- ceiling" if abs(cap - 0.015) < 1e-9 else ""
            print(f"   leakage <= {cap:.1%} -> touchless {b['touchless']:>6.1%} "
                  f"(conf {b['min_conf']}, fraud {b['max_fraud']}){mark}")
    print(f"\n wrote {OUT}")


if __name__ == "__main__":
    main()
