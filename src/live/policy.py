"""Mocked policy-master lookup - the customer-facing "read-through to the core
policy DB" rail (CLAUDE.md §2 rule: production rails are MOCKED behind a clean
interface). Deterministic: a policy number always resolves to the same vehicle +
coverage profile, so the customer app can show a real-feeling policy card and the
coverage matrix / rate card get the inputs they need.

PRODUCTION: replace lookup_policy() with a read from Bajaj's core policy DB.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

# A small fleet the demo draws from (make, model, segment, cc, fuel, idv).
_VEHICLES = [
    ("Maruti Suzuki", "Swift",   "hatchback",   1197, "petrol",  580000),
    ("Maruti Suzuki", "Baleno",  "hatchback",   1197, "petrol",  720000),
    ("Hyundai",       "i20",     "hatchback",   1197, "petrol",  760000),
    ("Honda",         "City",    "sedan",       1498, "petrol", 1150000),
    ("Hyundai",       "Verna",   "sedan",       1497, "petrol", 1090000),
    ("Tata",          "Nexon",   "compact_suv", 1199, "petrol",  920000),
    ("Hyundai",       "Venue",   "compact_suv", 1197, "petrol",  880000),
    ("Hyundai",       "Creta",   "suv",         1497, "petrol", 1420000),
    ("Kia",           "Seltos",  "suv",         1497, "petrol", 1380000),
    ("Mahindra",      "XUV700",  "suv",         1997, "diesel", 1850000),
]

_ADDON_SETS = [
    [],
    ["zero_depreciation"],
    ["zero_depreciation", "consumables"],
    ["zero_depreciation", "engine_protection", "consumables"],
    ["engine_protection"],
    ["zero_depreciation", "return_to_invoice", "consumables", "roadside_assistance"],
]

_CITY = [("Mumbai", "metro"), ("Delhi", "metro"), ("Bengaluru", "metro"),
         ("Pune", "tier2"), ("Jaipur", "tier2"), ("Nashik", "tier3")]


def _h(policy_id: str, salt: str, mod: int) -> int:
    """Deterministic small int from the policy id + a salt (no RNG - reproducible)."""
    d = hashlib.sha256(f"{policy_id}|{salt}".encode()).hexdigest()
    return int(d[:8], 16) % mod


def lookup_policy(policy_id: str, now: datetime | None = None) -> dict[str, Any]:
    """Resolve a policy number to a full, deterministic policy profile."""
    pid = (policy_id or "POL-DEMO").strip().upper()
    now = now or datetime.now(timezone.utc)

    make, model, segment, cc, fuel, idv_base = _VEHICLES[_h(pid, "veh", len(_VEHICLES))]
    # nudge IDV +/-8% deterministically so two policies on the same model differ
    idv = int(idv_base * (0.92 + 0.16 * (_h(pid, "idv", 100) / 100)))
    city, tier = _CITY[_h(pid, "city", len(_CITY))]
    add_ons = _ADDON_SETS[_h(pid, "addon", len(_ADDON_SETS))]
    ncb_years = _h(pid, "ncb", 6)              # 0..5 claim-free years
    voluntary = [0, 0, 2500, 5000][_h(pid, "vol", 4)]
    age = 1 + _h(pid, "age", 6)                # 1..6 years old
    product = "comprehensive"                  # demo policies are comprehensive

    # Current annual period: renewed a deterministic 60-330 days ago so it
    # comfortably covers recent incidents (an anniversary landing on "today"
    # would wrongly read a 3-day-old incident as pre-inception).
    renewed_days_ago = 60 + _h(pid, "renew", 271)      # 60..330
    period_from = (now - timedelta(days=renewed_days_ago)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    period_to = period_from + timedelta(days=365)

    reg = f"{['MH','DL','KA','RJ','MH','MH'][_h(pid,'rto',6)]}{_h(pid,'rr',89)+10:02d}" \
          f"{chr(65 + _h(pid,'a1',26))}{chr(65 + _h(pid,'a2',26))}{_h(pid,'num',9000)+1000}"

    return {
        "policy_id": pid,
        "found": True,
        "holder_name_masked": "Policyholder",   # PII stays out; real DB supplies it
        "make": make, "model": model, "segment": segment,
        "cubic_capacity": cc, "fuel_type": fuel, "is_ev": fuel == "electric",
        "registration_no": reg,
        "idv": idv,
        "vehicle_age_years": age,
        "product_type": product,
        "period_from": period_from.isoformat(),
        "period_to": period_to.isoformat(),
        "add_ons": add_ons,
        "ncb_percent": [0, 20, 25, 35, 45, 50][ncb_years],
        "claim_free_years": ncb_years,
        "voluntary_excess": voluntary,
        "od_premium_next_year": int(idv * 0.028),   # ~2.8% of IDV, indicative
        "city": city, "city_tier": tier, "geo": "metro" if tier == "metro" else "urban",
        "vehicle_type": "private_car",
        "usage_class": "private",
    }
