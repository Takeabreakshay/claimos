"""Tests for Phase 2 — mocked rails (CLAUDE.md §10).

Gate: each rail returns typed, seeded output, is deterministic (keyed by claim),
and carries a ``# PRODUCTION: replace with real <X> API`` swap comment.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src import rails
from src.data_gen import generate_claims
from src.rails import (
    ClaimHistory,
    LicenseVerification,
    PrismScore,
    VehicleVerification,
    enrich_claim,
    enrich_frame,
    get_claim_history,
    get_prism_score,
    verify_license,
    verify_vehicle,
)


@pytest.fixture(scope="module")
def sample():
    # A small deterministic slice of real generated claims.
    return generate_claims(n=200, seed=42)


def test_rails_return_typed_output(sample):
    claim = sample.iloc[0].to_dict()
    assert isinstance(verify_vehicle(claim), VehicleVerification)
    assert isinstance(verify_license(claim), LicenseVerification)
    assert isinstance(get_claim_history(claim), ClaimHistory)
    assert isinstance(get_prism_score(claim), PrismScore)


def test_rail_field_types_and_ranges(sample):
    claim = sample.iloc[0].to_dict()
    v = verify_vehicle(claim)
    assert isinstance(v.registration_valid, bool)
    assert v.rc_status in {"active", "expired"}
    assert isinstance(v.engine_chassis_match, bool)

    lic = verify_license(claim)
    assert isinstance(lic.license_valid, bool)
    assert lic.dl_status in {"active", "suspended", "expired"}

    h = get_claim_history(claim)
    assert isinstance(h.prior_claims_3y, int) and h.prior_claims_3y >= 0
    assert 0 <= h.prior_fraud_flags <= h.prior_claims_3y
    assert h.days_since_last_claim == -1 or h.days_since_last_claim >= 30

    p = get_prism_score(claim)
    assert 0.0 <= p.prism_score <= 1.0
    assert 0 <= p.prism_percentile <= 100
    assert isinstance(p.quest_hit, bool)


def test_rails_deterministic_keyed_by_claim(sample):
    claim = sample.iloc[7].to_dict()
    # Same claim -> identical output on repeat calls (rule 4).
    assert verify_vehicle(claim) == verify_vehicle(claim)
    assert verify_license(claim) == verify_license(claim)
    assert get_claim_history(claim) == get_claim_history(claim)
    assert get_prism_score(claim) == get_prism_score(claim)


def test_different_claims_differ(sample):
    # Two different claim_ids should (almost surely) map to different PRISM scores.
    a = get_prism_score(sample.iloc[1].to_dict())
    b = get_prism_score(sample.iloc[2].to_dict())
    assert a.claim_id != b.claim_id
    assert a != b


def test_enrich_frame_shape_and_determinism(sample):
    e1 = enrich_frame(sample)
    e2 = enrich_frame(sample)
    assert len(e1) == len(sample)
    assert e1.index.equals(sample.index)
    # No NaNs in rail features (sentinels used instead).
    assert not e1.isna().any().any()
    # Reproducible.
    import pandas as pd

    pd.testing.assert_frame_equal(e1, e2)


def test_enrich_claim_has_all_rail_fields(sample):
    out = enrich_claim(sample.iloc[0].to_dict())
    for key in (
        "rail_registration_valid",
        "rail_engine_chassis_match",
        "rail_license_valid",
        "rail_prior_claims_3y",
        "rail_prism_score",
        "rail_quest_hit",
    ):
        assert key in out


def test_each_rail_has_production_swap_comment():
    # The swap points must be explicit in source (CLAUDE.md §10 Phase 2 gate).
    src = Path(rails.__file__).read_text(encoding="utf-8")
    for system in ("VAHAN", "DigiLocker", "policy/claims DB", "PRISM"):
        assert "# PRODUCTION:" in src and system in src, f"missing PRODUCTION swap for {system}"
    assert src.count("# PRODUCTION:") >= 4
