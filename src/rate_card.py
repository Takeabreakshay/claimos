"""Parts + labour rate card - deterministic repair-cost estimator (LOGIC §1).

Turns a set of damaged parts (from the vision model or a garage estimate) into a
P10/P50/P90 rupee band via the §1.4 assembly formula, plus the signals the triage
engine needs downstream:

  * ``line_item_estimate``      - a strong cost prior / feature (LOGIC §1.5)
  * ``has_structural`` / ``has_airbag`` - hard severity escalators (LOGIC §1.5, §5)
  * ``total_loss_trigger``      - engine/gearbox rebuild - total-loss check (§2.4)
  * per-part depreciation       - handed to the settlement waterfall (§2.4), NOT
                                  subtracted here (the estimate is gross repair cost)

Everything is config-driven from ``config/rate_card.yaml`` (Rule 5). Free-text part
names ("left tail light", "front bumper scuff") are normalised to canonical keys
so the vision model's output plugs straight in.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Iterable

import yaml

from src import constants

RATE_CARD_YAML = constants.CONFIG_DIR / "rate_card.yaml"
MODEL_PARTS_YAML = constants.CONFIG_DIR / "model_parts.yaml"


@lru_cache(maxsize=1)
def _card() -> dict[str, Any]:
    with RATE_CARD_YAML.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# --------------------------------------------------------------------------- #
# Model-specific parts pricing (config/model_parts.yaml, optionally overridden
# by a Supabase `model_parts` table). A Honda City door costs ~2.5x a Maruti
# Alto door - segment multipliers alone can't capture that, so when the claim's
# make+model matches we price each part from the model's own OEM basket.
# --------------------------------------------------------------------------- #
# A provider set by the live layer (store.model_parts) so Supabase can override
# the shipped config without this module importing the DB. Signature:
#   provider(make, model) -> {"segment": str, "parts": {key: [low, high]}} | None
_price_provider = None


def set_model_price_provider(fn) -> None:
    """Register a Supabase-backed lookup, preferred over the bundled config."""
    global _price_provider
    _price_provider = fn


@lru_cache(maxsize=1)
def _model_index() -> dict[str, dict[str, Any]]:
    """Flatten config/model_parts.yaml into an alias -> entry lookup."""
    if not MODEL_PARTS_YAML.exists():
        return {}
    with MODEL_PARTS_YAML.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    idx: dict[str, dict[str, Any]] = {}
    for canon, entry in (raw.get("models") or {}).items():
        keys = {canon} | {str(a) for a in (entry.get("aliases") or [])}
        mm = f"{entry.get('make', '')} {entry.get('model', '')}"
        keys.add(mm)
        for k in keys:
            idx[_norm_model_key(k)] = entry
    return idx


def _norm_model_key(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower())).strip()


def model_prices(make: str | None, model: str | None) -> dict[str, Any] | None:
    """Model-specific {segment, parts:{key:[low,high]}} for a make+model, or None.

    Prefers the registered Supabase provider; falls back to the bundled config.
    Matches on the full "make model" string and on the model token alone, so
    "Honda", "City" and a bare "city" all resolve.
    """
    make = (make or "").strip()
    model = (model or "").strip()
    if not (make or model):
        return None
    if _price_provider is not None:
        try:
            hit = _price_provider(make, model)
            if hit and hit.get("parts"):
                return hit
        except Exception:
            pass  # DB down -> fall through to config, never dead-end pricing
    idx = _model_index()
    for cand in (f"{make} {model}", model, f"{make} {model}".replace("  ", " ")):
        entry = idx.get(_norm_model_key(cand))
        if entry:
            return entry
    return None


# --------------------------------------------------------------------------- #
# Part-name normalisation - map free text to a canonical rate-card key.
# --------------------------------------------------------------------------- #
# Synonyms/aliases - canonical key. Matched by longest-alias-first substring so
# "rear bumper" beats "bumper". Side words (left/right/front/rear/each) are kept
# only where they disambiguate a real key (front vs rear bumper/door/fender).
_ALIASES: dict[str, str] = {
    "front bumper": "front_bumper", "rear bumper": "rear_bumper",
    "bumper": "front_bumper",
    "bonnet": "bonnet", "hood": "bonnet",
    "front fender": "front_fender", "rear fender": "rear_fender",
    "front wing": "front_fender", "rear wing": "rear_fender",
    "fender": "front_fender", "wing": "front_fender", "mudguard": "front_fender",
    "front door": "front_door", "rear door": "rear_door", "door": "front_door",
    "boot": "boot_lid", "boot lid": "boot_lid", "tailgate": "boot_lid", "dickey": "boot_lid",
    "roof": "roof_panel", "roof panel": "roof_panel",
    "quarter panel": "quarter_panel", "quarter": "quarter_panel",
    "headlamp": "headlamp", "headlight": "headlamp", "head lamp": "headlamp", "head light": "headlamp",
    "tail lamp": "tail_lamp", "tail light": "tail_lamp", "taillight": "tail_lamp",
    "taillamp": "tail_lamp", "rear light": "tail_lamp",
    "windshield": "windshield_front", "windscreen": "windshield_front",
    "front windshield": "windshield_front", "front windscreen": "windshield_front",
    "rear windshield": "windshield_rear", "rear windscreen": "windshield_rear",
    "rear glass": "windshield_rear",
    "door glass": "door_glass", "window glass": "door_glass", "side glass": "door_glass",
    "orvm": "orvm", "mirror": "orvm", "side mirror": "orvm", "wing mirror": "orvm",
    "rear view mirror": "orvm",
    "grille": "grille", "grill": "grille", "radiator grille": "grille",
    "radiator": "radiator", "condenser": "condenser", "ac condenser": "condenser",
    "airbag module": "airbag_module", "airbag control": "airbag_module",
    "srs module": "airbag_module",
    "airbag": "airbag", "air bag": "airbag",
    "suspension": "suspension_strut", "strut": "suspension_strut",
    "shock absorber": "suspension_strut", "suspension strut": "suspension_strut",
    "alloy wheel": "alloy_wheel", "alloy": "alloy_wheel", "rim": "alloy_wheel",
    "steel wheel": "steel_wheel", "wheel": "steel_wheel",
    "tyre": "tyre", "tire": "tyre",
    "battery": "battery",
    "engine": "engine_assembly", "engine assembly": "engine_assembly", "motor": "engine_assembly",
    "gearbox": "gearbox", "transmission": "gearbox",
    "chassis": "chassis_straighten", "frame": "chassis_straighten",
    "chassis straightening": "chassis_straighten", "pillar": "chassis_straighten",
}

# Alias keys, longest first, so multi-word aliases win over their substrings.
_ALIAS_KEYS = sorted(_ALIASES, key=len, reverse=True)

# Parts that get a paint panel when repaired/replaced.
_PAINT_PANELS = {
    "front_bumper", "rear_bumper", "bonnet", "front_fender", "rear_fender",
    "front_door", "rear_door", "boot_lid", "roof_panel", "quarter_panel", "grille",
}


def normalize_part(name: str) -> str | None:
    """Map a free-text part name to a canonical rate-card key, or None."""
    if not name:
        return None
    s = re.sub(r"[^a-z ]", " ", str(name).lower())
    s = re.sub(r"\s+", " ", s).strip()
    if s in _card()["parts"]:
        return s
    for alias in _ALIAS_KEYS:
        if alias in s:
            return _ALIASES[alias]
    # last resort: token overlap with a canonical key
    toks = set(s.split())
    for key in _card()["parts"]:
        if toks & set(key.split("_")):
            return key
    return None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _mid(rng: list[float]) -> float:
    return (float(rng[0]) + float(rng[1])) / 2.0


def _labour_rate(garage_type: str, city_tier: str) -> float:
    c = _card()
    cls = c["garage_type_to_labour_class"].get(
        (garage_type or "").lower(), c["default_labour_class"])
    tier = (city_tier or c["default_region"]).lower()
    table = c["labour_rates"].get(cls, c["labour_rates"][c["default_labour_class"]])
    rng = table.get(tier) or table.get("metro")
    return _mid(rng)


def _metal_dep(age: float) -> float:
    for band in _card()["metal_depreciation_by_age"]:
        if age <= band["max_years"]:
            return float(band["dep"])
    return 0.50


def _part_depreciation(material: str, age: float) -> float:
    if material == "metal":
        return _metal_dep(age)
    return float(_card()["fixed_material_depreciation"].get(material, 0.0))


def segment_for(make: str | None = None, model: str | None = None,
                declared: str | None = None) -> str:
    """Best-effort segment from a declared value or a make/model hint."""
    c = _card()
    if declared and declared.lower() in c["segment_multipliers"]:
        return declared.lower()
    text = f"{make or ''} {model or ''}".lower()
    hints = {
        "two_wheeler": ["pulsar", "activa", "splendor", "bike", "scooter", "royal enfield"],
        "luxury": ["mercedes", "bmw", "audi", "jaguar", "volvo", "lexus"],
        "suv": ["creta", "seltos", "xuv700", "scorpio", "safari", "harrier", "fortuner", "innova"],
        "compact_suv": ["nexon", "venue", "sonet", "brezza", "magnite", "kiger"],
        "sedan": ["city", "verna", "dzire", "ciaz", "amaze", "aura", "virtus", "slavia"],
    }
    for seg, words in hints.items():
        if any(w in text for w in words):
            return seg
    return c["default_segment"]


# --------------------------------------------------------------------------- #
# The estimator (LOGIC §1.4)
# --------------------------------------------------------------------------- #
def estimate(
    damaged_parts: Iterable[str],
    *,
    segment: str = "hatchback",
    garage_type: str = "network",
    city_tier: str = "metro",
    vehicle_age_years: float = 0.0,
    is_ev: bool = False,
    is_import: bool = False,
    make: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Deterministic line-item repair estimate - P10/P50/P90 (gross, no dep).

    Depreciation is computed per part and returned for the settlement waterfall
    (§2.4) but NOT subtracted here - the estimate is the gross repair cost the
    garage charges.

    If ``make``/``model`` match a model-specific parts basket (Honda City, Maruti
    Alto, ...), each matched part is priced from that basket - region multiplier
    still applies, the segment multiplier does not (the model fixes the segment).
    Parts without a model-specific price fall back to the segment-scaled base.
    """
    c = _card()
    mp = model_prices(make, model)
    part_prices = (mp or {}).get("parts") or {}
    if mp and mp.get("segment"):
        segment = mp["segment"]
    seg_mult = c["segment_multipliers"].get((segment or "").lower(),
                                            c["segment_multipliers"][c["default_segment"]])
    region_mult = c["region_multipliers"].get((city_tier or "").lower(),
                                              c["region_multipliers"][c["default_region"]])
    labour_rate = _labour_rate(garage_type, city_tier)

    matched: list[str] = []
    unmatched: list[str] = []
    for raw in damaged_parts or []:
        key = normalize_part(raw)
        (matched if key else unmatched).append(key or raw)
    # de-dup while preserving order (a part claimed twice is one part)
    seen: set[str] = set()
    matched = [k for k in matched if not (k in seen or seen.add(k))]

    parts_cost = labour_cost = paint_cost = 0.0
    parts_dep_amt = paint_dep_amt = 0.0
    has_structural = has_airbag = has_hidden = total_loss_trigger = False
    line_items: list[dict[str, Any]] = []
    n_panels = 0
    n_model_priced = 0

    for key in matched:
        p = c["parts"][key]
        override = part_prices.get(key)
        if override:
            # Model-specific OEM price: absolute for this model, so the segment
            # multiplier is NOT applied (only region). Base parts still scale.
            price = _mid(override) * region_mult
            n_model_priced += 1
        else:
            price = _mid(p["price"]) * seg_mult * region_mult
        if is_ev and p.get("ev_electrical"):
            price *= c["ev_electrical_mult"]
        if is_import and p.get("material") == "metal":
            price *= c["import_body_mult"]
        hrs = float(p["labour_hrs"])
        labour = hrs * labour_rate
        dep_rate = _part_depreciation(p.get("material", "other"), vehicle_age_years)
        dep_amt = price * dep_rate

        parts_cost += price
        labour_cost += labour
        parts_dep_amt += dep_amt
        has_structural = has_structural or bool(p.get("structural"))
        has_airbag = has_airbag or bool(p.get("airbag"))
        has_hidden = has_hidden or bool(p.get("hidden_risk"))
        total_loss_trigger = total_loss_trigger or bool(p.get("total_loss_check"))
        if key in _PAINT_PANELS:
            n_panels += 1

        line_items.append({
            "part": key, "price": round(price), "labour_hrs": hrs,
            "labour": round(labour), "material": p.get("material"),
            "depreciation_rate": dep_rate, "depreciation": round(dep_amt),
            "structural": bool(p.get("structural")), "airbag": bool(p.get("airbag")),
        })

    # Paint (per panel) - material + booth labour; 50% dep on material only.
    pt = c["paint"]
    paint_mat = _mid(pt["material_per_panel"]) * seg_mult * region_mult
    paint_lab = float(pt["labour_hrs_per_panel"]) * labour_rate
    paint_cost = n_panels * (paint_mat + paint_lab)
    paint_dep_amt = n_panels * paint_mat * float(pt["material_depreciation"])

    subtotal = parts_cost + labour_cost + paint_cost
    cons_rate = _mid(c["consumables_rate"])
    consumables = cons_rate * subtotal
    p50 = subtotal + consumables

    u = c["uncertainty"]
    band = u["base"] + u["per_part"] * len(matched) + (u["structural"] if has_structural else 0.0)
    band = min(band, 0.60)
    p10 = p50 * (1 - band)
    p90 = p50 * (1 + band)

    escalators = c["hard_escalators"]
    escalate = ("airbag_deployed" in escalators and has_airbag) or \
               ("structural" in escalators and has_structural)

    return {
        "line_item_estimate": round(p50),
        "cost_p10": round(p10), "cost_p50": round(p50), "cost_p90": round(p90),
        "parts_cost": round(parts_cost), "labour_cost": round(labour_cost),
        "paint_cost": round(paint_cost), "consumables": round(consumables),
        "parts_depreciation": round(parts_dep_amt + paint_dep_amt),
        "n_parts": len(matched), "n_panels": n_panels,
        "n_model_priced": n_model_priced,
        "priced_from": (f"{(mp or {}).get('make','')} {(mp or {}).get('model','')}".strip()
                        if n_model_priced else None),
        "has_structural": has_structural, "has_airbag": has_airbag,
        "has_hidden_risk": has_hidden, "total_loss_trigger": total_loss_trigger,
        "escalate_min_lane": constants.Lane.ASSISTED.value if escalate else None,
        "line_items": line_items,
        "matched_parts": matched, "unmatched_parts": unmatched,
        "labour_rate": round(labour_rate), "segment": segment,
        "uncertainty_band": round(band, 3),
    }


def reconciliation_flag(claim_amount: float, line_item_estimate: float,
                        garage_type: str = "network") -> dict[str, Any]:
    """Cost-reconciliation / inflation signal (LOGIC §1.3, §4).

    An estimate that doesn't reconcile with its own parts list is a padding tell.
    Returns {ratio, min_lane, inflation_flag, non_network_tell}.
    """
    r = _card()["reconciliation"]
    if not line_item_estimate or line_item_estimate <= 0:
        return {"ratio": None, "min_lane": None, "inflation_flag": False,
                "non_network_tell": False}
    ratio = float(claim_amount) / float(line_item_estimate)
    min_lane = None
    inflation = False
    if ratio > r["lane3_inflation_mult"]:
        min_lane = constants.Lane.INVESTIGATIVE.value
        inflation = True
    elif ratio > r["lane2_inflation_mult"]:
        min_lane = constants.Lane.ASSISTED.value
    non_network_tell = (
        (garage_type or "").lower() == "non_network"
        and ratio > r["non_network_inflation_mult"]
    )
    return {"ratio": round(ratio, 3), "min_lane": min_lane,
            "inflation_flag": inflation, "non_network_tell": non_network_tell}
