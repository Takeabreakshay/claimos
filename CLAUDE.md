# CLAUDE.md — ClaimOS Triage Engine · Build Brief & Source of Truth

> **Read this file fully before writing any code, and re-read the RULES section before every phase.**
> This is the single source of truth for the build. If anything you are about to do conflicts with this file, STOP and flag it. Do not invent product concepts, statistics, or integrations that are not in here.

---

## 0. What we are building (one paragraph)

**ClaimOS is not one model. It is a decision layer** that scores an incoming motor-insurance claim and routes it into one of three execution lanes based on *how much automation that specific claim deserves*. Under the hood it is 3 predictive models + 1 rules engine + 1 calibration layer + 1 graph check, combined by a **risk-triage routing policy** (the "wedge"). We are building a **working prototype on calibrated synthetic data** plus an **interactive demo**, with an evaluation harness that produces real, defensible numbers. This is for a case competition (Bajaj Finserv ATOM Season 9); the judge is an enterprise CTO who will attack feasibility.

**The thesis:** TAT is high because every claim — a ₹8,000 dent or a total loss — runs the same heavy, serialized, manual path. The fix is not more staff; it is **risk-based triage** so effort goes where risk actually is.

---

## 1. The product logic (DO NOT CHANGE without flagging)

### 1.1 The three lanes
| Lane | Name | Who handles | Target speed | When |
|------|------|-------------|--------------|------|
| **1** | Touchless | Straight-through, no human | Minutes | Low value (< ₹50k) · low fraud · high confidence · low severity · no latent-escalation risk |
| **2** | Assisted | AI-prepared file, claims officer approves | Hours | Medium risk / medium confidence — the default |
| **3** | Investigative | Surveyor + fraud investigator | Days–weeks | High value · fraud flags · severe/total · TP with injury · high escalation risk |

### 1.2 The triage score inputs (the wedge)
Every claim is scored on: **value × model-confidence × fraud-signal × severity × latent-escalation (jumper/sleeper)**, and routed. The **₹50,000 surveyor-mandate seam is the anchor of Lane 1** (see §4). Everything else — OCR, CV, cost engine, fraud engine — is an *input that feeds the triage engine*, never the headline.

### 1.3 The evidence-gap gate
Before routing, if any critical signal has confidence below a floor, or a required document is missing, the claim loops back once for a retake/re-submission (bounded — max 1 loop). Only then does it route.

### 1.4 Non-negotiable framing
"The workflow is end-to-end and automated; the **decision path is risk-based**." We do not automate blindly, we automate intelligently. Low confidence always routes to a human.

---

## 2. Research facts you must build to (the ONLY authoritative numbers)

These come from IRDAI Annual Report 2024-25, the 2024 IRDAI Master Circular, IIB, and named case studies. **Use these and only these as sourced facts. Do not fabricate additional statistics.** Anything else you need is a *modeling assumption* — label it as such in `config/` with a comment and log it in `ASSUMPTIONS.md`.

- **Surveyor mandatory for motor loss > ₹50,000** (proposal to raise to ₹75,000). → This is the Lane-1 value seam.
- **2024 Master Circular TAT norms:** surveyor appointment ≤ 24h · survey report ≤ 15 days · claims decision ≤ 7 days of report. → These define the "clock we beat" and the TAT simulation ceilings.
- **Motor = 32.21%** of non-life GDPI (2nd after health 41.42%). Largest single claims outlay ≈ **₹63,263 cr** (OD+TP).
- **Motor incurred-claims ratio (ICR) = 85.51%** and worsening. PSU motor ICR **107.94%** (loss-making). Bajaj Allianz **68.54%**. → Leakage is margin survival.
- **Fraud ≈ 10–15% of claims; leakage ≈ 8–10% of payouts** (all-lines, directional). **>50% of motor TP claims alleged bogus.** **~70% of general-insurance fraud = document falsification.**
- **Cashless settlement 1–7 days; reimbursement 15–30 days.**
- **IIB PRISM:** predictive motor risk-scoring, 4.8M cases (proves predictive triage works at scale — we mock it as a rail). **IIB QUEST:** fraud flagging (7.7M queries, 62,846 frauds).
- **Jumper/sleeper claims:** look trivial at FNOL, spike into high cost around the **90-day** mark.
- **PwC auto-claims finding:** the hard part was **trust, not computer vision**; explainable AI unlocked adoption and **29% efficiency**. → XAI is mandatory on every decision.
- **Quantified delay per stage** (our own dependency analysis, cite as indicative): verification ~1–3 days · survey ~2–15 days (biggest block) · repair-approval ~1–7 days · reimbursement 15–30 days · rejection→litigation ~3–12+ months.

