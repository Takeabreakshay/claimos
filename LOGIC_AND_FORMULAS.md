# LOGIC_AND_FORMULAS.md — ClaimOS

The exact math and decision logic behind every step. This is the **implementation-level** companion to
`CLAUDE.md` (which owns the rules and structure). Where the two ever seem to differ, `CLAUDE.md` wins on
*rules*, this file wins on *formulas*. Notation is kept plain; all parameters referenced as
`config.<name>` live in `config/distributions.yaml` or `config/thresholds.yaml`.

### Notation
- `Z ~ N(0,1)` standard normal; `U(a,b)` uniform; `Bern(p)` Bernoulli; `Cat(w)` categorical with weights `w`.
- `σ(x) = 1 / (1 + e^-x)` logistic sigmoid; `logit(p) = ln(p/(1-p))`.
- `clip(x, lo, hi)` clamps to `[lo, hi]`. `𝟙[·]` is the indicator (1 if true else 0).
- Every draw uses the seeded RNG (`SEED = 42`).

---

## Phase 1 — Synthetic data generation (`data_gen.py`)

Generate `N = config.n_claims` rows. For each claim, draw in this order (order matters — later draws depend on earlier).

### 1.1 Claim type & amounts
```
type        ~ Cat(config.claim_type_mix)                      # OD / TP / theft_total
idv         = exp(μ_idv + σ_idv · Z)                          # μ_idv=13.02, σ_idv=0.50
claim_amount:
   if type == OD:          exp(μ_OD + σ_OD · Z)               # μ=9.80,  σ=0.90
   if type == TP:          exp(μ_TP + σ_TP · Z)               # μ=11.35, σ=1.00
   if type == theft_total: idv · U(0.90, 1.00)
```
`claim_amount` is the **claimed** figure (what the customer/garage states). The *true* cost is drawn in 1.4 and may differ — that gap is a fraud signal, so **the cost model must NOT take `claim_amount` as an input**.

**Acceptance:** assert `share(claim_amount < 50000) ∈ config.acceptance.sub_50k_share` (0.55–0.68). If outside, nudge `μ_OD` and re-log in `ASSUMPTIONS.md`.

### 1.2 Process & eligibility fields (marginals)
```
severity     ~ Cat(config.severity_mix)  (theft_total ⇒ 'total')
policy_status~ Cat(config.policy_status)                 # active/lapsed
garage_type  ~ Cat(config.garage_type)                  # network/non_network
geo          ~ Cat(config.geo)
driver_valid_license ~ Bern(config.driver.valid_license)
dui_flag             ~ Bern(config.driver.dui_flag)
modification_actual  ~ Bern(config.modification.actual_rate)
modification_declared= modification_actual · Bern(config.modification.declared_given_actual)
vehicle_age  ~ right-skewed on [0,15]  (e.g. 15·Beta(2,3))
num_photos   ~ U_int(2, 8);  photo_quality ~ Beta(5,2)  # ∈ [0,1]
```

### 1.3 Fraud label + correlated signals (the learnable core)
Draw fraud first, then bias the fraud-linked signals so a classifier can recover it — but noisily (target AUC ~0.80–0.90, **not** 0.99).
```
is_fraud ~ Bern(config.fraud_base_rate[type])

# For each fraud-linked binary signal s with genuine marginal q_s and lift ℓ_s (config.fraud_signal_lift):
P(s=1 | is_fraud=0) = q_s
P(s=1 | is_fraud=1) = clip(q_s + ℓ_s, 0, 0.98)
s ~ Bern( P(s=1 | is_fraud) )
```
Apply to: `non_network_garage` (q≈0.35), `intimation_gt_48h` (q≈0.15), `modification_undeclared` (derived from 1.2), `photo_reuse_flag` (q≈0.05). Because fraud ≈ 12%, the overall marginals stay ≈ their config targets; if the acceptance test on a marginal drifts >2pt, rescale `q_s` and log it.
```
fraud_type = Cat(config.fraud_type_given_fraud) if is_fraud else 'none'
intimation_delay_hours = 0..48 if not intimation_gt_48h else 48 + Exp(mean=48)
intimation_reason_valid = Bern(config.intimation.late_reason_valid_share) if intimation_gt_48h else True
```

