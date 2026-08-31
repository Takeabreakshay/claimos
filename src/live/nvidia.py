"""NVIDIA NIM client - OpenAI-compatible chat + vision.

Used for:
  * Document OCR   -> local RapidOCR by default (hosted Nemotron OCR is optional
                      via NVIDIA_OCR_KEY / NVIDIA_OCR_MODEL; 404s on some accounts)
  * LLM reasoning  -> plain-English claim narrative + officer summary
                      (NVIDIA_LLM_KEY / NVIDIA_LLM_MODEL, e.g. nemotron-3-super)
  * Vision         -> damage severity + parts from the photo
                      (NVIDIA_LLM_KEY / NVIDIA_VLM_MODEL = llama-3.2-11b-vision)

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
# OCR reads dense documents and is far slower than a chat turn (a full-res RC scan
# has spiked to ~145s), so it gets its own, longer budget. Downscaling first keeps
# most calls well under it.
OCR_TIMEOUT = int(os.getenv("NVIDIA_OCR_TIMEOUT", "150"))
OCR_MAX_DIM = int(os.getenv("NVIDIA_OCR_MAX_DIM", "1600"))


def _downscale_for_ocr(data: bytes, max_dim: int = OCR_MAX_DIM) -> bytes:
    """Shrink an oversized document image before OCR - smaller payload + faster,
    more reliable inference (1600px long side keeps document text legible). Falls
    back to the raw bytes if Pillow is missing or the image can't be decoded."""
    try:
        import io

        from PIL import Image

        im = Image.open(io.BytesIO(data)).convert("RGB")
        w, h = im.size
        scale = min(1.0, max_dim / max(w, h))
        # Already small and lightweight -> leave it as-is.
        if scale >= 1.0 and len(data) < 400_000:
            return data
        if scale < 1.0:
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        out = buf.getvalue()
        return out if out else data
    except Exception:
        return data


@dataclass
class NvResult:
    ok: bool
    text: str = ""
    error: str | None = None
    model: str = ""


# --------------------------------------------------------------------------- #
# Model resolution - NIM retires models (kimi-k2 went EOL 2026-05-12 and now
# returns HTTP 410). Hardcoding one model id guarantees a future outage, so we
# discover what the account can actually serve and fall back automatically.
# --------------------------------------------------------------------------- #
_model_cache: dict[str, list[str]] = {}
_dead: set[str] = set()

