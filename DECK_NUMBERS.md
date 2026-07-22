# DECK_NUMBERS.md — the only figures we quote

**Generated from `reports/eval.json` on 50,000 synthetic claims.**
Do not quote a number that is not on this page. If a brief or slide disagrees
with this file, this file is right — a judge can open the repo and check.

## The three closing numbers
| Metric | Measured | Note |
|---|---|---|
| Touchless (STP) share | **16.1%** | bounded by the leakage ceiling, never forced |
| Claims settled ≤ 7 days | **1.7% → 47.8%** | the sample-stable TAT headline |
| Lane-1 leakage | **1.347%** | vs the **1.5%** hard ceiling — HOLDING |

## Model performance (holdout)
| Model | Metric | Measured | Target |
|---|---|---|---|
| Fraud | ROC-AUC | **0.8689** | > 0.80 |
| Fraud | Brier | **0.0646** | < 0.15 |
| Escalation | ROC-AUC | **0.735** | > 0.70 |
| Cost | MAPE | **0.1086** | < 0.25 |
| Collusion graph | ring recall | **1.0** | ≥ 0.80 |

> Full-book fraud AUC reads 0.9322 because it includes rows the model
> trained on. **Quote the holdout (0.8689)** — it is the honest number.

## TAT
| | Baseline (all-manual) | Triaged |
|---|---|---|
| Median | 16.35 d | 17.92 d |
| Mean | 30.08 d | 29.23 d |
| Settled ≤ 7 days | 1.7% | 47.8% |

**Lead with "settled ≤7 days".** Median sits on the reimbursement cliff (~40% of
claims carry the +15–30d route in both worlds) and swings with sample size; the
≤7-day share is stable and is the honest description of what triage changes —
low-risk claims get fast, Lane 3 stays deliberately thorough.

## Fairness
- Legally-weak rejections routed to a human instead of auto-declined:
  **90%** of 3,633 such claims.

## Lane mix
- lane2_assisted: 23,310 (46.6%)
- lane1_touchless: 8,035 (16.1%)
- coverage_reject: 6,960 (13.9%)
- retake: 6,816 (13.6%)
- lane3_investigative: 4,879 (9.8%)

## Shipped Lane-1 thresholds
- max_claim_amount: ₹50,000 (IRDAI surveyor seam — the anchor)
- max_fraud_prob: 0.07
- min_confidence: 0.8
- auto-tighten steps applied this run: 133

## What we do NOT claim
- No real Bajaj data, no real PII — 100% synthetic, calibrated to IRDAI distributions.
- VAHAN / DigiLocker / IIB PRISM / IIB QUEST are **mocked adapters**; they are
  regulator-gated and cannot be obtained by a third party. Going live is a
  credential swap at `src/rails.py`, not a rebuild.
- Model metrics are on synthetic labels. Real performance requires Bajaj's
  historical resolved claims — that is the one input only they can supply.