---

## 3. Hard RULES — the model must not break these (re-read every phase)

1. **NO real data, NO real PII, ever.** 100% synthetic. Indian names/plates are synthetic and clearly fake.
2. **NO paid or production API calls.** VAHAN, DigiLocker, IIB, PRISM, core policy DB, payment rails are **MOCKED** behind clean interfaces in `src/rails.py`. Never attempt a real integration or ask for those keys. (The prototype requires **zero external API keys.**)
3. **NO fabricated statistics.** Only §2 numbers are "sourced." Every other number is a labeled assumption in config + `ASSUMPTIONS.md`.
4. **Reproducibility:** global `SEED = 42`. Every random draw seeded. Same input → same output, always.
5. **Config-driven, not hardcoded.** All distributions in `config/distributions.yaml`, all thresholds in `config/thresholds.yaml`. No magic numbers inside logic files.
6. **The ₹50,000 seam is the Lane-1 anchor.** Do not alter this logic without explicitly flagging why.
7. **Confidence must be CALIBRATED** (isotonic/Platt) before it is used in routing. Raw model scores are not confidence.
8. **Lane-1 leakage (auto-clearing a fraud) is the make-or-break metric.** Always compute and report it. NEVER improve "% in Lane 1" at the cost of leakage. Leakage has a hard ceiling (§7).
9. **Explainability is mandatory.** Every routed claim carries reason codes (SHAP for models + rule hits). No unexplained decisions.
10. **Right-sized tech only.** Gradient-boosted trees for tabular. NO deep learning for tabular. NO cloud. NO real computer vision unless explicitly added later (severity is derived/mocked from features in v1).
11. **Phase-gated.** Each phase has acceptance criteria (§10). Do not start a phase until the previous phase's tests pass. Run the phase's acceptance check and show the output before moving on.
12. **Don't rename or reinvent.** Lanes, modules, and the product name are fixed by §1. Don't add speculative features.
13. **When unsure, ASK or ASSUME-AND-LABEL — never silently guess.**

---

## 4. Repository structure (create exactly this)

```
claimos/
├── CLAUDE.md                  # this file (source of truth)
├── README.md                  # how to run
├── ASSUMPTIONS.md             # every non-sourced number, dated, with rationale
├── pyproject.toml             # Poetry: deps + env + tooling (the single manifest)
├── poetry.lock                # locked dependency set (commit it)
├── config/
│   ├── distributions.yaml     # synthetic data parameters (§5)
│   └── thresholds.yaml        # routing thresholds (§6)
├── src/
│   ├── __init__.py
│   ├── constants.py           # SEED, paths, enums (lanes, claim types, severities)
│   ├── data_gen.py            # Phase 1 — synthetic claim generator
│   ├── rails.py               # Phase 2 — MOCKED VAHAN/DigiLocker/IIB/PRISM interfaces
│   ├── features.py            # Phase 3 — feature engineering from raw claim + rails
│   ├── models/
│   │   ├── __init__.py
│   │   ├── cost.py            # repair-cost estimator (LightGBM quantile) + uncertainty
│   │   ├── fraud.py           # fraud classifier (LightGBM) + calibration
│   │   ├── escalation.py      # jumper/sleeper classifier + calibration
│   │   ├── coverage.py        # deterministic coverage/eligibility rules
│   │   └── graph.py           # networkx collusion-ring detection
│   ├── calibration.py         # isotonic/Platt wrappers + reliability curves
│   ├── triage.py              # Phase 4 — THE WEDGE: routing policy
│   ├── explain.py             # Phase 5 — SHAP + rule reason codes
│   ├── evaluate.py            # Phase 6 — metrics vs targets
│   └── pipeline.py            # orchestrates gen→train→route→eval end-to-end
├── demo/
│   └── app.py                 # interactive triage simulator (Streamlit) — OR a static HTML build
├── models/                    # saved model artifacts (.pkl / .txt)
├── data/synth/                # generated datasets (gitignored except a small sample)
├── reports/                   # eval JSON + figures (calibration, confusion, TAT)
└── tests/
    ├── test_data_gen.py
    ├── test_rails.py
    ├── test_triage.py
    └── test_eval.py
```

---

