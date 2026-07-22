"""Tests for Phase 1 — synthetic data generator (CLAUDE.md §10).

Phase-1 acceptance gate:
  * 50k rows
  * no NaNs in required fields
  * <Rs50k share in 55-68%
  * fraud rates within +/-1pt of config (per type)
  * seed-reproducible (two runs identical)

Also carries the Phase-0 scaffold-sanity checks so the config/anchor stay pinned.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src import constants
from src.data_gen import REQUIRED_FIELDS, generate_claims


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def cfg() -> dict:
    return constants.load_distributions()


@pytest.fixture(scope="module")
def df(cfg) -> pd.DataFrame:
    # Full config-sized generation (the gate is defined on N = 50k).
    return generate_claims()


# --------------------------------------------------------------------------- #
# Phase 0 — scaffold sanity (config + anchor pinned)
# --------------------------------------------------------------------------- #
def test_seed_is_42():
    assert constants.SEED == 42


def test_configs_load(cfg):
    thr = constants.load_thresholds()
    assert cfg["seed"] == 42
    assert cfg["n_claims"] == 50000
    assert thr["lane1_touchless"]["max_claim_amount"] == 50000  # Rs50k anchor
    assert thr["guardrails"]["lane1_leakage_ceiling"] == 0.015


def test_lane_vocabulary_fixed():
    assert {lane.value for lane in constants.Lane} == {
        "lane1_touchless",
        "lane2_assisted",
        "lane3_investigative",
    }


# --------------------------------------------------------------------------- #
# Phase 1 — data-generator acceptance
# --------------------------------------------------------------------------- #
def test_row_count(df, cfg):
    assert len(df) == cfg["n_claims"] == 50000


def test_no_nans_in_required_fields(df):
    missing = {c: int(df[c].isna().sum()) for c in REQUIRED_FIELDS if df[c].isna().any()}
    assert not missing, f"NaNs in required fields: {missing}"


def test_sub_50k_share_in_band(df, cfg):
    lo, hi = cfg["acceptance"]["sub_50k_share"]
    share = float((df["claim_amount"] < 50000).mean())
    assert lo <= share <= hi, f"sub-50k share {share:.4f} outside [{lo}, {hi}]"


def test_fraud_rates_within_1pt(df, cfg):
    for ctype, target in cfg["fraud_base_rate"].items():
        rate = float(df.loc[df["claim_type"] == ctype, "is_fraud"].mean())
        assert abs(rate - target) <= 0.01, f"fraud[{ctype}]={rate:.4f} vs {target} (>1pt off)"
    overall = float(df["is_fraud"].mean())
    assert 0.09 <= overall <= 0.15, f"overall fraud {overall:.4f} outside directional 9-15% band"


def test_seed_reproducible():
    # Determinism holds at any N; use a small N so the check is fast.
    a = generate_claims(n=3000, seed=constants.SEED)
    b = generate_claims(n=3000, seed=constants.SEED)
    pd.testing.assert_frame_equal(a, b)


# --------------------------------------------------------------------------- #
# Phase 1 — structural invariants (from LOGIC_AND_FORMULAS.md §1)
# --------------------------------------------------------------------------- #
def test_probabilities_and_amounts_valid(df):
    assert (df["claim_amount"] > 0).all()
    assert (df["idv"] > 0).all()
    assert (df["true_repair_cost"] > 0).all()
    assert df["photo_quality_score"].between(0, 1).all()
    assert set(df["is_fraud"].unique()) <= {0, 1}
    assert set(df["escalated_at_90d"].unique()) <= {0, 1}


def test_theft_total_is_total_severity(df):
    theft = df[df["claim_type"] == "theft_total"]
    assert (theft["incident_severity"] == "total").all()


def test_fraud_type_consistency(df):
    assert (df.loc[df["is_fraud"] == 0, "fraud_type"] == "none").all()
    assert (df.loc[df["is_fraud"] == 1, "fraud_type"] != "none").all()


def test_rings_seeded_and_are_fraud(df, cfg):
    n_rings = int(df["ring_id"].max()) + 1
    lo, hi = cfg["rings"]["n_rings"]
    assert lo <= n_rings <= hi, f"{n_rings} rings outside [{lo}, {hi}]"
    # Every ring member is a fraud claim (LOGIC §1.7).
    assert (df.loc[df["is_ring_claim"] == 1, "is_fraud"] == 1).all()


def test_rejected_claims_have_zero_settlement(df):
    assert (df.loc[df["surveyor_verdict"] == "reject", "final_settlement"] == 0).all()
