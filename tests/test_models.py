"""Tests for Phase 3 — features, models, calibration, graph (CLAUDE.md §10).

Gate: each model trains and hits its §7 target on a holdout; artifacts persist;
coverage rules are correct; scoring invariants (LOGIC §sanity) hold.
"""

from __future__ import annotations

import numpy as np
import pytest

from src import pipeline
from src.data_gen import generate_claims
from src.models.coverage import CLEAR, FLAG, NOT_CLEAR, coverage_check, coverage_frame


# --------------------------------------------------------------------------- #
# Fixtures — train once on the full set (this IS the phase gate).
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def df():
    return generate_claims()


@pytest.fixture(scope="module")
def trained(df):
    return pipeline.train_and_save_models(df, save=False)


# --------------------------------------------------------------------------- #
# §7 target metrics (in-band, not trivial)
# --------------------------------------------------------------------------- #
def test_cost_mape_under_target(trained):
    assert trained["metrics"]["cost_mape"] < 0.25


def test_fraud_auc_in_band(trained):
    auc = trained["metrics"]["fraud_auc"]
    assert auc > 0.80, "fraud AUC below §7 target"
    assert auc < 0.95, "fraud AUC implausibly high (should be ~0.80-0.90, not trivial)"


def test_fraud_brier_under_target(trained):
    assert trained["metrics"]["fraud_brier"] < 0.15


def test_escalation_auc_over_target(trained):
    assert trained["metrics"]["escalation_auc"] > 0.70


def test_escalation_brier_under_target(trained):
    assert trained["metrics"]["escalation_brier"] < 0.15


def test_ring_recall_over_target(trained):
    assert trained["metrics"]["ring_recall"] >= 0.80


# --------------------------------------------------------------------------- #
# Coverage engine — deterministic-rule correctness (LOGIC §3.4)
# --------------------------------------------------------------------------- #
def _base_claim(**over):
    claim = {
        "policy_status": "active",
        "driver_valid_license": 1,
        "dui_flag": 0,
        "fir_required": 0,
        "fir_filed": 0,
        "modification_undeclared": 0,
        "intimation_gt_48h": 0,
        "intimation_reason_valid": 1,
    }
    claim.update(over)
    return claim


def test_coverage_lapsed_rejects():
    r = coverage_check(_base_claim(policy_status="lapsed"))
    assert r.coverage_clear == NOT_CLEAR and r.reason == "policy_lapsed"


def test_coverage_driver_ineligible():
    assert coverage_check(_base_claim(dui_flag=1)).reason == "driver_ineligible"
    assert coverage_check(_base_claim(driver_valid_license=0)).reason == "driver_ineligible"


def test_coverage_fir_missing():
    r = coverage_check(_base_claim(fir_required=1, fir_filed=0))
    assert r.coverage_clear == NOT_CLEAR and r.reason == "fir_missing"


def test_coverage_undeclared_mod_flags_not_rejects():
    r = coverage_check(_base_claim(modification_undeclared=1))
    assert r.coverage_clear == FLAG and r.reason == "undeclared_modification"


def test_coverage_clean_claim_clears():
    assert coverage_check(_base_claim()).coverage_clear == CLEAR


def test_late_intimation_with_valid_reason_sets_legal_weak_flag():
    # SC-rulings fairness rule: late-but-valid is NEVER an auto-reject on that ground.
    r = coverage_check(_base_claim(intimation_gt_48h=1, intimation_reason_valid=1))
    assert r.legal_weak_reject_flag is True
    assert r.coverage_clear == CLEAR  # not rejected for lateness alone


def test_coverage_frame_matches_scalar(df):
    sample = df.head(300)
    frame = coverage_frame(sample)
    for i in range(0, len(sample), 37):
        row = sample.iloc[i].to_dict()
        scalar = coverage_check(row)
        assert frame.iloc[i]["coverage_clear"] == scalar.coverage_clear
        assert frame.iloc[i]["coverage_reason"] == scalar.reason


# --------------------------------------------------------------------------- #
# Scoring invariants (LOGIC sanity) + persistence
# --------------------------------------------------------------------------- #
def test_score_frame_invariants(df, trained):
    scored = pipeline.score_frame(df.head(800), models=trained)
    assert (
        not scored[["p_fraud", "p_escalation", "cost_p50", "model_confidence"]].isna().any().any()
    )
    assert scored["p_fraud"].between(0, 1).all()
    assert scored["p_escalation"].between(0, 1).all()
    # P10 <= P50 <= P90 for every claim.
    assert (scored["cost_p10"] <= scored["cost_p50"] + 1e-6).all()
    assert (scored["cost_p50"] <= scored["cost_p90"] + 1e-6).all()
    assert scored["model_confidence"].between(0, 1).all()


def test_artifacts_persisted_and_loadable():
    # pipeline.main() (run in Phase 3) saved artifacts; load must round-trip.
    models = pipeline.load_models()
    for key in (
        "cost_models",
        "fraud_model",
        "fraud_calibrator",
        "escalation_model",
        "escalation_calibrator",
    ):
        assert key in models and models[key] is not None
    assert np.isfinite(0.0)  # sanity
