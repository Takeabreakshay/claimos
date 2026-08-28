"""Coverage / eligibility engine - deterministic rules (CLAUDE.md §7, LOGIC §3.4).

Pure boolean logic (no ML), unit-tested. Precedence (LOGIC §3.4):
  policy_lapsed -> driver_ineligible -> fir_missing -> undeclared_modification.

CRUCIAL fairness rule (SOURCED, SC rulings): late intimation ALONE is NOT a valid
reject. When intimation is late but ``intimation_reason_valid``, we raise a
``legal_weak_reject_flag`` - the claim is routed to a human (Lane 2), never
auto-rejected on that ground. This is the appeal-rate win (CLAUDE.md §7/§8).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd
import yaml

from src import constants

# coverage_clear takes one of these three states (batch pipeline).
CLEAR = "clear"
FLAG = "flag"
NOT_CLEAR = "not_clear"

# Live-layer 4-state decision (LOGIC §2.5).
STATE_CLEAR = "CLEAR"
STATE_FLAG = "FLAG"
STATE_HARD_DECLINE = "HARD_DECLINE"
STATE_LEGAL_WEAK = "LEGAL_WEAK"

COVERAGE_YAML = constants.CONFIG_DIR / "coverage.yaml"


@lru_cache(maxsize=1)
def _cfg() -> dict[str, Any]:
    with COVERAGE_YAML.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@dataclass(frozen=True)
class CoverageResult:
    coverage_clear: str  # 'clear' | 'flag' | 'not_clear'
    reason: str
    legal_weak_reject_flag: bool


def _get(claim: Mapping[str, Any], key: str, default: Any = 0) -> Any:
    val = claim[key] if key in claim else default
    return default if val is None or (isinstance(val, float) and np.isnan(val)) else val


def coverage_check(claim: Mapping[str, Any]) -> CoverageResult:
    """Evaluate coverage/eligibility for a single claim."""
    policy_status = str(_get(claim, "policy_status", "active"))
    driver_valid = int(_get(claim, "driver_valid_license", 1))
    dui = int(_get(claim, "dui_flag", 0))
    fir_required = int(_get(claim, "fir_required", 0))
    fir_filed = int(_get(claim, "fir_filed", 0))
    mod_undeclared = int(_get(claim, "modification_undeclared", 0))
    intimation_gt_48h = int(_get(claim, "intimation_gt_48h", 0))
    intimation_reason_valid = int(_get(claim, "intimation_reason_valid", 1))

    # Fairness flag is independent of the reject cascade below.
    legal_weak = bool(intimation_gt_48h == 1 and intimation_reason_valid == 1)

    if policy_status == "lapsed":
        return CoverageResult(NOT_CLEAR, "policy_lapsed", legal_weak)
    if driver_valid == 0 or dui == 1:
        return CoverageResult(NOT_CLEAR, "driver_ineligible", legal_weak)
    if fir_required == 1 and fir_filed == 0:
        return CoverageResult(NOT_CLEAR, "fir_missing", legal_weak)
    if mod_undeclared == 1:
        return CoverageResult(FLAG, "undeclared_modification", legal_weak)
    return CoverageResult(CLEAR, "none", legal_weak)


def coverage_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Vectorized coverage over a frame; index-aligned to ``df``."""
    lapsed = df["policy_status"].to_numpy() == "lapsed"
    driver_bad = (df["driver_valid_license"].to_numpy() == 0) | (df["dui_flag"].to_numpy() == 1)
    fir_missing = (df["fir_required"].to_numpy() == 1) & (df["fir_filed"].to_numpy() == 0)
    mod = df["modification_undeclared"].to_numpy() == 1

    reason = np.select(
        [lapsed, driver_bad, fir_missing, mod],
        ["policy_lapsed", "driver_ineligible", "fir_missing", "undeclared_modification"],
        default="none",
    )
    state = np.select(
        [lapsed | driver_bad | fir_missing, mod],
        [NOT_CLEAR, FLAG],
        default=CLEAR,
    )
    legal_weak = (df["intimation_gt_48h"].to_numpy() == 1) & (
        df["intimation_reason_valid"].to_numpy() == 1
    )
    out = pd.DataFrame(index=df.index)
    out["coverage_clear"] = state
    out["coverage_reason"] = reason
    out["legal_weak_reject_flag"] = legal_weak.astype(int)
    return out


# =========================================================================== #
# LIVE coverage matrix (LOGIC §2) - deductibles, NCB, add-ons, waterfall, and
# the 4-state decision. Used by the live workflow; the batch pipeline above is
# left untouched so the eval gates stay reproducible.
# =========================================================================== #
def _parse_date(x: Any) -> datetime | None:
    if not x:
        return None
    try:
        dt = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
        # Normalize to naive-UTC so tz-aware policy dates and naive FNOL dates
        # compare without "can't compare offset-naive and offset-aware" errors.
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None


