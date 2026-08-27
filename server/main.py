"""ClaimOS web API — FastAPI over the live workflow engine.

Serves the dashboard SPA (web/) plus a REST surface for every live capability:
FNOL intake, photo vision, document OCR, model scoring, triage routing,
officer decisions, settlement and the audit trail.

Run:  poetry run claimos-web      (or: uvicorn server.main:app --port 8600)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, File, Form, HTTPException, UploadFile  # noqa: E402
from fastapi.concurrency import run_in_threadpool  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, ConfigDict  # noqa: E402

from src import constants  # noqa: E402
from src.live import nvidia, workflow as wf  # noqa: E402
from src.live.store import get_store  # noqa: E402

WEB_DIR = constants.ROOT_DIR / "web"

app = FastAPI(title="ClaimOS API", version="1.0.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


@app.on_event("startup")
def _warm() -> None:
    """Pre-load the trained models in the background so the first scoring request
    after a boot/wake is instant, not a cold model load. Runs off-thread so the
    health check (used by the host to mark the service live) responds immediately."""
    import threading

    def _load() -> None:
        try:
            wf.models()          # load + cache LightGBM cost/fraud/escalation + calibrators
        except Exception:
            pass                 # scoring will lazy-load on first use if this fails

    threading.Thread(target=_load, daemon=True).start()


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class FnolIn(BaseModel):
    # extra="allow" so any FNOL field (and the coverage/rate-card inputs below)
    # passes through to workflow.create_claim without being silently dropped.
    model_config = ConfigDict(extra="allow")

    policy_id: str | None = None
    customer_id: str | None = None
    claim_type: str = "OD"
    incident_severity: str = "minor"
    claim_amount: float = 0
    idv: float = 450000
    vehicle_age_years: float = 3
    geo: str = "urban"
    garage_type: str = "network"
    garage_id: str | None = None
    surveyor_id: str | None = None
    bank_account: str | None = None
    incident_date: str | None = None
    intimation_delay_hours: float = 0
    intimation_reason_valid: bool = True
    intimation_reason_text: str | None = None
    driver_valid_license: bool = True
    dui_flag: bool = False
    fir_filed: bool = False
    modification_actual: bool = False
    modification_declared: bool = False
    third_party_involved: bool = False
    injury_hint: bool = False
    incident_description: str | None = None
    # --- coverage matrix (LOGIC §2) ---
    policy_status: str = "active"
    product_type: str = "comprehensive"      # comprehensive | od_only | tp_only
    period_from: str | None = None
    period_to: str | None = None
    cubic_capacity: float = 0
    vehicle_type: str = "private_car"
    voluntary_excess: float = 0
    add_ons: list[str] = []
    usage_class: str = "private"
    claim_free_years: int = 0
    od_premium_next_year: float = 0
    claims_this_year: int = 0
    invoice_value: float = 0
    engine_damage: bool = False
    # --- rate card (LOGIC §1) ---
    make: str | None = None
    model: str | None = None
    segment: str | None = None
    city_tier: str = "metro"
    is_ev: bool = False
    is_import: bool = False


class DecisionIn(BaseModel):
    action: str
    actor: str = "officer.demo"
    override_reason: str | None = None
    to_lane: str | None = None


# --------------------------------------------------------------------------- #
# System
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health() -> dict[str, Any]:
    store = get_store()
    models_ok, models_err = True, None
    try:
        wf.models()
    except Exception as exc:
        models_ok, models_err = False, str(exc)
    return {
        "store": store.mode,
        "store_error": getattr(store, "init_error", None),
        "supabase_configured": bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_KEY")),
        "models_loaded": models_ok,
        "models_error": models_err,
        "ocr_engine": ("nemotron-ocr-v2"
                       if os.getenv("NVIDIA_OCR_KEY")
                       and str(os.getenv("NVIDIA_OCR_DISABLED", "")).lower() not in ("1", "true", "yes")
                       else "local:rapidocr"),
        "llm": bool(os.getenv("NVIDIA_LLM_KEY")),
        "llm_model": os.getenv("NVIDIA_LLM_MODEL", ""),
        "rails_live": os.getenv("RAILS_LIVE", "false") == "true",
        "mocked_rails": ["VAHAN", "DigiLocker", "IIB PRISM", "IIB QUEST"],
    }


@app.get("/api/nvidia/health")
def nvidia_health() -> dict[str, Any]:
    return nvidia.health()


# --------------------------------------------------------------------------- #
# Claims
# --------------------------------------------------------------------------- #
@app.get("/api/claims")
def list_claims(limit: int = 200) -> list[dict[str, Any]]:
    store = get_store()
    out = []
    for c in store.list_claims(limit=limit):
        sc = store.latest_score(c["claim_id"]) or {}
        out.append({**c, "score": sc})
    return out


@app.post("/api/claims")
def create_claim(body: FnolIn, actor: str = "officer.demo") -> dict[str, Any]:
    cid = wf.create_claim(body.model_dump(), actor=actor)
    return {"claim_id": cid, "claim": get_store().get_claim(cid)}


@app.get("/api/claims/{claim_id}")
def get_claim(claim_id: str) -> dict[str, Any]:
    store = get_store()
    claim = store.get_claim(claim_id)
    if not claim:
        raise HTTPException(404, "claim not found")
    return {
        "claim": claim,
        "score": store.latest_score(claim_id),
        "photos": store.list_child("claim_photos", claim_id),
        "documents": store.list_child("claim_documents", claim_id),
        "decisions": store.list_child("claim_decisions", claim_id),
        "timeline": wf.timeline(claim_id),
    }


@app.post("/api/claims/{claim_id}/photos")
async def upload_photo(claim_id: str, file: UploadFile = File(...),
                       angle: str = Form("wide")) -> dict[str, Any]:
    data = await file.read()
    try:
        res = await run_in_threadpool(
            wf.add_photo, claim_id, file.filename or "photo.jpg", data, angle)
    except Exception as exc:
        raise HTTPException(400, f"photo analysis failed: {exc}") from exc
    return {
        "filename": file.filename,
        "quality_score": res["quality_score"],
        "is_blurry": res["is_blurry"],
        "blur_variance": res["blur_variance"],
        "width": res["width"], "height": res["height"],
        "reuse_verdict": res["reuse_verdict"],
        "reuse_distance": res["reuse_distance"],
        "matched_claim": res["matched_claim"],
        "exif": {
            "timestamp": res["analysis"].get("exif_timestamp"),
            "lat": res["analysis"].get("exif_lat"),
            "lng": res["analysis"].get("exif_lng"),
        },
        "damage": res.get("damage") or {},
        "damage_err": res.get("damage_err"),
    }


@app.post("/api/claims/{claim_id}/documents")
async def upload_document(claim_id: str, file: UploadFile = File(...),
                          doc_type: str = Form("other")) -> dict[str, Any]:
    data = await file.read()
    try:
        res = await run_in_threadpool(
            wf.add_document, claim_id, file.filename or "doc", data, doc_type)
    except Exception as exc:
        raise HTTPException(400, f"OCR failed: {exc}") from exc
    return {
        "filename": file.filename, "doc_type": doc_type,
        "engine": res["engine"], "confidence": res.get("ocr_confidence"),
        "fields": res["ocr_fields"], "applied": res.get("applied"),
        "text": (res.get("ocr_text") or "")[:4000], "error": res.get("error"),
    }


@app.post("/api/claims/{claim_id}/score")
def score_claim(claim_id: str, actor: str = "officer.demo") -> dict[str, Any]:
    try:
        res = wf.score_and_route(claim_id, actor=actor)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"score": res["score"], "reasons": res["decision"].reasons,
            "lane": res["decision"].outcome, "legal_check": res["decision"].legal_check,
            "claim": res["claim"]}


@app.get("/api/claims/{claim_id}/brain")
def brain_trace(claim_id: str) -> dict[str, Any]:
    """Full cognitive trace: how the brain reasoned, and whether it judged itself
    entitled to decide at all (BRAIN_DECISION_ENGINE.md L0-L4)."""
    from src.live import brain

    try:
        return brain.think(claim_id).to_dict()
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, f"brain failed: {exc}") from exc


@app.post("/api/claims/{claim_id}/decision")
def decide(claim_id: str, body: DecisionIn) -> dict[str, Any]:
    return wf.record_decision(claim_id, body.action, body.actor,
                              override_reason=body.override_reason,
                              to_lane=body.to_lane)


@app.post("/api/claims/{claim_id}/settle")
def settle(claim_id: str, actor: str = "officer.demo") -> dict[str, Any]:
    return wf.settle(claim_id, actor)


_LANE_PLAIN = {
    "lane1_touchless": "APPROVED — auto-settled straight through, no human touched it",
    "lane2_assisted": "SENT TO A CLAIMS OFFICER for approval (AI-prepared file)",
    "lane3_investigative": "SENT TO INVESTIGATION — surveyor and fraud investigator",
    "retake": "PAUSED — additional evidence requested from the customer",
    "coverage_reject": "DECLINED on a policy-eligibility rule (human reviewed)",
}


@app.post("/api/claims/{claim_id}/narrative")
def narrative(claim_id: str) -> dict[str, Any]:
    """Officer note. The payload is written in plain language on purpose — feeding
    raw internal enums (e.g. coverage_reason='none') caused the model to invert
    the decision and describe an approved claim as uncovered."""
    store = get_store()
    claim = store.get_claim(claim_id) or {}
    sc = store.latest_score(claim_id) or {}

    lane = sc.get("lane")
    reason = sc.get("coverage_reason")
    coverage_status = ("No rule hits — policy active and the claim is eligible"
                       if reason in (None, "", "none")
                       else f"Rule hit: {reason}")

    res = nvidia.claim_narrative({
        "claim_id": claim_id,
        "decision": _LANE_PLAIN.get(lane, lane or "not yet scored"),
        "routing_triggers": sc.get("lane_reasons") or [],
        "fraud_probability": f"{float(sc.get('p_fraud') or 0):.1%}",
        "model_confidence": f"{float(sc.get('model_confidence') or 0):.0%}",
        "predicted_repair_cost_inr": sc.get("cost_p50"),
        "amount_claimed_inr": claim.get("claim_amount"),
        "coverage_status": coverage_status,
        "legally_weak_rejection_flag": bool(sc.get("legal_weak_reject_flag")),
        "note_on_flag": ("Late intimation with a valid reason — Supreme Court rulings "
                         "mean this alone is NOT lawful grounds to reject, so it goes "
                         "to a human.") if sc.get("legal_weak_reject_flag") else None,
    })
    return {"ok": res.ok, "text": res.text, "error": res.error, "model": res.model}


# --------------------------------------------------------------------------- #
# Policy lookup (customer app) — mocked core-policy-DB rail
# --------------------------------------------------------------------------- #
@app.get("/api/policies/{policy_id}")
def policy_lookup(policy_id: str) -> dict[str, Any]:
    from src.live import policy

    return policy.lookup_policy(policy_id)


# --------------------------------------------------------------------------- #
# Dashboard aggregate
# --------------------------------------------------------------------------- #
@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    store = get_store()
    claims = store.list_claims(limit=5000)
    rows = [(c, store.latest_score(c["claim_id"]) or {}) for c in claims]
    n = len(rows)
    lane_mix: dict[str, int] = {}
    for c, _ in rows:
        if c.get("lane"):
            lane_mix[c["lane"]] = lane_mix.get(c["lane"], 0) + 1

    touchless = lane_mix.get("lane1_touchless", 0)
    fraud_flagged = [c["claim_id"] for c, s in rows if (s.get("p_fraud") or 0) >= 0.5]
    leaked = [c["claim_id"] for c, s in rows
              if c.get("lane") == "lane1_touchless" and (s.get("p_fraud") or 0) >= 0.5]
    settled = [c for c, _ in rows if c.get("status") == "paid"]
    exposure = sum(float(c.get("claim_amount") or 0) for c, _ in rows)

    return {
        "n_claims": n,
        "touchless_share": (touchless / n) if n else 0,
        "lane_mix": lane_mix,
        "fraud_flagged": len(fraud_flagged),
        "leaked_claims": leaked,
        "leakage_rate": (len(leaked) / touchless) if touchless else 0,
        "leakage_ceiling": constants.load_thresholds()["guardrails"]["lane1_leakage_ceiling"],
        "settled": len(settled),
        "total_exposure": exposure,
        "recent": [
            {"claim_id": c["claim_id"], "lane": c.get("lane"),
             "status": c.get("status"), "claim_amount": c.get("claim_amount"),
             "claim_type": c.get("claim_type"),
             "p_fraud": s.get("p_fraud"), "confidence": s.get("model_confidence"),
             "created_at": c.get("created_at")}
            for c, s in rows[:12]
        ],
    }


# --------------------------------------------------------------------------- #
# Static SPA
# --------------------------------------------------------------------------- #
if WEB_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(WEB_DIR)), name="assets")

    # Source files must never be served stale — a cached stylesheet after an edit
    # looks exactly like a code bug (and will bite during a live demo). Fonts and
    # vendored libraries are immutable, so those we do let the browser keep.
    _NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate",
                 "Pragma": "no-cache", "Expires": "0"}
    _LONG_CACHE = {"Cache-Control": "public, max-age=31536000, immutable"}

    def _serve(p: Path) -> FileResponse:
        suffix = p.suffix.lower()
        if suffix in {".woff2", ".woff", ".otf", ".ttf"} or "/js/" in p.as_posix():
            return FileResponse(str(p), headers=_LONG_CACHE)
        if suffix in {".css", ".js", ".html"}:
            return FileResponse(str(p), headers=_NO_CACHE)
        return FileResponse(str(p))

    @app.get("/")
    def index() -> FileResponse:
        return _serve(WEB_DIR / "index.html")

    @app.get("/claim")
    def customer_app() -> FileResponse:
        """The customer-facing self-service claim journey (deliverable #7)."""
        return _serve(WEB_DIR / "claim.html")

    @app.get("/{path:path}")
    def spa(path: str):
        target = WEB_DIR / path
        if target.is_file():
            return _serve(target)
        return _serve(WEB_DIR / "index.html")


def main() -> None:
    import uvicorn

    # 0.0.0.0 so cloud hosts (Render/Railway) can route external traffic to it;
    # on localhost this still serves at http://127.0.0.1:<port>.
    uvicorn.run("server.main:app", host=os.getenv("HOST", "0.0.0.0"),
                port=int(os.getenv("PORT", "8600")), reload=False)


if __name__ == "__main__":
    main()
