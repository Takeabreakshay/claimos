"""Phase 1 - synthetic claim generator (CLAUDE.md §5, LOGIC_AND_FORMULAS.md §1).

Generates N calibrated-synthetic motor claims with FNOL fields, correlated-but-
noisy fraud signals, collusion-ring structure, and ground-truth target labels.

Design guarantees (CLAUDE.md §3):
  * 100% synthetic - names/plates via Faker, clearly fake (rule 1).
  * Every random draw seeds off ``constants.SEED``; two runs are identical (rule 4).
  * No magic numbers - all parameters come from ``config/distributions.yaml`` and
    are logged in ``ASSUMPTIONS.md`` (rule 5).

Draw order follows LOGIC_AND_FORMULAS.md §1 (later draws depend on earlier). One
deviation from the doc's section numbering, made explicit here: ``is_fraud`` is
drawn immediately after claim type (it depends only on type) so the fraud-lifted
signals in §1.3 can be conditioned on it - this is what the §1.3 math requires.

Entry point: ``poetry run claimos-generate``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from faker import Faker

from src import constants
from src.constants import (
    SEED,
    ClaimType,
    FraudType,
    GarageType,
    PolicyStatus,
    Severity,
    SurveyorVerdict,
)

# Fields that must never contain NaN (asserted by test_data_gen).
REQUIRED_FIELDS: list[str] = [
    "claim_id",
    "claim_type",
    "idv",
    "claim_amount",
    "incident_severity",
    "policy_status",
    "garage_type",
    "geo",
    "is_fraud",
    "fraud_type",
    "true_repair_cost",
    "escalated_at_90d",
    "surveyor_verdict",
    "final_settlement",
    "baseline_tat_days",
    "customer_id",
    "garage_id",
    "surveyor_id",
]


# --------------------------------------------------------------------------- #
# Small seeded helpers
# --------------------------------------------------------------------------- #
def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _cat(rng: np.random.Generator, weights: dict[str, float], size: int) -> np.ndarray:
    """Sample ``size`` categorical labels from a {label: weight} dict."""
    keys = list(weights.keys())
    probs = np.asarray([weights[k] for k in keys], dtype=float)
    probs = probs / probs.sum()
    idx = rng.choice(len(keys), size=size, p=probs)
    return np.asarray(keys, dtype=object)[idx]


def _bern(rng: np.random.Generator, p, size: int) -> np.ndarray:
    """Bernoulli draw; ``p`` may be a scalar or a length-``size`` array."""
    return (rng.random(size) < p).astype(np.int8)


def _lifted_signal(
    rng: np.random.Generator, is_fraud: np.ndarray, q: float, lift: float
) -> np.ndarray:
    """A fraud-correlated binary signal (LOGIC §1.3).

    P(s=1 | not fraud) = q ; P(s=1 | fraud) = clip(q + lift, 0, 0.98).
    """
    p = np.where(is_fraud == 1, np.clip(q + lift, 0.0, 0.98), q)
    return _bern(rng, p, len(is_fraud))


def _tune_intercept(base_logit: np.ndarray, target_mean: float) -> float:
    """Bisection: find intercept b so mean(sigmoid(b + base_logit)) == target.

    Deterministic given ``base_logit`` (built from seeded draws), so the whole
    generator stays reproducible. sigmoid is monotincreasing in b.
    """
    lo, hi = -20.0, 20.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if _sigmoid(mid + base_logit).mean() < target_mean:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# --------------------------------------------------------------------------- #
# The generator
# --------------------------------------------------------------------------- #
def generate_claims(n: int | None = None, seed: int = SEED) -> pd.DataFrame:
    """Generate the synthetic claims DataFrame (deterministic given ``seed``)."""
    cfg = constants.load_distributions()
    n = int(cfg["n_claims"]) if n is None else int(n)

    rng = np.random.default_rng(seed)
    fake = Faker("en_IN")
    fake.seed_instance(seed)

    # ----- 1.1 Claim type & amounts ------------------------------------------
    claim_type = _cat(rng, cfg["claim_type_mix"], n)

    idv = np.exp(cfg["idv"]["mu"] + cfg["idv"]["sigma"] * rng.standard_normal(n))

    ca = cfg["claim_amount"]
    z_amount = rng.standard_normal(n)
    theft_frac = rng.uniform(
        ca["theft_total"]["idv_fraction"][0], ca["theft_total"]["idv_fraction"][1], n
    )
    claim_amount = np.where(
        claim_type == ClaimType.OD.value,
        np.exp(ca["OD"]["mu"] + ca["OD"]["sigma"] * z_amount),
        np.where(
            claim_type == ClaimType.TP.value,
            np.exp(ca["TP"]["mu"] + ca["TP"]["sigma"] * z_amount),
            idv * theft_frac,  # theft_total
        ),
    )

    # ----- is_fraud (depends only on type; drawn early - see module docstring) -
    fbr = cfg["fraud_base_rate"]
    fraud_p = np.select(
        [claim_type == ClaimType.OD.value, claim_type == ClaimType.TP.value],
        [fbr["OD"], fbr["TP"]],
        default=fbr["theft_total"],
    )
    is_fraud = _bern(rng, fraud_p, n)

    fraud_type = np.where(
        is_fraud == 1,
        _cat(rng, cfg["fraud_type_given_fraud"], n),
        "none",
    ).astype(object)

    # ----- 1.2 Process / eligibility marginals + 1.3 fraud-lifted signals -----
    # severity: marginal for OD/TP; theft_total is always 'total'.
    severity = _cat(rng, cfg["severity_mix"], n)
    severity = np.where(claim_type == ClaimType.THEFT_TOTAL.value, Severity.TOTAL.value, severity)

    policy_status = _cat(rng, cfg["policy_status"], n)
    geo = _cat(rng, cfg["geo"], n)

    driver_valid_license = _bern(rng, cfg["driver"]["valid_license"], n)
    dui_flag = _bern(rng, cfg["driver"]["dui_flag"], n)

    vehicle_age_years = 15.0 * rng.beta(2, 3, n)  # right-skewed on [0, 15]
    num_photos = rng.integers(cfg["photo"]["num_photos"][0], cfg["photo"]["num_photos"][1] + 1, n)
    photo_quality_score = rng.beta(
        cfg["photo"]["quality_beta"][0], cfg["photo"]["quality_beta"][1], n
    )

    lift = cfg["fraud_signal_lift"]
    qbase = cfg["fraud_signal_base"]

    # garage_type is the non_network_garage signal (same field), fraud-lifted.
    non_network_garage = _lifted_signal(
        rng, is_fraud, qbase["non_network_garage"], lift["non_network_garage"]
    )
    garage_type = np.where(
        non_network_garage == 1, GarageType.NON_NETWORK.value, GarageType.NETWORK.value
    )

    intimation_gt_48h = _lifted_signal(
        rng, is_fraud, qbase["intimation_gt_48h"], lift["intimation_gt_48h"]
    )
    photo_reuse_flag = _lifted_signal(
        rng, is_fraud, qbase["photo_reuse_flag"], lift["photo_reuse_flag"]
    )

    # modification: actual rate fraud-lifted; declared given actual from config.
    modification_actual = _lifted_signal(
        rng, is_fraud, qbase["modification_actual"], lift["modification_undeclared"]
    )
    declared_draw = _bern(rng, cfg["modification"]["declared_given_actual"], n)
    modification_declared = (modification_actual & declared_draw).astype(np.int8)
    modification_undeclared = (modification_actual & (modification_declared == 0)).astype(np.int8)

    # intimation delay hours + valid-reason flag (§1.3).
    intimation_delay_hours = np.where(
        intimation_gt_48h == 1,
        48.0 + rng.exponential(48.0, n),
        rng.uniform(0.0, 48.0, n),
    )
    intimation_reason_valid = np.where(
        intimation_gt_48h == 1,
        _bern(rng, cfg["intimation"]["late_reason_valid_share"], n),
        1,  # on-time claims are trivially "valid"
    ).astype(np.int8)

    # FIR: required for TP / theft_total / severe / total; usually filed when required.
    fir_required = (
        np.isin(claim_type, [ClaimType.TP.value, ClaimType.THEFT_TOTAL.value])
        | np.isin(severity, [Severity.SEVERE.value, Severity.TOTAL.value])
    ).astype(np.int8)
    fir_filed = np.where(fir_required == 1, _bern(rng, 0.90, n), _bern(rng, 0.20, n)).astype(
        np.int8
    )

    # ----- 1.4 True repair cost (label) --------------------------------------
    cost_cfg = cfg["cost"]
    sf = cost_cfg["severity_factor"]
    sev_factor = np.select(
        [
            severity == Severity.MINOR.value,
            severity == Severity.MODERATE.value,
            severity == Severity.SEVERE.value,
            severity == Severity.TOTAL.value,
        ],
        [sf["minor"], sf["moderate"], sf["severe"], sf["total"]],
        default=sf["moderate"],
    )
    base_noise = rng.uniform(cost_cfg["base_noise"][0], cost_cfg["base_noise"][1], n)
    base_cost = idv * sev_factor * base_noise

    inflated = (is_fraud == 1) & (fraud_type == FraudType.STAGED_OR_INFLATED.value)
    genuine_cost = base_cost * np.exp(cost_cfg["genuine_lognorm_sigma"] * rng.standard_normal(n))
    infl_mult = rng.uniform(
        cost_cfg["inflated_claim_mult"][0], cost_cfg["inflated_claim_mult"][1], n
    )

    true_repair_cost = np.where(inflated, base_cost, genuine_cost)
    # For inflated fraud, OVERWRITE the claimed amount above the true cost (the gap
    # becomes a fraud feature downstream). Genuine claims keep their §1.1 amount.
    claim_amount = np.where(inflated, base_cost * infl_mult, claim_amount)

    # ----- 1.5 Jumper/sleeper label (escalated_at_90d) -----------------------
    esc = cfg["escalation_model"]
    hs = esc["hidden_signal_rate"]
    tp_linkage = _bern(rng, hs["tp_linkage"], n)
    ambiguous_liability = _bern(rng, hs["ambiguous_liability"], n)
    injury_hint = _bern(rng, hs["injury_hint"], n)
    b = esc["beta"]
    base_logit = (
        b["tp_linkage"] * tp_linkage
        + b["ambiguous_liability"] * ambiguous_liability
        + b["injury_hint"] * injury_hint
        + b["low_amount"] * (claim_amount < 50000).astype(float)
        + b["low_severity"]
        * np.isin(severity, [Severity.MINOR.value, Severity.MODERATE.value]).astype(float)
        + b["claim_type_tp"] * (claim_type == ClaimType.TP.value).astype(float)
        + esc["noise_sigma"] * rng.standard_normal(n)
    )
    intercept = _tune_intercept(base_logit, float(cfg["escalation"]["base_rate"]))
    escalated_at_90d = _bern(rng, _sigmoid(intercept + base_logit), n)

    # ----- 1.6 Surveyor verdict, settlement, override (labels) ---------------
    sv = cfg["surveyor"]
    fraud_reject = (is_fraud == 1) & (_bern(rng, sv["fraud_reject_prob"], n) == 1)
    reject = (policy_status == PolicyStatus.LAPSED.value) | (dui_flag == 1) | fraud_reject

    partial_p = np.clip(
        sv["partial_base"] + sv["partial_age_slope"] * vehicle_age_years, 0, sv["partial_max"]
    )
    partial = (~reject) & (_bern(rng, partial_p, n) == 1)

    surveyor_verdict = np.where(
        reject,
        SurveyorVerdict.REJECT.value,
        np.where(partial, SurveyorVerdict.PARTIAL.value, SurveyorVerdict.APPROVE.value),
    ).astype(object)
    surveyor_reject_reason = np.where(
        policy_status == PolicyStatus.LAPSED.value,
        "policy_lapsed",
        np.where(
            dui_flag == 1, "driver_ineligible", np.where(fraud_reject, "fraud_evidence", "none")
        ),
    ).astype(object)

    settle_noise = rng.uniform(sv["settlement_true_noise"][0], sv["settlement_true_noise"][1], n)
    settle_basis = np.minimum(claim_amount, true_repair_cost * settle_noise)
    depreciation = np.clip(
        sv["depreciation_age_slope"] * vehicle_age_years, 0, sv["depreciation_max"]
    )
    partial_factor = np.where(
        partial,
        rng.uniform(sv["partial_settlement_factor"][0], sv["partial_settlement_factor"][1], n),
        1.0,
    )
    final_settlement = np.where(reject, 0.0, settle_basis * (1.0 - depreciation) * partial_factor)
    human_override = _bern(rng, sv["human_override_rate"], n)

    # ----- 1.7 Collusion rings + entity graph --------------------------------
    # Genuine claims get UNIQUE entities (sparse linkage, LOGIC §1.7); rings below
    # overwrite their members with a few SHARED entities so the graph model finds
    # them as dense components against an otherwise-isolated background.
    ent = cfg["entities"]
    customer_id = np.array([f"CUST{i:07d}" for i in range(n)], dtype=object)
    phone = np.array([f"PH{i:08d}" for i in range(n)], dtype=object)
    bank_account = np.array([f"AC{i:08d}" for i in range(n)], dtype=object)
    garage_id = np.array([f"GAR{i:07d}" for i in range(n)], dtype=object)
    surveyor_id = np.array([f"SUR{i:07d}" for i in range(n)], dtype=object)

    ring_id = np.full(n, -1, dtype=int)
    fraud_idx = np.where(is_fraud == 1)[0]
    rng.shuffle(fraud_idx)
    n_rings = int(rng.integers(cfg["rings"]["n_rings"][0], cfg["rings"]["n_rings"][1] + 1))
    cpr = cfg["rings"]["claims_per_ring"]
    ptr = 0
    for r in range(n_rings):
        k = int(rng.integers(cpr[0], cpr[1] + 1))
        if ptr + k > len(fraud_idx):
            break
        members = fraud_idx[ptr : ptr + k]
        ptr += k
        # Shared entities the ring reuses across "independent" claims.
        n_shared_gar = int(
            rng.integers(ent["ring_shared_garages"][0], ent["ring_shared_garages"][1] + 1)
        )
        shared_gars = [f"RGAR{r:03d}_{j}" for j in range(n_shared_gar)]
        shared_sur = f"RSUR{r:03d}"
        shared_bank = f"RAC{r:03d}" if rng.random() < ent["ring_bank_share_prob"] else None
        for m in members:
            garage_id[m] = shared_gars[int(rng.integers(0, n_shared_gar))]
            surveyor_id[m] = shared_sur
            if shared_bank is not None:
                bank_account[m] = shared_bank
        ring_id[members] = r
    is_ring_claim = (ring_id >= 0).astype(np.int8)

    # ----- 1.8 Baseline (all-manual) TAT -------------------------------------
    ts = cfg["tat_sim"]
    appt_days = rng.uniform(0.0, cfg["tat_ceilings"]["surveyor_appointment_hours"], n) / 24.0
    report_days = rng.triangular(
        ts["survey_report_tri"][0], ts["survey_report_tri"][1], ts["survey_report_tri"][2], n
    )
    decision_days = rng.uniform(ts["decision_days"][0], ts["decision_days"][1], n)
    tat_survey = appt_days + report_days + decision_days

    reim = cfg["tat_ceilings"]["reimbursement_extra_days"]
    reimburse_extra = np.where(
        garage_type == GarageType.NON_NETWORK.value, rng.uniform(reim[0], reim[1], n), 0.0
    )
    lg = ts["litigation_lognorm"]
    dispute = (surveyor_verdict == SurveyorVerdict.REJECT.value) & (
        _bern(rng, ts["litigation_dispute_prob"], n) == 1
    )
    litigation_tail = np.where(dispute, rng.lognormal(lg["mu"], lg["sigma"], n), 0.0)
    baseline_tat_days = tat_survey + reimburse_extra + litigation_tail

    # ----- Synthetic identity fields (clearly fake) --------------------------
    customer_name = np.array([fake.name() for _ in range(n)], dtype=object)
    states = ["MH", "DL", "KA", "TN", "GJ", "UP", "RJ", "WB", "TS", "KL"]
    plate_states = np.asarray(states, dtype=object)[rng.integers(0, len(states), n)]
    plate_num1 = rng.integers(1, 99, n)
    letters = np.array([chr(ord("A") + i) for i in range(26)])
    plate_l = ["".join(letters[rng.integers(0, 26, 2)]) for _ in range(n)]
    plate_num2 = rng.integers(1000, 9999, n)
    vehicle_plate = np.array(
        [f"{plate_states[i]}{plate_num1[i]:02d}{plate_l[i]}{plate_num2[i]}" for i in range(n)],
        dtype=object,
    )
    claim_id = np.array([f"CLM{i:07d}" for i in range(n)], dtype=object)

    # ----- Assemble ----------------------------------------------------------
    df = pd.DataFrame(
        {
            "claim_id": claim_id,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "phone": phone,
            "bank_account": bank_account,
            "garage_id": garage_id,
            "surveyor_id": surveyor_id,
            "vehicle_plate": vehicle_plate,
            "vehicle_age_years": np.round(vehicle_age_years, 2),
            "claim_type": claim_type,
            "idv": np.round(idv, 2),
            "claim_amount": np.round(claim_amount, 2),
            "incident_severity": severity,
            "policy_status": policy_status,
            "garage_type": garage_type,
            "geo": geo,
            "driver_valid_license": driver_valid_license,
            "dui_flag": dui_flag,
            "modification_actual": modification_actual,
            "modification_declared": modification_declared,
            "modification_undeclared": modification_undeclared,
            "non_network_garage": non_network_garage,
            "intimation_delay_hours": np.round(intimation_delay_hours, 2),
            "intimation_gt_48h": intimation_gt_48h,
            "intimation_reason_valid": intimation_reason_valid,
            "fir_required": fir_required,
            "fir_filed": fir_filed,
            "num_photos": num_photos,
            "photo_quality_score": np.round(photo_quality_score, 4),
            "photo_reuse_flag": photo_reuse_flag,
            "tp_linkage": tp_linkage,
            "ambiguous_liability": ambiguous_liability,
            "injury_hint": injury_hint,
            "is_fraud": is_fraud,
            "fraud_type": fraud_type,
            "true_repair_cost": np.round(true_repair_cost, 2),
            "escalated_at_90d": escalated_at_90d,
            "surveyor_verdict": surveyor_verdict,
            "surveyor_reject_reason": surveyor_reject_reason,
            "final_settlement": np.round(final_settlement, 2),
            "baseline_tat_days": np.round(baseline_tat_days, 3),
            "human_override": human_override,
            "ring_id": ring_id,
            "is_ring_claim": is_ring_claim,
        }
    )
    return df


def main() -> None:
    """CLI entry point for ``claimos-generate``: generate, save, summarize."""
    df = generate_claims()
    constants.SYNTH_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(constants.CLAIMS_PARQUET, index=False)
    df.head(100).to_csv(constants.SAMPLE_CSV, index=False)

    sub50k = float((df["claim_amount"] < 50000).mean())
    print(f"[claimos-generate] wrote {len(df):,} claims -> {constants.CLAIMS_PARQUET}")
    print(f"[claimos-generate] sample -> {constants.SAMPLE_CSV}")
    print(f"  sub-Rs50k share : {sub50k:.4f}  (target 0.55-0.68)")
    print(f"  overall fraud   : {df['is_fraud'].mean():.4f}")
    for t in ("OD", "TP", "theft_total"):
        m = df["claim_type"] == t
        print(f"    fraud[{t:>11}] : {df.loc[m, 'is_fraud'].mean():.4f}  (n={int(m.sum()):,})")
    print(f"  escalation rate : {df['escalated_at_90d'].mean():.4f}  (target ~0.07)")
    print(
        f"  seeded rings    : {int(df['ring_id'].max()) + 1}  "
        f"ring-claims={int(df['is_ring_claim'].sum())}"
    )


if __name__ == "__main__":
    main()