def total_deductible(cubic_capacity: float = 0, vehicle_type: str = "private_car",
                     voluntary_excess: float = 0) -> dict[str, float]:
    """Compulsory + voluntary excess (LOGIC §2.1)."""
    ce = _cfg()["compulsory_excess"]
    vt = (vehicle_type or "private_car").lower()
    cc = float(cubic_capacity or 0)
    if vt in ("two_wheeler", "2w", "bike", "scooter"):
        comp = ce["two_wheeler"]["gte_150cc" if cc >= 150 else "lt_150cc"]
    else:
        comp = ce["private_car"]["gte_1500cc" if cc >= 1500 else "lt_1500cc"]
    vol = float(voluntary_excess or 0)
    return {"compulsory": float(comp), "voluntary": vol, "total": float(comp) + vol}


def ncb_for(claim_free_years: int) -> float:
    """NCB fraction for N consecutive claim-free years (LOGIC §2.2)."""
    y = int(claim_free_years or 0)
    if y <= 0:
        return 0.0
    slab = _cfg()["ncb_slab"]
    best = 0.0
    for row in slab:
        if y >= row["years"]:
            best = float(row["ncb"])
    return best


def _idv_depreciation(age: float) -> float:
    for band in _cfg()["idv_depreciation_by_age"]:
        if float(age or 0) <= band["max_years"]:
            return float(band["dep"])
    return 0.50


def addon_active(claim: Mapping[str, Any], addon: str) -> bool:
    addons = claim.get("add_ons") or claim.get("addons") or []
    if isinstance(addons, str):
        addons = [a.strip() for a in addons.split(",")]
    return addon in {str(a).lower() for a in addons}


def settlement_waterfall(
    *,
    claimed: float,
    line_item_estimate: float,
    idv: float,
    vehicle_age_years: float = 0,
    parts_depreciation: float = 0,
    consumables: float = 0,
    deductible_total: float = 0,
    salvage_value: float = 0,
    unrepaired_deduction: float = 0,
    zero_dep_active: bool = False,
    zero_dep_within_cap: bool = True,
    consumables_covered: bool = False,
    rti_active: bool = False,
    invoice_value: float | None = None,
    insured_retains_salvage: bool = False,
) -> dict[str, Any]:
    """The full settlement waterfall (LOGIC §2.4). Returns the payable + every step."""
    cfg = _cfg()
    assessed = min(float(claimed or 0), float(line_item_estimate or 0)) \
        if line_item_estimate else float(claimed or 0)

    # Total-loss branch (assessed > 75% IDV).
    if idv and assessed > cfg["total_loss_idv_ratio"] * float(idv):
        if rti_active and invoice_value:
            base = float(invoice_value)  # invoice + tax + reg (caller pre-sums)
            basis = "RTI: invoice value"
        else:
            base = float(idv) * (1 - _idv_depreciation(vehicle_age_years))
            basis = f"IDV less {_idv_depreciation(vehicle_age_years):.0%} depreciation"
        salvage = float(salvage_value) if insured_retains_salvage else 0.0
        net = max(0.0, base - salvage - float(deductible_total))
        return {
            "is_total_loss": True, "assessed_cost": round(assessed),
            "total_loss_basis": basis, "net_payable": round(net),
            "steps": [
                {"label": "Total-loss base", "amount": round(base)},
                {"label": "Salvage retained", "amount": -round(salvage)},
                {"label": "Deductible", "amount": -round(deductible_total)},
            ],
        }

    dep = 0.0 if (zero_dep_active and zero_dep_within_cap) else float(parts_depreciation)
    cons = 0.0 if consumables_covered else float(consumables)
    gross = assessed - dep - cons
    salvage = float(salvage_value) if insured_retains_salvage else 0.0
    net = max(0.0, gross - float(deductible_total) - salvage - float(unrepaired_deduction))
    return {
        "is_total_loss": False, "assessed_cost": round(assessed),
        "gross_payable": round(gross), "net_payable": round(net),
        "steps": [
            {"label": "Assessed cost (min of claimed, estimate)", "amount": round(assessed)},
            {"label": "Parts depreciation" + (" (waived: zero-dep)" if dep == 0 and zero_dep_active else ""),
             "amount": -round(dep)},
            {"label": "Consumables" + (" (covered)" if cons == 0 and consumables_covered else ""),
             "amount": -round(cons)},
            {"label": "Deductible", "amount": -round(float(deductible_total))},
            {"label": "Salvage retained", "amount": -round(salvage)},
            {"label": "Unrepaired/pre-existing", "amount": -round(float(unrepaired_deduction))},
        ],
    }


def advise_withdraw(*, net_payable: float, od_premium_next_year: float = 0,
                    claim_free_years: int = 0, ncb_protect_active: bool = False,
                    claims_this_year: int = 0) -> dict[str, Any]:
    """Should the customer be advised NOT to claim? (LOGIC §2.1 deductible + §2.2 NCB).

    Two triggers: payable is zero/near-zero (below deductible), or the NCB lost
    next year outweighs the payout. Catching these at FNOL is free TAT and a
    genuinely delightful product moment.
    """
    cfg = _cfg()
    if net_payable <= 0:
        return {"advise_withdraw": True, "reason": "payable_below_deductible",
                "net_benefit": round(net_payable), "ncb_loss": 0}
    current = ncb_for(claim_free_years)
    allowed = cfg["ncb_protect_default_claims_allowed"] if ncb_protect_active else 0
    reset = current if (ncb_protect_active and claims_this_year < allowed) else 0.0
    ncb_loss = float(od_premium_next_year or 0) * (current - reset)
    net_benefit = float(net_payable) - ncb_loss
    if net_benefit < 0:
        return {"advise_withdraw": True, "reason": "ncb_loss_exceeds_payout",
                "net_benefit": round(net_benefit), "ncb_loss": round(ncb_loss)}
    return {"advise_withdraw": False, "reason": "", "net_benefit": round(net_benefit),
            "ncb_loss": round(ncb_loss)}


