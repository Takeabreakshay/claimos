"""Tests for Phase 5 (explainability) + Phase 6 (evaluation) — CLAUDE.md §10.

Phase 6 gate: leakage < 1.5% (after auto-tighten), all §9 metrics computed,
reports/eval.json produced, baseline-vs-triaged TAT drop shown.
Phase 5 gate: every routed claim returns non-empty reason codes + lane rationale.
"""

from __future__ import annotations

import json

import pytest

from src import constants
from src.data_gen import generate_claims
from src.evaluate import run_evaluation
from src.explain import Explainer, explain_frame
from src.pipeline import score_frame, train_and_save_models


@pytest.fixture(scope="module")
def bundle():
    df = generate_claims(n=12000, seed=42)
    models = train_and_save_models(df, save=False)
    scored = score_frame(df, models=models)
    return df, models, scored


# --------------------------------------------------------------------------- #
# Phase 6 — evaluation
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def eval_metrics(bundle):
    df, models, _ = bundle
    return run_evaluation(df, models=models)


def test_leakage_under_hard_ceiling(eval_metrics):
    assert eval_metrics["leakage_under_ceiling"] is True
    assert eval_metrics["lane1_leakage_count"] <= eval_metrics["lane1_leakage_ceiling"]


def test_all_headline_metrics_present(eval_metrics):
    for key in ("fraud", "escalation", "cost", "tat", "appeal_rate", "lane_mix"):
        assert key in eval_metrics
    assert eval_metrics["fraud"]["auc"] > 0.80
    assert eval_metrics["cost"]["mape"] < 0.25


def test_tat_speeds_up_low_risk_claims(eval_metrics):
    tat = eval_metrics["tat"]
    # The honest, sample-stable headline: many more claims now settle within 7 days
    # (low-risk fast lanes), while Lane 3 stays deliberately slow (§9). Median/mean
    # sit on the reimbursement + litigation-tail cliff, so we don't gate on them.
    assert tat["pct_within_7d_triaged"] > tat["pct_within_7d_baseline"] + 0.15


def test_eval_json_written():
    path = constants.REPORTS_DIR / "eval.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "lane1_leakage_count" in data


def test_touchless_share_reasonable(eval_metrics):
    # Report-only, but sanity: some claims are auto-settled and not all.
    assert 0.01 < eval_metrics["touchless_share"] < 0.90


# --------------------------------------------------------------------------- #
# Phase 5 — explainability
# --------------------------------------------------------------------------- #
def test_explain_frame_every_claim_has_reason(bundle):
    df, models, scored = bundle
    ex = explain_frame(df.head(500), scored.head(500), models)
    assert len(ex) == 500
    assert ex["primary_reason"].astype(str).str.len().gt(0).all()
    assert ex["lane_label"].notna().all()
    assert ex["outcome"].notna().all()


def test_explain_claim_returns_full_explanation(bundle):
    df, models, scored = bundle
    from src.rails import enrich_claim

    expl = Explainer(models)
    raw = df.iloc[0].to_dict()
    e = expl.explain_claim(raw, scored.iloc[0].to_dict(), rails_row=enrich_claim(raw))
    assert e.lane_label
    assert len(e.lane_reasons) > 0
    assert len(e.cost_drivers) > 0
    assert len(e.escalation_drivers) > 0
    assert len(e.plain_reason) > 0