### 1.4 True repair cost (label for the cost model)
```
base_cost = f(vehicle features)                # NOT claim_amount
          = idv · severity_factor · U(0.9,1.1)
   severity_factor: minor 0.03 · moderate 0.09 · severe 0.22 · total 0.95   # of IDV (assumption, log it)
if is_fraud and fraud_type == 'staged_or_inflated':
   true_repair_cost = base_cost               # claimed is inflated above true
   claim_amount     = base_cost · U(1.3, 2.5) # overwrite: inflated claim
else:
   true_repair_cost = base_cost · exp(0.12 · Z)   # genuine: claim ≈ true + mild noise
```
This yields a **claim-vs-true gap** for inflated fraud → later a strong fraud feature (`claim_to_predicted_ratio`).

### 1.5 Jumper/sleeper label (`escalated_at_90d`)
Latent logistic on FNOL-visible + hidden signals, kept weak so it's hard to see early (target AUC ~0.70):
```
tp_linkage, ambiguous_liability, injury_hint ~ Bern(small)      # hidden-ish signals
z = β0 + β1·tp_linkage + β2·ambiguous_liability + β3·injury_hint
    + β4·𝟙[claim_amount < 50000] + β5·𝟙[severity ∈ {minor,moderate}] + 0.5·Z
escalated_at_90d ~ Bern( σ(z) )      # tune β0 so mean ≈ config.escalation.base_rate (0.07)
```

### 1.6 Surveyor verdict, settlement, override (labels)
```
surveyor_verdict:
   'reject'  if policy_status==lapsed OR (dui_flag) OR (is_fraud with strong evidence)
   'partial' if depreciation/consumables dispute (prob rises with vehicle_age)
   'approve' otherwise
final_settlement = 0 if reject; else min(claim_amount, true_repair_cost·U(0.9,1.05)) − depreciation
human_override ~ Bern(0.05..0.15) higher near decision boundaries   # feeds the feedback loop
```

### 1.7 Collusion rings (for the graph model)
```
R ~ U_int(config.rings.n_rings)                     # 30–50 rings
for each ring r:
   shared = {1–2 garage_id, 1 surveyor_id, maybe 1 bank_account}   # reused entities
   k_r ~ U_int(config.rings.claims_per_ring)          # 5–20 claims
   assign k_r fraud claims that all reference `shared`
non-ring claims: unique/random garage_id, surveyor_id, bank_account, phone
```

### 1.8 Baseline TAT (all-manual world — the comparison point)
Every claim is surveyed (no triage):
```
tat_survey  = appt(≤24h)/24 + report ~ Tri(2, 8, 15) + decision ~ U(1, 7)     # days
tat_base    = tat_survey
            + (U(15,30) if garage_type==non_network else 0)                    # reimbursement route
            + (litigation_tail if surveyor_verdict=='reject' else 0)
litigation_tail ~ LogNormal(mean≈120 days) but only for rejected & disputed    # the 3–12+ month tail
```
Ceilings come from `config.tat_ceilings` ([SOURCED] — do not exceed the Master-Circular limits).

---

## Phase 3 — Feature engineering (`features.py`)

Derived features (all deterministic transforms; no leakage of labels):
```
claim_to_idv_ratio        = claim_amount / idv
predicted_cost, P10, P90  = cost_model(features)                 # from §3.1
claim_to_predicted_ratio  = claim_amount / max(predicted_cost, 1)   # >1 ⇒ possible inflation
cost_band_width           = (P90 − P10) / max(P50, 1)
days_to_policy_expiry     = policy_end − incident_date
intimation_ok             = 𝟙[intimation_delay_hours ≤ 48  OR  intimation_reason_valid]
mod_mismatch              = 𝟙[modification_actual AND NOT modification_declared]
fir_required              = 𝟙[type ∈ {TP, theft_total} OR severity ∈ {severe, total}]
fir_ok                    = 𝟙[NOT fir_required OR fir_filed]
# graph features from §3.5: component_size, shared_garage_count, shared_surveyor_count, ring_risk
```
Encodings: ordinal for `severity` (minor<moderate<severe<total); one-hot for `type`, `geo`, `garage_type`. Trees don't need scaling.

---

## Models

### 3.1 Repair-cost estimator — LightGBM quantile regression
Train **three** models at τ ∈ {0.10, 0.50, 0.90} with the **pinball (quantile) loss**:
```
L_τ(y, ŷ) = max( τ·(y − ŷ),  (τ − 1)·(y − ŷ) )
```
Outputs: `P10, P50, P90` (`P50` is the point estimate). Enforce `P10 ≤ P50 ≤ P90` (sort if needed).
**Cost certainty** (used in confidence):
```
c_cost = 1 − clip( (P90 − P10) / (2 · P50),  0, 1 )      # tight band ⇒ high certainty
```
Target: MAPE(P50) < 0.25.

