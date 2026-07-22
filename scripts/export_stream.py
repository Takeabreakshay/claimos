"""Export stream_200.json — the pre-scored population the demo re-routes live.

Per DEMO_INTERACTIONS_BUILD_SPEC §0. Rows come from the held-out test split of a
real scoring run, so every field is a genuine model output. TAT is precomputed
per claim per lane with a fixed seed, so dragging the risk dial produces a stable
curve instead of shimmering noise.

Run:  poetry run python scripts/export_stream.py
"""

from __future__ import annotations

import json

import numpy as np
from sklearn.model_selection import train_test_split

from src import constants
from src.constants import SEED
from src.data_gen import generate_claims
from src.pipeline import load_models, score_frame

# 200 rows is enough to *cascade* in stream mode, but far too few to measure
# leakage: with only ~50 claims landing in Lane 1, a single fraud swings leakage
# by 2% and the guardrail trips on noise at the shipped defaults. The dial needs
# a population big enough for the metric to be stable, so we export 1,200 and let
# stream mode cascade the first slice.
N_EXPORT = 1200
STREAM_SLICE = 200
OUT = constants.ROOT_DIR / "web" / "data" / "stream_200.json"


def main() -> None:
    df = generate_claims()
    models = load_models()
    mp = constants.load_distributions()["model_params"]

    idx = np.arange(len(df))
    _, test_idx = train_test_split(
        idx, test_size=mp["test_size"], random_state=SEED,
        stratify=df["is_fraud"].to_numpy(),
    )
    # Score the WHOLE book, then sample. Scoring a slice in isolation recomputes
    # the collusion graph within that slice — ring members outside it disappear,
    # ring_risk collapses to zero, and fraud the graph would have caught leaks
    # into Lane 1. (Measured: 3.33% leakage sliced vs 1.195% on the full book.)
    print(f"scoring the full book ({len(df):,} claims) so graph features are intact…")
    scored_all = score_frame(df, models=models)

    rng = np.random.default_rng(SEED)
    sample = rng.choice(test_idx, size=min(N_EXPORT, len(test_idx)), replace=False)
    sub = df.iloc[sample].reset_index(drop=True)
    scored = scored_all.iloc[sample].reset_index(drop=True)

    # Precomputed TAT per lane (fixed seed => stable while dragging the dial)
    cfg = constants.load_distributions()
    tr, reim = cfg["tat_realized"], cfg["tat_ceilings"]["reimbursement_extra_days"]
    n = len(sub)
    t1 = rng.uniform(tr["lane1_days"][0], tr["lane1_days"][1], n)
    t2 = rng.uniform(tr["lane2_days"][0], tr["lane2_days"][1], n)
    non_net = (sub["garage_type"] == "non_network").to_numpy()
    t2 = t2 + np.where(non_net, rng.uniform(reim[0], reim[1], n), 0.0)
    t3 = rng.triangular(*tr["lane3_tri_days"], n)

    rows = []
    for i in range(n):
        c, s = sub.iloc[i], scored.iloc[i]
        rows.append({
            "id": c["claim_id"],
            "amount": round(float(c["claim_amount"])),
            "sev": c["incident_severity"],
            "ctype": c["claim_type"],
            "p_fraud": round(float(s["p_fraud"]), 4),
            "p_esc": round(float(s["p_escalation"]), 4),
            "conf": round(float(s["model_confidence"]), 4),
            "is_fraud": int(c["is_fraud"]),
            "coverage_clear": bool(s["coverage_clear"] == "clear"),
            "intim_ok": bool(int(c["intimation_gt_48h"]) == 0),
            "injury": int(c["injury_hint"]),
            "non_network": bool(non_net[i]),
            "settlement": round(float(c["final_settlement"])),
            "tat": [round(float(t1[i]), 3), round(float(t2[i]), 3), round(float(t3[i]), 3)],
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows), encoding="utf-8")

    # Sanity: leakage at the SHIPPED defaults must sit under the ceiling on this
    # population, otherwise the dial would show "auto-tightened" at the very
    # settings we claim are safe.
    thr = constants.load_thresholds()["lane1_touchless"]
    l1 = [r for r in rows
          if r["amount"] < thr["max_claim_amount"] and r["p_fraud"] < thr["max_fraud_prob"]
          and r["conf"] >= thr["min_confidence"] and r["p_esc"] < thr["max_escalation_prob"]
          and r["sev"] in ("minor", "moderate") and r["coverage_clear"] and r["intim_ok"]
          and not (r["p_fraud"] >= 0.50 or r["amount"] >= 200000 or r["sev"] == "total"
                   or r["p_esc"] >= 0.50 or (r["ctype"] == "TP" and r["injury"]))]
    leak = (sum(r["is_fraud"] for r in l1) / len(l1)) if l1 else 0.0
    print(f"  at shipped defaults: touchless {len(l1) / len(rows):.1%} · "
          f"leakage {leak:.3%} (ceiling 1.500%) -> {'OK' if leak <= 0.015 else 'TRIPS'}")
    print(f"  stream cascade uses the first {STREAM_SLICE} rows")

    frauds = sum(r["is_fraud"] for r in rows)
    print(f"wrote {OUT}  ({len(rows)} claims, {frauds} fraudulent)")
    print(f"  amount  : Rs{min(r['amount'] for r in rows):,} – Rs{max(r['amount'] for r in rows):,}")
    print(f"  p_fraud : {min(r['p_fraud'] for r in rows):.3f} – {max(r['p_fraud'] for r in rows):.3f}")
    print(f"  conf    : {min(r['conf'] for r in rows):.3f} – {max(r['conf'] for r in rows):.3f}")


if __name__ == "__main__":
    main()
