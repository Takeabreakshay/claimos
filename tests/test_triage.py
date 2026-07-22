"""Tests for Phase 4 — triage routing policy (CLAUDE.md §10).

Gate: hand-crafted claims land in expected lanes —
  clean <Rs50k -> Lane 1; fraud-ring claim -> Lane 3; lapsed policy -> coverage
  reject; valid-reason late-intimation -> Lane 2 (not reject); low confidence ->
  retake. Plus a full-frame integration route on real scored data.
"""

from __future__ import annotations

import pytest

from src.constants import Lane
from src.models.coverage import CLEAR, NOT_CLEAR
from src.triage import COVERAGE_REJECT, RETAKE, route_claim, route_frame


def _clean_scored(**over):
    """A clean, low-risk, high-confidence <Rs50k scored record -> Lane 1."""
    rec = {
        "claim_amount": 20000.0,
        "incident_severity": "minor",
        "claim_type": "OD",
        "injury_hint": 0,
        "intimation_gt_48h": 0,
        "p_fraud": 0.02,
        "p_escalation": 0.03,
        "model_confidence": 0.95,
        "coverage_clear": CLEAR,
        "coverage_reason": "none",
        "legal_weak_reject_flag": 0,
    }
    rec.update(over)
    return rec


# --------------------------------------------------------------------------- #
# Hand-crafted routing (the wedge in isolation)
# --------------------------------------------------------------------------- #
def test_clean_small_claim_routes_lane1():
    d = route_claim(_clean_scored())
    assert d.outcome == Lane.TOUCHLESS.value


def test_fraud_ring_claim_routes_lane3():
    d = route_claim(_clean_scored(p_fraud=0.85))
    assert d.outcome == Lane.INVESTIGATIVE.value
    assert any("fraud_prob" in r for r in d.reasons)


def test_high_value_routes_lane3():
    d = route_claim(_clean_scored(claim_amount=350000.0, model_confidence=0.95))
    assert d.outcome == Lane.INVESTIGATIVE.value


def test_total_loss_routes_lane3():
    d = route_claim(_clean_scored(claim_type="theft_total", incident_severity="total"))
    assert d.outcome == Lane.INVESTIGATIVE.value


def test_high_escalation_routes_lane3():
    d = route_claim(_clean_scored(p_escalation=0.66))
    assert d.outcome == Lane.INVESTIGATIVE.value


def test_lapsed_policy_routes_coverage_reject():
    d = route_claim(_clean_scored(coverage_clear=NOT_CLEAR, coverage_reason="policy_lapsed"))
    assert d.outcome == COVERAGE_REJECT
    assert "coverage:policy_lapsed" in d.reasons


def test_late_intimation_valid_reason_routes_lane2_not_reject():
    d = route_claim(_clean_scored(intimation_gt_48h=1, legal_weak_reject_flag=1))
    assert d.outcome == Lane.ASSISTED.value  # Lane 2, NOT a reject
    assert d.legal_check is True


def test_low_confidence_routes_retake():
    d = route_claim(_clean_scored(model_confidence=0.40))
    assert d.outcome == RETAKE
    assert d.retake_requested is True


def test_borderline_amount_over_50k_not_lane1():
    # The Rs50k anchor: exactly at/above the seam is never Lane 1.
    d = route_claim(_clean_scored(claim_amount=50000.0))
    assert d.outcome != Lane.TOUCHLESS.value


def test_moderate_fraud_medium_conf_routes_lane2():
    # Not a Lane-3 trigger, fails a Lane-1 condition -> default Assisted.
    d = route_claim(_clean_scored(p_fraud=0.20, model_confidence=0.80))
    assert d.outcome == Lane.ASSISTED.value


# --------------------------------------------------------------------------- #
# Full-frame integration (real scored data)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def routed_frame():
    from src.data_gen import generate_claims
    from src.pipeline import score_frame, train_and_save_models

    df = generate_claims(n=8000, seed=42)
    trained = train_and_save_models(df, save=False)
    scored = score_frame(df, models=trained)
    return df, route_frame(df, scored)


def test_route_frame_covers_all_rows(routed_frame):
    df, routed = routed_frame
    assert len(routed) == len(df)
    assert routed["outcome"].notna().all()


def test_route_frame_outcomes_are_valid(routed_frame):
    _, routed = routed_frame
    valid = {
        Lane.TOUCHLESS.value,
        Lane.ASSISTED.value,
        Lane.INVESTIGATIVE.value,
        RETAKE,
        COVERAGE_REJECT,
    }
    assert set(routed["outcome"].unique()) <= valid


def test_no_lane1_claim_breaches_anchor_or_severity(routed_frame):
    df, routed = routed_frame
    lane1 = df[routed["outcome"] == Lane.TOUCHLESS.value]
    # Sanity invariants (LOGIC): no Lane-1 claim >= Rs50k or severe/total.
    assert (lane1["claim_amount"] < 50000).all()
    assert lane1["incident_severity"].isin(["minor", "moderate"]).all()
    assert (lane1["policy_status"] != "lapsed").all()
