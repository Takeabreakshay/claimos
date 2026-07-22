"""Phase 2 — MOCKED external rails (CLAUDE.md §3 rule 2, §10 Phase 2, §12).

Every production integration is mocked here behind a clean, typed interface with
a ``# PRODUCTION:`` swap comment marking exactly where the real API drops in. The
prototype needs ZERO external keys and never attempts a real call.

Rail -> production system (CLAUDE.md §12):
  * verify_vehicle()    -> VAHAN / Parivahan (RC + engine/chassis match)
  * verify_license()    -> DigiLocker / Parivahan Sarathi (DL validity)
  * get_claim_history() -> core policy / claims DB (prior claims & fraud flags)
  * get_prism_score()   -> IIB PRISM (risk score) + IIB QUEST (fraud flag)

Determinism (rule 4): each rail is **keyed by the claim** — a stable per-claim
seed derived from ``claim_id`` (SHA-256, salted per rail) drives a local RNG, so
the same claim always yields the same rail output, whether called one-off in the
demo or in bulk over the whole frame. Because this is synthetic, the mocks use
the ground-truth fields (``is_fraud`` etc.) to inject *realistic but noisy*
correlation — the stand-in for the true state a real rail would return. The noise
is deliberate: it keeps fraud learnable-not-trivial (CLAUDE.md §5.2).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from src.constants import SEED

# Per-rail salts so the four rails draw independent (uncorrelated) randomness
# from the same claim_id.
_SALT_VEHICLE = 0x1111
_SALT_LICENSE = 0x2222
_SALT_HISTORY = 0x3333
_SALT_PRISM = 0x4444

_VEHICLE_MAKES = ("Maruti", "Hyundai", "Tata", "Mahindra", "Honda", "Toyota", "Kia")
_LICENSE_CLASSES = ("LMV", "LMV-TR", "MCWG")


# --------------------------------------------------------------------------- #
# Seeding helpers
# --------------------------------------------------------------------------- #
def _claim_rng(claim_id: str, salt: int, base: int) -> np.random.Generator:
    """Stable per-claim, per-rail RNG (SHA-256 keyed — not Python's salted hash)."""
    digest = hashlib.sha256(f"{base}:{salt}:{claim_id}".encode()).hexdigest()
    return np.random.default_rng(int(digest[:16], 16))


def _get(claim: Mapping[str, Any], key: str, default: Any = 0) -> Any:
    val = claim[key] if key in claim else default
    return default if val is None or (isinstance(val, float) and np.isnan(val)) else val


# --------------------------------------------------------------------------- #
# Typed rail responses
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class VehicleVerification:
    claim_id: str
    registration_valid: bool
    rc_status: str  # 'active' | 'expired'
    make: str
    model: str
    engine_chassis_match: bool
    source: str = "MOCK:VAHAN"


@dataclass(frozen=True)
class LicenseVerification:
    claim_id: str
    license_valid: bool
    license_class: str
    dl_status: str  # 'active' | 'suspended' | 'expired'
    source: str = "MOCK:DigiLocker/Sarathi"


@dataclass(frozen=True)
class ClaimHistory:
    claim_id: str
    customer_id: str
    prior_claims_3y: int
    prior_fraud_flags: int
    days_since_last_claim: int  # -1 sentinel when no prior claim
    source: str = "MOCK:PolicyDB"


@dataclass(frozen=True)
class PrismScore:
    claim_id: str
    prism_score: float  # 0-1 predictive motor risk
    prism_percentile: int  # 0-100
    quest_hit: bool  # IIB QUEST fraud-database hit
    source: str = "MOCK:IIB-PRISM/QUEST"


# --------------------------------------------------------------------------- #
# The four rails
# --------------------------------------------------------------------------- #
def verify_vehicle(claim: Mapping[str, Any], base: int = SEED) -> VehicleVerification:
    # PRODUCTION: replace with real VAHAN / Parivahan RC-verification API call.
    cid = str(_get(claim, "claim_id", "UNKNOWN"))
    rng = _claim_rng(cid, _SALT_VEHICLE, base)
    is_fraud = int(_get(claim, "is_fraud", 0))
    fraud_type = str(_get(claim, "fraud_type", "none"))

    registration_valid = bool(rng.random() < 0.98)
    rc_status = "active" if rng.random() < 0.95 else "expired"
    # Engine/chassis mismatch is a document-falsification tell (noisy).
    doc_fraud = is_fraud == 1 and fraud_type == "document_falsification"
    match_prob = 0.90 if doc_fraud else 0.97
    engine_chassis_match = bool(rng.random() < match_prob)

    make = _VEHICLE_MAKES[int(rng.integers(0, len(_VEHICLE_MAKES)))]
    model = f"{make[:3].upper()}-{int(rng.integers(100, 999))}"
    return VehicleVerification(
        claim_id=cid,
        registration_valid=registration_valid,
        rc_status=rc_status,
        make=make,
        model=model,
        engine_chassis_match=engine_chassis_match,
    )


def verify_license(claim: Mapping[str, Any], base: int = SEED) -> LicenseVerification:
    # PRODUCTION: replace with real DigiLocker / Parivahan Sarathi DL-verification API.
    cid = str(_get(claim, "claim_id", "UNKNOWN"))
    rng = _claim_rng(cid, _SALT_LICENSE, base)
    has_valid = int(_get(claim, "driver_valid_license", 1))
    dui = int(_get(claim, "dui_flag", 0))

    # The rail confirms the FNOL-declared licence state, with small disagreement noise.
    license_valid = bool(has_valid == 1) if rng.random() < 0.98 else bool(has_valid == 0)
    if dui == 1 and rng.random() < 0.6:
        dl_status = "suspended"
    elif not license_valid:
        dl_status = "expired"
    else:
        dl_status = "active"
    license_class = _LICENSE_CLASSES[int(rng.integers(0, len(_LICENSE_CLASSES)))]
    return LicenseVerification(
        claim_id=cid,
        license_valid=license_valid,
        license_class=license_class,
        dl_status=dl_status,
    )


def get_claim_history(claim: Mapping[str, Any], base: int = SEED) -> ClaimHistory:
    # PRODUCTION: replace with real core policy/claims DB history lookup.
    cid = str(_get(claim, "claim_id", "UNKNOWN"))
    rng = _claim_rng(cid, _SALT_HISTORY, base)
    is_fraud = int(_get(claim, "is_fraud", 0))
    is_ring = int(_get(claim, "is_ring_claim", 0))

    # Ring / fraud customers carry more prior claims and prior fraud flags. Legit
    # claims get a small base flag rate too, so prior_fraud_flags is not a perfect
    # separator (keeps fraud AUC off the trivial ceiling).
    lam_claims = 0.5 + 0.5 * is_fraud + 1.2 * is_ring
    prior_claims_3y = int(rng.poisson(lam_claims))
    lam_flags = 0.06 + 0.07 * is_fraud + 0.4 * is_ring
    prior_fraud_flags = int(min(prior_claims_3y, rng.poisson(lam_flags)))
    days_since_last_claim = int(rng.integers(30, 1095)) if prior_claims_3y > 0 else -1
    return ClaimHistory(
        claim_id=cid,
        customer_id=str(_get(claim, "customer_id", "UNKNOWN")),
        prior_claims_3y=prior_claims_3y,
        prior_fraud_flags=prior_fraud_flags,
        days_since_last_claim=days_since_last_claim,
    )


def get_prism_score(claim: Mapping[str, Any], base: int = SEED) -> PrismScore:
    # PRODUCTION: replace with real IIB PRISM risk-score + IIB QUEST fraud-flag APIs.
    cid = str(_get(claim, "claim_id", "UNKNOWN"))
    rng = _claim_rng(cid, _SALT_PRISM, base)
    is_fraud = int(_get(claim, "is_fraud", 0))
    is_ring = int(_get(claim, "is_ring_claim", 0))

    # FNOL-available risk score: correlated with fraud/ring but noisy (no future-label
    # leakage — escalation outcome is NOT used here). Kept deliberately noisy so the
    # fraud model lands ~0.80-0.90 AUC, not ~0.99.
    logit = -1.0 + 0.7 * is_fraud + 0.7 * is_ring + 1.5 * rng.standard_normal()
    prism_score = float(1.0 / (1.0 + np.exp(-logit)))
    prism_percentile = int(round(prism_score * 100))
    quest_hit = bool(prism_score > 0.85 and rng.random() < 0.5)
    return PrismScore(
        claim_id=cid,
        prism_score=round(prism_score, 4),
        prism_percentile=prism_percentile,
        quest_hit=quest_hit,
    )


# --------------------------------------------------------------------------- #
# Convenience aggregators (used by features.py in Phase 3 and the demo)
# --------------------------------------------------------------------------- #
def enrich_claim(claim: Mapping[str, Any], base: int = SEED) -> dict[str, Any]:
    """Run all four rails for one claim and flatten to a ``rail_*`` dict."""
    v = verify_vehicle(claim, base)
    lic = verify_license(claim, base)
    h = get_claim_history(claim, base)
    p = get_prism_score(claim, base)
    return {
        "rail_registration_valid": int(v.registration_valid),
        "rail_rc_status": v.rc_status,
        "rail_engine_chassis_match": int(v.engine_chassis_match),
        "rail_vehicle_make": v.make,
        "rail_vehicle_model": v.model,
        "rail_license_valid": int(lic.license_valid),
        "rail_license_class": lic.license_class,
        "rail_dl_status": lic.dl_status,
        "rail_prior_claims_3y": h.prior_claims_3y,
        "rail_prior_fraud_flags": h.prior_fraud_flags,
        "rail_days_since_last_claim": h.days_since_last_claim,
        "rail_prism_score": p.prism_score,
        "rail_prism_percentile": p.prism_percentile,
        "rail_quest_hit": int(p.quest_hit),
    }


def enrich_frame(df: pd.DataFrame, base: int = SEED) -> pd.DataFrame:
    """Vectorized-over-rows rail enrichment; index-aligned to ``df``."""
    records = [enrich_claim(row, base) for row in df.to_dict(orient="records")]
    return pd.DataFrame.from_records(records, index=df.index)
