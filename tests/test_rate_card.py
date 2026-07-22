"""Rate-card estimator tests (LOGIC §1)."""

from __future__ import annotations

from src import rate_card as rc


def test_part_normalization():
    assert rc.normalize_part("left tail light") == "tail_lamp"
    assert rc.normalize_part("Front Bumper (scuffed)") == "front_bumper"
    assert rc.normalize_part("headlight") == "headlamp"
    assert rc.normalize_part("rear bumper") == "rear_bumper"
    assert rc.normalize_part("windscreen") == "windshield_front"
    assert rc.normalize_part("side mirror") == "orvm"
    assert rc.normalize_part("totally unknown widget") is None


def test_basic_estimate_positive_and_banded():
    e = rc.estimate(["front bumper", "headlight"], segment="hatchback",
                    garage_type="network", city_tier="metro")
    assert e["cost_p50"] > 0
    assert e["cost_p10"] < e["cost_p50"] < e["cost_p90"]
    assert e["n_parts"] == 2
    assert "front_bumper" in e["matched_parts"]


def test_segment_scaling_monotonic():
    parts = ["front bumper", "bonnet"]
    hatch = rc.estimate(parts, segment="hatchback")["cost_p50"]
    suv = rc.estimate(parts, segment="suv")["cost_p50"]
    lux = rc.estimate(parts, segment="luxury")["cost_p50"]
    assert hatch < suv < lux
    # SUV multiplier is 2.10 — parts+paint scale, labour does not, so ratio < 2.1
    assert 1.5 < suv / hatch < 2.1


def test_airbag_and_structural_escalate():
    e = rc.estimate(["airbag", "front bumper"])
    assert e["has_airbag"] is True
    assert e["escalate_min_lane"] == "lane2_assisted"

    s = rc.estimate(["roof panel"])
    assert s["has_structural"] is True
    assert s["escalate_min_lane"] == "lane2_assisted"


def test_engine_triggers_total_loss_check():
    e = rc.estimate(["engine assembly"])
    assert e["total_loss_trigger"] is True
    assert e["has_structural"] is True


def test_depreciation_grows_with_age():
    young = rc.estimate(["bonnet"], vehicle_age_years=0.4)["parts_depreciation"]
    old = rc.estimate(["bonnet"], vehicle_age_years=8.0)["parts_depreciation"]
    assert old > young  # metal depreciation is age-based


def test_glass_not_depreciated():
    e = rc.estimate(["windshield"], vehicle_age_years=10.0)
    # only glass part, no paint panel -> zero depreciation
    assert e["parts_depreciation"] == 0


def test_reconciliation_flags_inflation():
    e = rc.estimate(["front bumper"], garage_type="network")
    est = e["line_item_estimate"]
    ok = rc.reconciliation_flag(est * 1.05, est)
    assert ok["min_lane"] is None and not ok["inflation_flag"]

    mid = rc.reconciliation_flag(est * 1.4, est)
    assert mid["min_lane"] == "lane2_assisted"

    high = rc.reconciliation_flag(est * 2.0, est)
    assert high["min_lane"] == "lane3_investigative" and high["inflation_flag"]


def test_non_network_inflation_tell():
    e = rc.estimate(["front bumper"], garage_type="non_network")
    est = e["line_item_estimate"]
    f = rc.reconciliation_flag(est * 1.4, est, garage_type="non_network")
    assert f["non_network_tell"] is True