# Preference order when the configured model is unavailable. First match wins.
# Verified live on the account 2026-08-28 (a mass NIM EOL on 2026-08-26 retired
# most llama-3.1/3.3 + nemotron-super-49b ids). The vision model doubles as a
# text fallback, so the LLM feature survives even if the Nemotron ids go too.
_PREFERRED = [
    "nvidia/nemotron-3-super-120b-a12b",   # strong general reasoning (verified)
    "nvidia/nemotron-3-nano-30b-a3b",      # fast fallback (verified)
    "meta/llama-3.2-11b-vision-instruct",  # vision-capable, also handles text
    "nvidia/nemotron-4-340b-instruct",
    "openai/gpt-oss-120b",
    "mistralai/mistral-large-2-instruct",
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


def _post(path: str, key: str, body: dict, timeout: int | None = None) -> dict:
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
    with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as r:
        return json.loads(r.read().decode())


def strip_reasoning(text: str) -> str:
    """Return only the model's answer - drop any chain-of-thought.

    Reasoning models (Nemotron, etc.) can emit their working before the answer,
    either wrapped in <think>...</think> tags or as a preamble. This keeps only
    the final note so the officer never sees the model thinking out loud."""
    import re
    t = text or ""
    # Remove complete <think>/<thinking>/<reasoning> blocks.
    t = re.sub(r"<\s*(think|thinking|reasoning)\s*>.*?<\s*/\s*\1\s*>", "", t,
               flags=re.I | re.S)
    # An unclosed opener (truncated) or a stray closer: keep what follows it.
    if re.search(r"<\s*/\s*(think|thinking|reasoning)\s*>", t, re.I):
        t = re.split(r"<\s*/\s*(?:think|thinking|reasoning)\s*>", t, flags=re.I)[-1]
    t = re.sub(r"<\s*/?\s*(think|thinking|reasoning)\s*>", "", t, flags=re.I)
    # Strip markdown fences, bold markers, and label prefixes the model sometimes adds.
    t = t.strip().removeprefix("```").removesuffix("```").strip()
    t = t.replace("**", "").replace("__", "")
    t = re.sub(r"^\s*(officer[' ]?s?\s*note|note|answer|final answer|response|decision)\s*[:\-]\s*",
               "", t, flags=re.I).strip()
    # Drop inline label runs like "Decision: ... Next Action: ..." -> keep the prose.
    t = re.sub(r"\b(Next Action|Next Step|Key Flag|Decision|Rationale)\s*:\s*", "", t, flags=re.I)
    return re.sub(r"\s+\n", "\n", t).strip()


def _chat(key: str, model: str, content, max_tokens: int = 1800,
          temperature: float = 0.2, _tries: int = 0, timeout: int | None = None,
          system: str | None = None) -> NvResult:
    if not key:
        return NvResult(False, error="no NVIDIA key set", model=model)

    model = resolve_model(key, model)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    try:
        data = _post("/chat/completions", key, body, timeout=timeout)
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
                return _chat(key, nxt, content, max_tokens, temperature, _tries + 1, timeout, system)
        return NvResult(False, error=f"HTTP {e.code}: {detail}", model=model)
    except Exception as e:  # network, timeout, shape
        # NIM occasionally stalls on first token. One retry costs little and
        # removes most transient failures during a live demo.
        msg = str(e)
        if _tries < 1 and ("timed out" in msg.lower() or "timeout" in msg.lower()):
            return _chat(key, model, content, max_tokens, temperature, _tries + 1, timeout, system)
        return NvResult(False, error=msg, model=model)


def _img_content(data: bytes, prompt: str, mime: str = "image/jpeg") -> list:
    b64 = base64.b64encode(data).decode()
    return [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
    ]


# --------------------------------------------------------------------------- #
# OCR - Nemotron OCR v2
# --------------------------------------------------------------------------- #
def ocr_document(data: bytes, doc_type: str = "other", _retry: bool = True) -> NvResult:
    key = os.getenv("NVIDIA_OCR_KEY", "").strip()
    # Default to the vision model (reads document text accurately as an image);
    # the hosted nemotron-ocr-v2 function 404s on many accounts. Overridable via
    # NVIDIA_OCR_MODEL so a dedicated OCR NIM can be swapped in where provisioned.
    model = os.getenv("NVIDIA_OCR_MODEL", "meta/llama-3.2-11b-vision-instruct").strip()
    # Known-dead / non-multimodal OCR ids -> route to the vision model. A stale
    # env var (e.g. NVIDIA_OCR_MODEL=nvidia/nemotron-ocr-v2 on a host) would
    # otherwise 400/404 on an image, and there is no local OCR fallback in prod.
    if model in ("", "nvidia/nemotron-ocr-v2", "nemotron-ocr-v2"):
        model = "meta/llama-3.2-11b-vision-instruct"
    prompt = (
        "Extract ALL text from this Indian motor-insurance document "
        f"(declared type: {doc_type}). Return the raw text verbatim, preserving "
        "line breaks. Do not summarise, do not add commentary."
    )
    # Downscale a heavy scan first (smaller payload + faster inference), give OCR
    # its own longer timeout, and re-OCR once if the model returns nothing - the
    # empty result on a slow doc is usually a transient stall, not a real blank.
    img = _downscale_for_ocr(data)
    res = _chat(key, model, _img_content(img, prompt),
                max_tokens=2500, temperature=0.0, timeout=OCR_TIMEOUT)
    if _retry and (not res.ok or not (res.text or "").strip()):
        res = _chat(key, model, _img_content(img, prompt),
                    max_tokens=2500, temperature=0.0, timeout=OCR_TIMEOUT)
    return res


# --------------------------------------------------------------------------- #
# Reasoning - Kimi K2
# --------------------------------------------------------------------------- #
def claim_narrative(payload: dict) -> NvResult:
    """Officer-facing plain-English summary of the decision (XAI polish).

    Templates already cover this without a key (explain.py); this makes it read
    like a human wrote it. Never invents numbers - it is handed the decision.
    """
    key = os.getenv("NVIDIA_LLM_KEY", "").strip()
    # Use a straight instruction-follower, NOT a reasoning model. Nemotron-super /
    # nano dump their chain-of-thought as plain prose (no <think> tags) and burn
    # the token budget before writing the note; the vision-instruct model answers
    # cleanly. Overridable via NVIDIA_NOTE_MODEL.
    model = os.getenv("NVIDIA_NOTE_MODEL", "meta/llama-3.2-11b-vision-instruct").strip()
    prompt = (
        "You are a senior motor-claims officer at an Indian general insurer, "
        "writing the decision note for a claim file.\n\n"
        "Write EXACTLY 2 tight sentences (max ~45 words total):\n"
        "1. The decision and the single fact that drove it - lead with the number "
        "or flag that matters most.\n"
        "2. The one next action the handling officer should take.\n\n"
        "STRICT RULES:\n"
        "- Output ONLY the two sentences. No preamble, no reasoning, no headings, "
        "no labels (no 'Decision:', 'Next action:'), no bold, no asterisks, no "
        "markdown, no bullet points - just plain flowing prose.\n"
        "- Use ONLY the facts given; never invent numbers, reasons or outcomes.\n"
        "- The 'decision' field is authoritative - never contradict it (an approval "
        "is not a rejection).\n"
        "- 'coverage_status' is an eligibility check: 'No rule hits' means the policy "
        "IS valid and eligible - it does NOT mean uncovered.\n"
        "- Plain, confident English an underwriter would write.\n\n"
        "FACTS:\n" + json.dumps(payload, default=str, indent=1)
    )
    # 'detailed thinking off' suppresses any Nemotron reasoning at the source (for a
    # swapped-in model); strip_reasoning() cleans whatever slips through, so the
    # officer only ever sees the note itself.
    res = _chat(key, model, prompt, max_tokens=300, temperature=0.3,
                system="detailed thinking off")
    if res.ok:
        res.text = strip_reasoning(res.text)
    return res


# A vision-capable default so damage assessment works even if NVIDIA_VLM_MODEL
# isn't set in the running process. Never fall back to the text LLM here - a
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
    # Model wrapped the JSON in prose - extract the first {...} block.
    try:
        s, e = raw.find("{"), raw.rfind("}")
        if s != -1 and e > s:
            out = json.loads(raw[s:e + 1])
            return out if isinstance(out, dict) else {}
    except Exception:
        pass
    return {}


def health() -> dict:
    """Quick live check of both keys - surfaced in the console sidebar."""
    out = {}
    key = os.getenv("NVIDIA_LLM_KEY", "").strip()
    llm = _chat(key, os.getenv("NVIDIA_LLM_MODEL", "").strip(),
                "Reply with exactly: OK", max_tokens=10, temperature=0.0)
    out["llm"] = {"ok": llm.ok, "model": llm.model, "error": llm.error,
                  "reply": (llm.text or "").strip()[:40],
                  "available": len(list_models(key)), "retired": sorted(_dead)}
    ocr_key = os.getenv("NVIDIA_OCR_KEY", "").strip()
    out["ocr"] = {"configured": bool(ocr_key),
                  "model": os.getenv("NVIDIA_OCR_MODEL", "meta/llama-3.2-11b-vision-instruct")}
    return out