## 5. Synthetic data spec (Phase 1) — `config/distributions.yaml`

Generate **N = 50,000 claims** (configurable). Each row = one motor claim with FNOL fields, engineered signals, and **ground-truth target labels** (which is what makes supervised training possible). All parameters below go in `distributions.yaml`; treat them as **calibration assumptions consistent with §2**, logged in `ASSUMPTIONS.md`.

### 5.1 Claim mix & amounts
- `claim_type`: OD 0.75 · TP 0.18 · theft_total 0.07
- `claim_amount` (₹), by type — lognormal:
  - OD: `mu = ln(18000)`, `sigma = 0.90`  (→ majority < ₹50k, realistic dominance of small dents)
  - TP: `mu = ln(85000)`, `sigma = 1.00`  (→ mostly > ₹50k)
  - theft_total: `= idv * uniform(0.90, 1.00)`
- `idv` (insured declared value): lognormal `mu = ln(450000)`, `sigma = 0.50`
- **Acceptance:** overall share of claims with `claim_amount < 50000` must land in **55–68%** (assert in test). If not, adjust and log.

### 5.2 Risk & fraud labels (ground truth)
- `is_fraud` base rate by type: OD 0.08 · TP 0.20 · theft_total 0.15
- `fraud_type` (given fraud): document_falsification 0.70 · staged_or_inflated 0.20 · other 0.10
- Fraud must be **learnable but not trivial**: inject correlated-but-noisy signals (e.g. fraud raises P(non_network_garage), P(intimation_delay>48h), P(modification_undeclared), P(photo_reuse_flag), and clusters some fraud into rings — see §5.5). Keep signal imperfect so AUC lands ~0.80–0.90, not 0.99.

### 5.3 Severity, coverage, process fields
- `incident_severity`: minor 0.55 · moderate 0.30 · severe 0.15 (theft_total → `total`)
- `policy_status`: active 0.93 · lapsed 0.07 (lapsed → coverage reject)
- `garage_type`: network 0.65 · non_network 0.35 (non_network → reimbursement route, +15–30 days TAT)
- `intimation_delay_hours`: 85% within 48h; 15% > 48h (the "delayed intimation" trap). Add `intimation_reason_valid` bool for a subset (SC rulings: late intimation alone is not valid grounds → these must NOT be auto-rejected).
- `driver_valid_license` 0.95 · `dui_flag` 0.02 · `modification_declared` vs `modification_actual` (undeclared-mod subset)
- `fir_filed` (required for theft/TP/severe) · `photo_quality_score` ∈ [0,1] · `num_photos` · `photo_reuse_flag`
- `vehicle_age_years`: 0–15, right-skewed · `geo`: metro 0.4 · urban 0.4 · rural 0.2

### 5.4 Target labels to generate (for supervised training + eval)
- `true_repair_cost` (₹) — the label for the cost model (claim_amount is the *claimed* estimate; true cost differs, sometimes inflated when fraud).
- `is_fraud` (0/1), `fraud_type`
- `escalated_at_90d` (0/1) — **jumper/sleeper label**, base ~0.07, concentrated in claims that look minor/moderate & low-amount at FNOL but carry latent signals (e.g. TP linkage, ambiguous liability, injury hint). Must be *hard to see at FNOL* — that's the point.
- `surveyor_verdict`: approve / partial / reject (with reason enum)
- `final_settlement` (₹), `baseline_tat_days` (all-manual world), `human_override` (0/1)

### 5.5 Collusion rings (for the graph model)
Create a synthetic entity graph: `customer_id, phone, bank_account, garage_id, surveyor_id`. Seed ~30–50 fraud rings where a small set of garages/surveyors/accounts recur across many "independent" claims. The graph model (§7) must surface these dense subgraphs. Legit claims get sparse, random linkage.

---

## 6. Routing thresholds (Phase 4) — `config/thresholds.yaml`

```yaml
evidence_gap:
  min_component_confidence: 0.60   # below this on any critical signal -> retake loop
  max_retake_loops: 1

lane1_touchless:                   # ALL must hold
  max_claim_amount: 50000          # IRDAI surveyor-mandate seam (ANCHOR)
  max_fraud_prob: 0.10
  min_confidence: 0.85             # calibrated, min across components
  max_escalation_prob: 0.15
  allowed_severity: [minor, moderate]
  require_coverage_clear: true
  require_intimation_ok: true      # <=48h OR intimation_reason_valid

lane3_investigative:               # ANY triggers
  min_fraud_prob: 0.50
  high_value_threshold: 200000
  severities: [total]
  min_escalation_prob: 0.50
  tp_with_injury: true

# else -> lane2_assisted (the default)
```

