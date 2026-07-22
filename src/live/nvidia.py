"""NVIDIA NIM client — OpenAI-compatible chat + vision.

Used for:
  * Nemotron OCR v2   -> document text extraction  (NVIDIA_OCR_KEY / NVIDIA_OCR_MODEL)
  * Kimi K2           -> plain-English claim narrative, officer summary,
                         damage severity read  (NVIDIA_LLM_KEY / NVIDIA_LLM_MODEL)

Both the base URL and model ids come from .env so a model string can be swapped
without touching code. Every call degrades gracefully: on any failure the caller
falls back to the local engine, so the workflow never dead-ends.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
TIMEOUT = int(os.getenv("NVIDIA_TIMEOUT", "90"))


@dataclass
class NvResult:
    ok: bool
    text: str = ""
    error: str | None = None
    model: str = ""


# --------------------------------------------------------------------------- #
# Model resolution — NIM retires models (kimi-k2 went EOL 2026-05-12 and now
# returns HTTP 410). Hardcoding one model id guarantees a future outage, so we
# discover what the account can actually serve and fall back automatically.
# --------------------------------------------------------------------------- #
_model_cache: dict[str, list[str]] = {}
_dead: set[str] = set()

# Preference order when the configured model is unavailable. First match wins.
_PREFERRED = [
    "meta/llama-3.3-70b-instruct",
    "nvidia/llama-3.3-nemotron-super-49b-v1.5",
    "meta/llama-3.1-70b-instruct",
    "mistralai/mistral-nemo-12b-instruct",
    "qwen/qwen2.5-coder-32b-instruct",
    "meta/llama-3.1-8b-instruct",
]


def list_models(key: str) -> list[str]:
    """Model ids this key can serve (cached for the process)."""
    if not key:
        return []
    if key in _model_cache:
        return _model_cache[key]
    try:
        req = urllib.request.Request(
            f"{BASE_URL}/models", headers={"Authorization": f"Bearer {key}"}
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
        ids = sorted(m.get("id", "") for m in data.get("data", []) if m.get("id"))
    except Exception:
        ids = []
    _model_cache[key] = ids
    return ids


def resolve_model(key: str, preferred: str) -> str:
    """Pick a model that actually exists, preferring the configured one."""
    available = list_models(key)
    if preferred and preferred not in _dead and (not available or preferred in available):
        return preferred
    for cand in _PREFERRED:
        if cand in _dead:
            continue
        if not available or cand in available:
            return cand
    # last resort: anything chat-shaped the account exposes
    for m in available:
        if m not in _dead and "embed" not in m and "rerank" not in m and "ocr" not in m:
            return m
    return preferred


def _post(path: str, key: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


def _chat(key: str, model: str, content, max_tokens: int = 1800,
          temperature: float = 0.2, _tries: int = 0) -> NvResult:
    if not key:
        return NvResult(False, error="no NVIDIA key set", model=model)

    model = resolve_model(key, model)
    body = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    try:
        data = _post("/chat/completions", key, body)
        txt = data["choices"][0]["message"]["content"]
        return NvResult(True, text=txt, model=model)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()[:300]
        except Exception:
            pass
        # 410 Gone / 404 => this model is retired. Blacklist it and retry with
        # the next candidate so a model EOL can never take the feature down.
        if e.code in (404, 410) and _tries < 3:
            _dead.add(model)
            nxt = resolve_model(key, "")
            if nxt and nxt != model:
                return _chat(key, nxt, content, max_tokens, temperature, _tries + 1)
        return NvResult(False, error=f"HTTP {e.code}: {detail}", model=model)
    except Exception as e:  # network, timeout, shape
        # NIM occasionally stalls on first token. One retry costs little and
        # removes most transient failures during a live demo.
        msg = str(e)
        if _tries < 1 and ("timed out" in msg.lower() or "timeout" in msg.lower()):
            return _chat(key, model, content, max_tokens, temperature, _tries + 1)
        return NvResult(False, error=msg, model=model)


def _img_content(data: bytes, prompt: str, mime: str = "image/jpeg") -> list:
    b64 = base64.b64encode(data).decode()
    return [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
    ]


# --------------------------------------------------------------------------- #
# OCR — Nemotron OCR v2
# --------------------------------------------------------------------------- #
def ocr_document(data: bytes, doc_type: str = "other") -> NvResult:
    key = os.getenv("NVIDIA_OCR_KEY", "").strip()
    model = os.getenv("NVIDIA_OCR_MODEL", "nvidia/nemotron-ocr-v2").strip()
    prompt = (
        "Extract ALL text from this Indian motor-insurance document "
        f"(declared type: {doc_type}). Return the raw text verbatim, preserving "
        "line breaks. Do not summarise, do not add commentary."
    )
    return _chat(key, model, _img_content(data, prompt), max_tokens=2500, temperature=0.0)


# --------------------------------------------------------------------------- #
# Reasoning — Kimi K2
# --------------------------------------------------------------------------- #
def claim_narrative(payload: dict) -> NvResult:
    """Officer-facing plain-English summary of the decision (XAI polish).

    Templates already cover this without a key (explain.py); this makes it read
    like a human wrote it. Never invents numbers - it is handed the decision.
    """
    key = os.getenv("NVIDIA_LLM_KEY", "").strip()
    model = os.getenv("NVIDIA_LLM_MODEL", "moonshotai/kimi-k2-instruct").strip()
    prompt = (
        "You are a senior motor-claims officer at an Indian general insurer.\n"
        "Write a 2-3 sentence decision note for the claim file: what the system "
        "decided, the single most important reason, and the next action.\n\n"
        "STRICT RULES:\n"
        "- Use ONLY the facts given. Never invent numbers, reasons or outcomes.\n"
        "- The 'decision' field is authoritative. Do NOT contradict it. If the "
        "decision is an approval, do not describe it as a rejection (or vice versa).\n"
        "- 'coverage_status' describes policy eligibility checks. 'No rule hits' "
        "means the policy IS valid and eligible — it does NOT mean uncovered.\n"
        "- Plain English, no jargon, no bullet points.\n\n"
        "FACTS:\n" + json.dumps(payload, default=str, indent=1)
    )
    return _chat(key, model, prompt, max_tokens=400, temperature=0.3)


# A vision-capable default so damage assessment works even if NVIDIA_VLM_MODEL
# isn't set in the running process. Never fall back to the text LLM here — a
# text model returns nothing useful for an image and the feature silently dies.
DEFAULT_VLM = "meta/llama-3.2-11b-vision-instruct"


def severity_from_photo(data: bytes) -> dict:
    """Read damage severity from a photo. Returns {} on any failure."""
    key = os.getenv("NVIDIA_LLM_KEY", "").strip()
    model = (os.getenv("NVIDIA_VLM_MODEL", "").strip() or DEFAULT_VLM)
    prompt = (
        "Assess the vehicle damage in this photo for an insurance claim. "
        'Reply ONLY with JSON: {"severity":"minor|moderate|severe|total",'
        '"damaged_parts":["..."],"confidence":0.0-1.0,"note":"one short line"}'
    )
    res = _chat(key, model, _img_content(data, prompt), max_tokens=300, temperature=0.0)
    if not res.ok:
        return {}
    raw = res.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(raw)
    except Exception:
        pass
    # Model wrapped the JSON in prose — extract the first {...} block.
    try:
        s, e = raw.find("{"), raw.rfind("}")
        if s != -1 and e > s:
            out = json.loads(raw[s:e + 1])
            return out if isinstance(out, dict) else {}
    except Exception:
        pass
    return {}


def health() -> dict:
    """Quick live check of both keys — surfaced in the console sidebar."""
    out = {}
    key = os.getenv("NVIDIA_LLM_KEY", "").strip()
    llm = _chat(key, os.getenv("NVIDIA_LLM_MODEL", "").strip(),
                "Reply with exactly: OK", max_tokens=10, temperature=0.0)
    out["llm"] = {"ok": llm.ok, "model": llm.model, "error": llm.error,
                  "reply": (llm.text or "").strip()[:40],
                  "available": len(list_models(key)), "retired": sorted(_dead)}
    ocr_key = os.getenv("NVIDIA_OCR_KEY", "").strip()
    out["ocr"] = {"configured": bool(ocr_key),
                  "model": os.getenv("NVIDIA_OCR_MODEL", "nvidia/nemotron-ocr-v2")}
    return out
