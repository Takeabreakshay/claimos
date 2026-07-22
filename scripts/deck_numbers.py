"""Emit DECK_NUMBERS.md straight from the evaluation artifacts.

Every figure quoted in the pitch must come from here — never from a brief, a
slide, or memory. If a number isn't in this file, we didn't measure it and it
does not go on a slide (CLAUDE.md §3 rule 3).

Run:  poetry run python scripts/deck_numbers.py
"""

from __future__ import annotations

import json

from src import constants

EVAL = constants.REPORTS_DIR / "eval.json"
TRAIN = constants.REPORTS_DIR / "train_metrics.json"
OUT = constants.ROOT_DIR / "DECK_NUMBERS.md"


def main() -> None:
    if not EVAL.exists():
        raise SystemExit("reports/eval.json missing — run: poetry run claimos-evaluate")
    e = json.loads(EVAL.read_text(encoding="utf-8"))
    t = json.loads(TRAIN.read_text(encoding="utf-8")) if TRAIN.exists() else {}
    tat = e["tat"]
    thr = constants.load_thresholds()["lane1_touchless"]

    md = f"""# DECK_NUMBERS.md — the only figures we quote

**Generated from `reports/eval.json` on {e['n_claims']:,} synthetic claims.**
Do not quote a number that is not on this page. If a brief or slide disagrees
with this file, this file is right — a judge can open the repo and check.

## The three closing numbers
| Metric | Measured | Note |
|---|---|---|
| Touchless (STP) share | **{e['touchless_share']:.1%}** | bounded by the leakage ceiling, never forced |
| Claims settled ≤ 7 days | **{tat['pct_within_7d_baseline']:.1%} → {tat['pct_within_7d_triaged']:.1%}** | the sample-stable TAT headline |
| Lane-1 leakage | **{e['lane1_leakage_count']:.3%}** | vs the **{e['lane1_leakage_ceiling']:.1%}** hard ceiling — {'HOLDING' if e['leakage_under_ceiling'] else 'BREACH'} |

## Model performance (holdout)
| Model | Metric | Measured | Target |
|---|---|---|---|
| Fraud | ROC-AUC | **{t.get('fraud_auc', '—')}** | > 0.80 |
| Fraud | Brier | **{t.get('fraud_brier', '—')}** | < 0.15 |
| Escalation | ROC-AUC | **{t.get('escalation_auc', '—')}** | > 0.70 |
| Cost | MAPE | **{t.get('cost_mape', '—')}** | < 0.25 |
| Collusion graph | ring recall | **{t.get('ring_recall', '—')}** | ≥ 0.80 |

> Full-book fraud AUC reads {e['fraud']['auc']} because it includes rows the model
> trained on. **Quote the holdout ({t.get('fraud_auc', '—')})** — it is the honest number.

## TAT
| | Baseline (all-manual) | Triaged |
|---|---|---|
| Median | {tat['baseline_median_days']} d | {tat['triaged_median_days']} d |
| Mean | {tat['baseline_mean_days']} d | {tat['triaged_mean_days']} d |
| Settled ≤ 7 days | {tat['pct_within_7d_baseline']:.1%} | {tat['pct_within_7d_triaged']:.1%} |

**Lead with "settled ≤7 days".** Median sits on the reimbursement cliff (~40% of
claims carry the +15–30d route in both worlds) and swings with sample size; the
≤7-day share is stable and is the honest description of what triage changes —
low-risk claims get fast, Lane 3 stays deliberately thorough.

## Fairness
- Legally-weak rejections routed to a human instead of auto-declined:
  **{e['appeal_rate']['appeals_avoided_rate']:.0%}** of {e['appeal_rate']['legally_weak_claims']:,} such claims.

## Lane mix
{chr(10).join(f"- {k}: {v:,} ({v / e['n_claims']:.1%})" for k, v in e['lane_mix'].items())}

## Shipped Lane-1 thresholds
- max_claim_amount: ₹{thr['max_claim_amount']:,} (IRDAI surveyor seam — the anchor)
- max_fraud_prob: {thr['max_fraud_prob']}
- min_confidence: {thr['min_confidence']}
- auto-tighten steps applied this run: {e['auto_tighten_steps']}

## What we do NOT claim
- No real Bajaj data, no real PII — 100% synthetic, calibrated to IRDAI distributions.
- VAHAN / DigiLocker / IIB PRISM / IIB QUEST are **mocked adapters**; they are
  regulator-gated and cannot be obtained by a third party. Going live is a
  credential swap at `src/rails.py`, not a rebuild.
- Model metrics are on synthetic labels. Real performance requires Bajaj's
  historical resolved claims — that is the one input only they can supply.
"""
    OUT.write_text(md, encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"  touchless {e['touchless_share']:.1%} | leakage {e['lane1_leakage_count']:.3%} "
          f"| <=7d {tat['pct_within_7d_baseline']:.1%} -> {tat['pct_within_7d_triaged']:.1%}")


if __name__ == "__main__":
    main()
