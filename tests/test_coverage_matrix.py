"""Live coverage-matrix tests (LOGIC §2)."""

from __future__ import annotations

from src.models import coverage as cov


def test_deductibles():
    assert cov.total_deductible(1200, "private_car")["compulsory"] == 1000
    assert cov.total_deductible(1600, "private_car")["compulsory"] == 2000
    assert cov.total_deductible(1600, "private_car", 5000)["total"] == 7000
    assert cov.total_deductible(125, "two_wheeler")["compulsory"] == 100
    assert cov.total_deductible(180, "two_wheeler")["compulsory"] == 200


def test_ncb_slab():
    assert cov.ncb_for(0) == 0.0
    assert cov.ncb_for(1) == 0.20
    assert cov.ncb_for(3) == 0.35
    assert cov.ncb_for(9) == 0.50


def test_waterfall_basic():
    w = cov.settlement_waterfall(claimed=40000, line_item_estimate=35000, idv=500000,
                                 vehicle_age_years=3, parts_depreciation=6000,
                                 consumables=1500, deductible_total=1000)
    assert w["is_total_loss"] is False
    assert w["assessed_cost"] == 35000          # min(claimed, estimate)
    assert w["net_payable"] == 35000 - 6000 - 1500 - 1000


def test_waterfall_zero_dep_waives_depreciation():
    base = cov.settlement_waterfall(claimed=40000, line_item_estimate=40000, idv=500000,
                                    parts_depreciation=8000, deductible_total=1000)
    zd = cov.settlement_waterfall(claimed=40000, line_item_estimate=40000, idv=500000,
                                  parts_depreciation=8000, deductible_total=1000,
                                  zero_dep_active=True, zero_dep_within_cap=True)
    assert zd["net_payable"] == base["net_payable"] + 8000


def test_waterfall_total_loss_branch():
    w = cov.settlement_waterfall(claimed=450000, line_item_estimate=450000, idv=500000,
                                 vehicle_age_years=3, deductible_total=2000)
    assert w["is_total_loss"] is True
    # IDV 500000 less 30% dep at age 3 = 350000, less 2000 deductible
    assert w["net_payable"] == 350000 - 2000


def test_advise_withdraw_below_deductible():
    a = cov.advise_withdraw(net_payable=0)
    assert a["advise_withdraw"] is True and a["reason"] == "payable_below_deductible"


def test_advise_withdraw_ncb_break_even():
    # small payout, big NCB loss -> advise withdraw
    a = cov.advise_withdraw(net_payable=2000, od_premium_next_year=20000,
                            claim_free_years=5)
    assert a["advise_withdraw"] is True and a["reason"] == "ncb_loss_exceeds_payout"
    # large payout dwarfs NCB loss -> proceed
    b = cov.advise_withdraw(net_payable=80000, od_premium_next_year=20000,
                            claim_free_years=5)
    assert b["advise_withdraw"] is False


def test_state_hard_decline_od_on_tp_only():
    r = cov.coverage_state({"product_type": "tp_only", "claim_type": "OD"})
    assert r["state"] == cov.STATE_HARD_DECLINE
    assert any("not_covered" in x for x in r["reasons"])


def test_state_hard_decline_lapsed():
    r = cov.coverage_state({"policy_status": "lapsed"})
    assert r["state"] == cov.STATE_HARD_DECLINE and r["reason"] == "policy_lapsed"


def test_state_legal_weak_late_but_valid():
    r = cov.coverage_state({"intimation_delay_hours": 120, "intimation_reason_valid": 1})
    assert r["state"] == cov.STATE_LEGAL_WEAK
    assert r["legal_weak"] is True


def test_state_engine_damage_needs_addon():
    decline = cov.coverage_state({"engine_damage": True, "add_ons": []})
    assert decline["state"] == cov.STATE_HARD_DECLINE
    ok = cov.coverage_state({"engine_damage": True, "add_ons": ["engine_protection"]})
    assert ok["state"] == cov.STATE_CLEAR


def test_state_policy_not_in_force_on_incident():
    r = cov.coverage_state({
        "policy_status": "active", "incident_date": "2026-01-01T10:00:00",
        "period_from": "2026-02-01T00:00:00", "period_to": "2027-01-31T00:00:00"})
    assert r["state"] == cov.STATE_HARD_DECLINE
    assert "policy_not_in_force_on_incident_date" in r["reasons"]


def test_state_clear():
    r = cov.coverage_state({"policy_status": "active", "product_type": "comprehensive",
                            "claim_type": "OD"})
    assert r["state"] == cov.STATE_CLEAR