def coverage_state(claim: Mapping[str, Any]) -> dict[str, Any]:
    """4-state live coverage decision (LOGIC §2.5).

    Returns {state, reason, reasons[], legal_weak}. States: CLEAR / FLAG /
    HARD_DECLINE / LEGAL_WEAK. HARD_DECLINE and LEGAL_WEAK are mutually exclusive
    with proceeding; FLAG proceeds (to Lane 2) with a note.
    """
    cfg = _cfg()
    hard: list[str] = []
    flags: list[str] = []
    legal_weak: list[str] = []

    incident = _parse_date(claim.get("incident_date"))

    # -- HARD DECLINE checks --------------------------------------------------
    # Policy in force ON THE INCIDENT DATE (not the filing date - a common
    # wrongful-rejection trap runs it the other way).
    pf, pt = _parse_date(claim.get("period_from")), _parse_date(claim.get("period_to"))
    if str(claim.get("policy_status", "active")).lower() == "lapsed":
        hard.append("policy_lapsed")
    elif incident and pf and pt and not (pf <= incident <= pt):
        hard.append("policy_not_in_force_on_incident_date")

    if int(_get(claim, "driver_valid_license", 1)) == 0:
        hard.append("no_valid_licence")
    if int(_get(claim, "dui_flag", 0)) == 1:
        hard.append("dui")

    # Cover type vs claim type: OD on a TP-only policy is an instant decline.
    product = str(claim.get("product_type") or "comprehensive").lower()
    ctype = str(claim.get("claim_type") or "OD")
    allowed = cfg["cover_type_allows"].get(product)
    if allowed is not None and ctype not in allowed:
        hard.append(f"claim_type_{ctype}_not_covered_by_{product}")

    # Usage class: private policy used commercially.
    usage = str(claim.get("usage_class") or "private").lower()
    if usage in ("commercial", "hire", "taxi") and product != "commercial":
        hard.append("usage_contrary_to_limitation")

    if bool(claim.get("deliberate_act")):
        hard.append("deliberate_act")
    if bool(claim.get("outside_geographic_area")):
        hard.append("outside_geographic_area")

    # Peril excluded: engine damage without engine-protection add-on.
    if bool(claim.get("engine_damage")) and not addon_active(
            claim, cfg["engine_damage_needs_addon"]):
        hard.append("engine_damage_without_engine_protect")

    # -- LEGAL-WEAK checks (never auto-decline; force human) -------------------
    late = int(_get(claim, "intimation_gt_48h", 0)) == 1 or float(
        _get(claim, "intimation_delay_hours", 0)) > cfg["intimation_late_hours"]
    if late and int(_get(claim, "intimation_reason_valid", 1)) == 1:
        legal_weak.append("late_intimation_valid_reason")
    # Undeclared mod causally UNRELATED to the damage - not a lawful decline.
    if int(_get(claim, "modification_undeclared", 0)) == 1 and not bool(
            claim.get("modification_related_to_damage")):
        legal_weak.append("undeclared_mod_no_nexus_to_loss")
    if bool(claim.get("technical_breach")) and not bool(claim.get("breach_caused_loss")):
        legal_weak.append("technical_breach_no_nexus")

    # -- FLAG checks (proceed to Lane 2 with a note) --------------------------
    if int(_get(claim, "modification_undeclared", 0)) == 1 and bool(
            claim.get("modification_related_to_damage")):
        flags.append("undeclared_modification")
    if bool(claim.get("doc_mismatch_minor")):
        flags.append("minor_document_mismatch")
    if bool(claim.get("addon_cap_exhausted")):
        flags.append("addon_cap_exhausted")
    if bool(claim.get("ncb_negative")):
        flags.append("ncb_negative_claim")
    fir_required = int(_get(claim, "fir_required", 0))
    if fir_required == 1 and int(_get(claim, "fir_filed", 0)) == 0:
        flags.append("fir_missing")

    # Precedence: hard decline > legal-weak > flag > clear.
    if hard:
        return {"state": STATE_HARD_DECLINE, "reason": hard[0], "reasons": hard,
                "legal_weak": bool(legal_weak)}
    if legal_weak:
        return {"state": STATE_LEGAL_WEAK, "reason": legal_weak[0],
                "reasons": legal_weak, "legal_weak": True}
    if flags:
        return {"state": STATE_FLAG, "reason": flags[0], "reasons": flags,
                "legal_weak": False}
    return {"state": STATE_CLEAR, "reason": "none", "reasons": [], "legal_weak": False}