Routing order: (1) run evidence-gap gate → retake if triggered; (2) check Lane 3 triggers → if any, Lane 3; (3) check ALL Lane 1 conditions → if all pass, Lane 1; (4) else Lane 2. Emit the triggered reasons at every step.

---

## 7. Models (Phase 3) — specs

| Model | File | Type | Target | Output | Target metric |
|-------|------|------|--------|--------|---------------|
| Repair-cost estimator | `models/cost.py` | LightGBM quantile regression (P10/P50/P90) | `true_repair_cost` | point estimate + uncertainty band | MAPE < 25% |
| Fraud classifier | `models/fraud.py` | LightGBM binary + graph features | `is_fraud` | calibrated fraud prob | ROC-AUC > 0.80 |
| Escalation (jumper/sleeper) | `models/escalation.py` | LightGBM binary | `escalated_at_90d` | calibrated escalation prob | ROC-AUC > 0.70 |
| Coverage engine | `models/coverage.py` | Deterministic rules | — | clear / not-clear + reason | correctness (unit-tested) |
| Collusion graph | `models/graph.py` | networkx (community/degree) | rings from §5.5 | ring flag + linked entities | recovers ≥ 80% seeded rings |
| Calibration | `calibration.py` | isotonic / Platt | — | reliability curve + Brier | Brier < 0.15 |

**Model confidence** used in routing = a principled combination (start with: min of calibrated component confidences relevant to the claim, plus a penalty for wide cost band). Document the formula in code.

Coverage rules (deterministic, from §2/§5): reject if `policy_status==lapsed`; flag if `modification_actual and not modification_declared`; flag if `dui_flag or not driver_valid_license`; require `fir_filed` for theft/TP/severe; **late intimation alone is NOT auto-reject** if `intimation_reason_valid` (SC rulings) → route to Lane 2 with a legal-check note, don't reject. This is the "fairer" behavior and the appeal-rate win.

---

## 8. Explainability (Phase 5)

Every decision returns a structured explanation:
- **Lane + why** (which thresholds/triggers fired).
- **Top SHAP contributors** for fraud, cost, escalation.
- **Rule hits** from coverage.
- **Plain-English reason** (template-based; optionally LLM-polished later — not required, no key needed for templates).
- **Legal-check flag** where a rejection would be legally weak (delayed-intimation etc.) → routes to human instead of reject.

---

## 9. Evaluation (Phase 6) — `evaluate.py`

Produce `reports/eval.json` + figures. Compare **baseline (all-manual: every claim surveyed, TAT ≈ Master-Circular ceilings)** vs **triaged**.

| Metric | Target | Note |
|--------|--------|------|
| **Lane-1 leakage / false-clear rate** | **< 1.5% (HARD CEILING)** | fraud auto-approved in Lane 1 — make-or-break |
| % claims in Lane 1 | report (expect ~40–55%) | never force up at leakage's expense |
| Fraud precision / recall / AUC | AUC > 0.80 | |
| Cost MAE / MAPE | MAPE < 25% | |
| Calibration (Brier, reliability) | Brier < 0.15 | |
| Median & p90 TAT: baseline vs triaged | show the drop | Lane1 minutes · Lane2 hours–days · Lane3 unchanged |
| Appeal-rate proxy | show reduction | % legally-weak rejections avoided |

TAT simulation must respect §2 ceilings (survey ≤15d, decision ≤7d; reimbursement +15–30d). If leakage > ceiling, the pipeline must **auto-tighten Lane 1 thresholds and re-report** — never ship a leaky Lane 1.

---

## 10. Phase plan & acceptance gates (build in this order)

