# Deploying ClaimOS (Render)

ClaimOS is a **full-stack app** — a FastAPI server that serves the two web apps
*and* runs the live engine (models, scoring, Supabase, NVIDIA). It is **not** a
static site, so it can't run on Netlify/GitHub Pages alone. Render runs the whole
thing as one service, which is the simplest path and the one these files set up.

The repo already contains everything Render needs:
- `render.yaml` — the Blueprint (service, build/start commands, env vars)
- `requirements.txt` — the lean runtime dependency set
- `models/*.pkl` — the trained model artifacts (committed, ~8 MB, so no retrain on boot)
- `web/` — both apps + fonts + the precomputed `data/*.json` (risk dial, stream)

---

## One-time: push to GitHub

This folder isn't a git repo yet. From the project root:

```bash
git init
git add -A
git commit -m "ClaimOS — deploy-ready"
git branch -M main
git remote add origin https://github.com/<you>/claimos.git
git push -u origin main
```

`.env` is gitignored, so your keys are **not** pushed — you'll set them in Render.

---

## Deploy on Render

1. Go to **render.com** → **New +** → **Blueprint**.
2. Connect your GitHub and pick the `claimos` repo. Render reads `render.yaml`.
3. It will show one service (`claimos`) and prompt for the **secret** env vars
   (the ones marked `sync: false`). Paste these four from your local `.env`:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY`
   - `NVIDIA_LLM_KEY`
   - `NVIDIA_OCR_KEY`
   (The non-secret vars — models, bucket, `LLM_PROVIDER=nvidia` — are already in
   the blueprint.)
4. **Apply / Create**. First build takes ~5–10 min (installing pandas/lightgbm/etc.).
5. When it's live you get a URL like `https://claimos.onrender.com`:
   - **Insurer console** → `/`
   - **Customer app** → `/claim`
   - **Health check** → `/api/health` (Render uses this to confirm the service is up)

---

## Important notes

- **Free tier sleeps after ~15 min idle.** The first request after that takes
  ~30–60 s to wake the service. Before a live demo, open the URL once to warm it
  up. (Upgrade to the $7 Starter plan to keep it always-on.)
- **Migrations:** run `supabase/migration_002` + `003` in Supabase once (you've
  already done this) so the rich fields persist.
- **Memory:** the lean build fits the free tier's 512 MB. The batch-only libs
  (shap, matplotlib, streamlit) are intentionally excluded — they're not used by
  the API. If you ever need the local OCR fallback, uncomment
  `rapidocr-onnxruntime` in `requirements.txt` (adds ~200 MB; NVIDIA OCR is the
  primary path so it's off by default).
- **If a build fails on a missing module:** add it to `requirements.txt` and
  redeploy. The current set is derived from the server's actual import chain.

---

## Railway / Fly.io / any Python host

The same three things make it portable anywhere:
- start command: `uvicorn server.main:app --host 0.0.0.0 --port $PORT`
- `pip install -r requirements.txt`
- the four secrets as env vars

---

## Why not Netlify?

Netlify (and GitHub Pages, S3, etc.) serve **static files only**. They can't run
the Python engine, so every `/api/...` call — scoring, policy lookup, photo
vision, the dashboard, the customer app — would fail. The scroll shell, risk dial
and stream cascade would load (they read static JSON), but the product would be
dead. If you specifically need Netlify for the frontend, host this FastAPI service
on Render and point a small `netlify.toml` proxy at it — ask and I'll wire it.
