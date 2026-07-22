# ClaimOS — Risk-Triage Decision Layer for Motor Claims

A prototype that scores an incoming motor-insurance claim and routes it into one of three lanes —
**Touchless / Assisted / Investigative** — based on *how much automation that claim deserves*.
Built for **Bajaj Finserv ATOM Season 9**. Runs entirely on **calibrated synthetic data** with all
production rails **mocked**: no real PII, **no external API keys required**.

> **`CLAUDE.md` is the source of truth for this build.** Read it before changing anything.
> This README is just how to run it.

---

## The idea in one line
Motor-claim TAT is high because every claim runs the same heavy, manual path. ClaimOS sends each claim
down the lane its risk actually warrants — low-value, low-risk, high-confidence claims settle in minutes;
risky ones get a human.

## The three lanes
| Lane | Handler | Speed | When |
|------|---------|-------|------|
| **1 · Touchless** | straight-through | minutes | < ₹50k · low fraud · high confidence · low severity · low escalation |
| **2 · Assisted** | AI-prepared file, officer approves | hours | the default |
| **3 · Investigative** | surveyor + fraud investigator | days–weeks | high value · fraud flags · severe/total · TP injury · high escalation |

The **₹50,000 surveyor-mandate seam** (IRDAI) is the anchor of Lane 1.

---

## Project structure (kept tidy with Poetry)
```
claimos/
├── CLAUDE.md            # build brief & rules — READ FIRST
├── README.md
├── ASSUMPTIONS.md       # every non-sourced number, logged
├── pyproject.toml       # Poetry: deps, env, tooling — the single manifest
├── config/
│   ├── distributions.yaml   # synthetic-data parameters (calibrated to IRDAI)
│   └── thresholds.yaml      # routing thresholds (the wedge)
├── src/
│   ├── constants.py · data_gen.py · rails.py · features.py
│   ├── models/  cost · fraud · escalation · coverage · graph
│   ├── calibration.py · triage.py · explain.py · evaluate.py · pipeline.py
├── demo/app.py         # interactive triage simulator
├── models/ · data/synth/ · reports/ · tests/
```

---

## Quickstart (Poetry)

```bash
# 0. one-time: install Poetry if you don't have it
#    https://python-poetry.org/docs/#installation

# 1. install everything into an isolated env
poetry install

# 2. sanity check
poetry run pytest

# 3. generate the synthetic dataset (Phase 1)
poetry run claimos-generate

# 4. run the full pipeline: generate -> train -> route -> evaluate
poetry run claimos-pipeline

# 5. see the numbers
poetry run claimos-evaluate      # writes reports/eval.json + figures

# 6. interactive demo
poetry run streamlit run demo/app.py
```

Every command runs inside Poetry's environment, so the dependency set is always the one pinned in
`pyproject.toml`. Don't `pip install` into the global env — add deps with `poetry add <pkg>` so the
manifest stays the single source of structure.

---

## Build order (phase-gated — see CLAUDE.md §10)
0. Scaffold → 1. Data engine → 2. Mocked rails → 3. Features + models →
4. Triage policy → 5. Explainability → 6. Evaluation → 7. Demo.
Each phase has an acceptance test that must pass before the next begins.

## The one metric that matters
**Lane-1 leakage** (a fraud auto-cleared in Touchless) has a hard **1.5% ceiling**. The pipeline
auto-tightens Lane 1 rather than ever shipping above it. Never trade leakage for a bigger touchless share.

## Honest scope
The prototype runs the **full triage logic today** on data calibrated to real IRDAI distributions.
The two things it cannot manufacture — **Bajaj's historical labeled claims** and the **seven production
rails** (VAHAN, DigiLocker, IIB/QUEST, PRISM, core policy DB, payments, telematics) — are mocked in
`src/rails.py` with `# PRODUCTION:` swap points. Going live is an integration exercise, not a research risk.