### 3.2 Fraud classifier — LightGBM binary
Objective = **binary log-loss (cross-entropy)**:
```
L = −(1/N) Σ [ y·ln(p̂) + (1−y)·ln(1−p̂) ]
```
Features include the graph features (§3.5) and `claim_to_predicted_ratio` (§3). Raw output `p_raw` → **calibrate** (§3.6) → `p_fraud`. Target ROC-AUC > 0.80.

### 3.3 Escalation classifier — LightGBM binary
Same log-loss objective; target `escalated_at_90d`. Uses only **FNOL-visible** features (no leakage of the hidden latent). Calibrate → `p_esc`. Target ROC-AUC > 0.70.

### 3.4 Coverage engine — deterministic rules (`coverage.py`)
Pure boolean logic, unit-tested (no ML):
```
coverage_clear = TRUE
if policy_status == 'lapsed':            coverage_clear=False; reason='policy_lapsed'
elif not driver_valid_license or dui_flag: coverage_clear=False; reason='driver_ineligible'
elif fir_required and not fir_filed:     coverage_clear=False; reason='fir_missing'
elif mod_mismatch:                       coverage_clear='flag'; reason='undeclared_modification'
# CRUCIAL fairness rule (SOURCED, SC rulings): late intimation ALONE is NOT a valid reject.
if intimation_gt_48h and intimation_reason_valid:
    legal_weak_reject_flag = TRUE        # never auto-reject on this ground; route to human
```

### 3.5 Collusion graph — networkx (`graph.py`)
Build a graph linking claims that share high-risk entities:
```
Nodes = claims.  Edge(i,j) if claims i,j share ≥1 of {garage_id, surveyor_id, bank_account, phone}.
component_size(i)      = size of connected component containing claim i
shared_garage_count(i) = # other claims sharing i's garage_id     (similarly surveyor, account)
ring_risk(i)           = 1 − exp( −λ · (component_size(i) − 1) ),   λ = 0.3
```
`ring_risk` → 0 for isolated (genuine) claims, → 1 inside dense rings. Feed `component_size`,
`shared_*_count`, `ring_risk` into the fraud model (§3.2).
**Detection eval:** flag components with `size ≥ 5` as rings; require recall ≥ 0.80 vs seeded rings (§1.7).

### 3.6 Calibration (`calibration.py`)
Map raw classifier scores to trustworthy probabilities on a **held-out** split.
- **Isotonic** (preferred at 50k rows): fit a monotonic non-decreasing `g(s)→p` minimizing `Σ(g(sᵢ)−yᵢ)²`.
- **Platt** (fallback, small data): `p_cal = σ(a·s + b)`, fit `a,b` by MLE.
Quality:
```
Brier = (1/N) Σ (pᵢ − yᵢ)²                                  # target < 0.15
ECE   = Σ_b (n_b/N) · | acc_b − conf_b |                    # over 10 confidence bins; report
```
Plot reliability curves to `reports/`.

---

## Confidence combination (used by the triage gate)

Per-component **certainty** (distance from the decision boundary), then aggregate:
```
c_fraud = 2 · | p_fraud − 0.5 |          # 0 at p=0.5, 1 at p∈{0,1}
c_esc   = 2 · | p_esc   − 0.5 |
c_cost  = 1 − clip( (P90−P10)/(2·P50), 0, 1 )      # from §3.1
model_confidence = min( c_fraud, c_esc, c_cost )   # weakest link governs
```
Direction is handled separately by the threshold gates (a *confident fraud* has high `c_fraud` but is caught
by `max_fraud_prob` and sent to Lane 3 — not Lane 1). `model_confidence` only measures *certainty*.

---

## Phase 4 — Triage routing (`triage.py`)

Reads `config/thresholds.yaml`. Deterministic; emits the triggered reasons at each step.

**Step A — evidence-gap gate**
```
critical_conf = min( c_fraud, c_cost, (c_esc), coverage_confidence )
if (critical_conf < config.evidence_gap.min_component_confidence  OR  required_doc_missing)
   and loops_used < config.evidence_gap.max_retake_loops:
      request retake/doc ; loops_used += 1 ; recompute signals ; restart Step A
```

**Step B — Lane 3 (Investigative): ANY trigger ⇒ Lane 3**
```
LANE3 =  (p_fraud ≥ min_fraud_prob=0.50)
      OR (claim_amount ≥ high_value_threshold=200000)
      OR (severity == 'total')
      OR (p_esc ≥ min_escalation_prob=0.50)
      OR (type=='TP' AND injury_hint)
```