- **Phase 0 — Scaffold.** Repo structure (§4). `pyproject.toml`, `config/*.yaml`, `README.md`, and `CLAUDE.md` are already provided — use them as-is; add deps only via `poetry add`. Create `constants.py`, empty modules, tests skeleton. **Gate:** `poetry install` clean; `poetry run pytest` runs (even if skips).
- **Phase 1 — Data engine.** `data_gen.py` + `distributions.yaml`. **Gate:** `test_data_gen` passes — 50k rows, no NaNs in required fields, `<₹50k` share in 55–68%, fraud rates within ±1pt of config, seed-reproducible (two runs identical).
- **Phase 2 — Mocked rails.** `rails.py` with `verify_vehicle()`, `verify_license()`, `get_claim_history()`, `get_prism_score()` — deterministic mocks keyed by claim, documented as swap points. **Gate:** `test_rails` passes; each returns typed, seeded output; a `# PRODUCTION: replace with real <X> API` comment on each.
- **Phase 3 — Features + models.** `features.py`, all `models/*`. **Gate:** each model trains, hits its §7 target metric on a holdout, artifacts saved to `models/`.
- **Phase 4 — Triage policy.** `triage.py` reading `thresholds.yaml`. **Gate:** `test_triage` — hand-crafted claims land in expected lanes (a clean <₹50k → Lane1; a fraud-ring claim → Lane3; a lapsed policy → coverage reject; a valid-reason late-intimation → Lane2 not reject; low-confidence → retake).
- **Phase 5 — Explainability.** `explain.py`. **Gate:** every routed claim returns non-empty reason codes + lane rationale.
- **Phase 6 — Eval.** `evaluate.py`, `pipeline.py`. **Gate:** `test_eval` — leakage < 1.5%, all §9 metrics computed, `reports/eval.json` + figures produced, baseline-vs-triaged TAT drop shown.
- **Phase 7 — Demo.** `demo/app.py`. **Gate:** enter a claim → see lane, confidence, reasons, cost band, fraud/escalation probs, and the legal-check flag.

At each gate: run the test, print the result, and summarize before proceeding.

---

## 11. Tech stack — managed by **Poetry** (`pyproject.toml` provided)

The project is structured with **Poetry**: one manifest (`pyproject.toml`), one lockfile, one virtual env.
Run everything via `poetry run ...`. The manifest is already written with the pinned set below — **do not
create a `requirements.txt`**; add any new dependency with `poetry add <pkg>` so the manifest stays the
single source of structure. (If a plain `requirements.txt` is ever needed for a reviewer, export it with
`poetry export -f requirements.txt --output requirements.txt` — don't hand-maintain one.)

Runtime deps: `pandas · numpy · lightgbm · scikit-learn · shap · networkx · pyyaml · matplotlib · joblib · faker · streamlit` (demo only).
Dev deps: `pytest · ruff · black`.
No other heavyweight deps without flagging. **No torch/tensorflow** (not needed for tabular).

Entry points (defined in `pyproject.toml`): `poetry run claimos-generate` · `poetry run claimos-pipeline` · `poetry run claimos-evaluate`.

---

## 12. API keys — definitive list

**Prototype (Phases 0–7): ZERO keys required.** Everything is open-source + synthetic + mocked.

**Optional (nice-to-have, not required):**
- **LLM key (Anthropic/OpenAI)** — only for polishing reason codes into prose or richer synthetic narratives. Templates work without it. Do NOT hardcode a key; read from env `LLM_API_KEY` if present, else fall back to templates.
- **Hugging Face token** — only if we later add *real* photo→severity CV instead of mocked severity. Read from env `HF_TOKEN`.

**Production only (Bajaj must procure — never us, never in this repo):** VAHAN/Parivahan, DigiLocker, IIB (QUEST), PRISM, core policy/claims DB, payment/settlement rails, telematics/OBD provider. These stay as `rails.py` mocks with `# PRODUCTION:` swap comments.

---

## 13. First message to paste into Claude Code

> "Read CLAUDE.md fully. It is the source of truth — follow its RULES and phase gates exactly, don't invent product concepts or statistics beyond §2, and keep everything synthetic with all external rails mocked (zero API keys). `pyproject.toml`, `config/distributions.yaml`, `config/thresholds.yaml` and `README.md` are already provided — use them as-is, don't recreate or edit them, and manage dependencies only through Poetry (`poetry add`). Start with Phase 0 (finish the scaffold on top of the provided files), run `poetry install` and `poetry run pytest`, then STOP and show me the structure and the passing gate before Phase 1. Confirm you've read the RULES section back to me in 3 bullets before you begin."

That last line forces it to prove it read the guardrails before touching code.

---

## 14. What "perfect" honestly requires (say this in the deck)

The prototype runs the **full triage logic today** on data calibrated to real IRDAI distributions. The only thing it cannot manufacture is **Bajaj's historical labeled claims** and the **seven production rails** — and going live is therefore an *integration exercise against named rails, not a research risk*. That sentence answers the CTO judge's feasibility question directly.