**Step C — Lane 1 (Touchless): ALL must hold ⇒ Lane 1**
```
LANE1 =  (claim_amount < max_claim_amount=50000)          # ₹50k ANCHOR
     AND (p_fraud < max_fraud_prob=0.10)
     AND (model_confidence ≥ min_confidence=0.85)
     AND (p_esc < max_escalation_prob=0.15)
     AND (severity ∈ {minor, moderate})
     AND (coverage_clear == True)
     AND (intimation_ok == True)
```

**Step D — else Lane 2 (Assisted).**  Routing order is strictly B → C → D.
```
lane = LANE3 ? 3 : (LANE1 ? 1 : 2)
```

---

## TAT simulation (in `evaluate.py`)

Per-lane realized TAT (days):
```
Lane 1:  U(0.02, 1)                                             # minutes–hours
Lane 2:  U(0.5, 3)  + (U(15,30) if non_network else 0)          # +reimbursement route
Lane 3:  Tri(15, 30, 90) + (litigation_tail if reject else 0)   # survey path + tail
```
Aggregate (report both median and mean; median is the headline):
```
TAT_after  = distribution over all claims by their assigned lane
TAT_base   = tat_base from §1.8 (all-manual)
TAT_improvement = (median(TAT_base) − median(TAT_after)) / median(TAT_base)
touchless_share = |Lane1| / N
```

---

## Phase 6 — Evaluation metrics (`evaluate.py`)

**The make-or-break metric — Lane-1 leakage (fraud auto-cleared):**
```
leakage_count = |{ claims : lane==1 AND is_fraud }| / |{ lane==1 }|
leakage_value = Σ_{lane1 ∧ fraud} final_settlement  /  Σ_{lane1} final_settlement
```
Report both; `leakage_count` is gated against `config.guardrails.lane1_leakage_ceiling` (0.015).

**Classifiers (fraud, escalation):**
```
Precision = TP/(TP+FP)      Recall = TP/(TP+FN)      F1 = 2PR/(P+R)
ROC-AUC   = P( score(positive) > score(negative) )
```
**Cost model:**
```
MAE  = mean |y − ŷ|          MAPE = mean( |y − ŷ| / max(y, ε) )
```
**Calibration:** Brier, ECE (from §3.6).
**Appeal-rate proxy (fairness win):**
```
appeals_avoided = |{ legal_weak_reject_flag=True routed to human }| / |{ would-be legal-weak rejects }|
```

---

## Guardrail: auto-tighten loop (never ship a leaky Lane 1)

Runs at the end of evaluation:
```
while leakage_count(Lane1) > config.guardrails.lane1_leakage_ceiling  and  |Lane1| > 0:
    thresholds.min_confidence   += 0.02
    thresholds.max_fraud_prob    = max(0, max_fraud_prob − 0.01)
    re-run Step C routing
    recompute leakage_count
report: final thresholds, final leakage, resulting touchless_share
```
This trades touchless-share *down* to keep leakage under the ceiling — never the reverse. Log the final
thresholds so the deck quotes the *shipped* numbers, not the pre-tightening ones.

---

## Explainability (`explain.py`)

Per routed claim, produce reason codes:
```
SHAP:  f(x) = φ0 + Σ_i φ_i(x)          # TreeSHAP for LightGBM; φ_i = feature i's contribution
top_drivers = features ranked by |φ_i| for fraud, cost, escalation (top 3 each)
rule_hits   = coverage reasons + threshold triggers that decided the lane
plain_reason= template( lane, top_drivers, rule_hits, legal_weak_reject_flag )
```
Every decision must return non-empty `top_drivers`, `rule_hits`, and a one-line `plain_reason` (PwC lesson:
trust needs the *why*, not just the *what*). LLM polishing of `plain_reason` is optional and needs no key.

---

## Sanity invariants (assert somewhere in tests)
- `P10 ≤ P50 ≤ P90` for every claim.
- Every `p_fraud, p_esc ∈ [0,1]`; calibrated Brier < 0.15.
- No claim in Lane 1 has `claim_amount ≥ 50000`, `severity ∈ {severe,total}`, or `coverage_clear == False`.
- `leakage_count(Lane1) ≤ 0.015` after the auto-tighten loop.
- Re-running the whole pipeline with `SEED=42` reproduces identical metrics.
