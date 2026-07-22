"""LIVE photo analysis — real computation, zero API keys.

Everything here runs locally on the uploaded bytes and feeds signals the triage
engine actually consumes:

  * blur / sharpness  -> photo_quality_score -> the EVIDENCE-GAP gate (retake loop)
  * perceptual hash   -> photo_reuse_flag    -> the FRAUD model
  * EXIF time & GPS   -> incident consistency -> fraud signal

This is the honest answer to "is the CV real?" for v1: quality + reuse + EXIF are
genuinely computed. Damage severity from pixels needs a vision model (optional
LLM key, see ocr.llm_severity) — without it severity stays operator-declared.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any

import numpy as np
from PIL import Image, ExifTags

# Laplacian kernel for sharpness (variance of the response = focus measure).
_LAPLACIAN = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float32)

# Tuning (assumptions, documented): a 640px-normalised Laplacian variance below
# ~60 is visibly soft on phone photos; above ~400 is crisp.
BLUR_FLOOR = 60.0
BLUR_CEIL = 400.0


@dataclass
class PhotoAnalysis:
    quality_score: float          # 0-1 overall usability
    blur_variance: float
    is_blurry: bool
    brightness: float             # 0-1 mean luma
    width: int
    height: int
    phash: str | None
    exif_timestamp: str | None
    exif_lat: float | None
    exif_lng: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _to_gray_array(img: Image.Image, max_side: int = 640) -> np.ndarray:
    img = img.convert("L")
    w, h = img.size
    scale = max_side / max(w, h)
    if scale < 1:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    return np.asarray(img, dtype=np.float32) / 255.0


def _laplacian_variance(gray: np.ndarray) -> float:
    """Focus measure: variance of the Laplacian response (higher = sharper)."""
    if gray.shape[0] < 3 or gray.shape[1] < 3:
        return 0.0
    # 2-D convolution via stride tricks (no scipy dependency).
    m, n = gray.shape
    windows = np.lib.stride_tricks.sliding_window_view(gray, (3, 3))
    resp = np.einsum("ijkl,kl->ij", windows, _LAPLACIAN)
    return float(np.var(resp) * 10000.0)  # scale to a human-readable range


def _phash(img: Image.Image) -> str | None:
    """Perceptual hash — identical/near-identical photos hash the same.

    This is what catches a garage re-submitting the same damage photo across
    'independent' claims.
    """
    try:
        import imagehash

        return str(imagehash.phash(img))
    except Exception:
        # Fallback: 8x8 average hash, pure numpy (still reuse-detecting).
        try:
            small = np.asarray(img.convert("L").resize((8, 8)), dtype=np.float32)
            bits = (small > small.mean()).flatten()
            return "".join("1" if b else "0" for b in bits)
        except Exception:
            return None


def _exif(img: Image.Image) -> tuple[str | None, float | None, float | None]:
    """Extract capture timestamp + GPS from EXIF (consistency / fraud signal)."""
    ts = lat = lng = None
    try:
        raw = img.getexif()
        if not raw:
            return None, None, None
        tags = {ExifTags.TAGS.get(k, k): v for k, v in raw.items()}
        dt = tags.get("DateTimeOriginal") or tags.get("DateTime")
        if dt:
            try:
                ts = datetime.strptime(str(dt), "%Y:%m:%d %H:%M:%S").isoformat()
            except Exception:
                ts = str(dt)
        gps_ifd = raw.get_ifd(0x8825) if hasattr(raw, "get_ifd") else None
        if gps_ifd:
            g = {ExifTags.GPSTAGS.get(k, k): v for k, v in gps_ifd.items()}

            def _dms(vals, ref, neg):
                d = float(vals[0]); m = float(vals[1]); s = float(vals[2])
                val = d + m / 60.0 + s / 3600.0
                return -val if str(ref).upper() == neg else val

            if g.get("GPSLatitude") and g.get("GPSLatitudeRef"):
                lat = _dms(g["GPSLatitude"], g["GPSLatitudeRef"], "S")
            if g.get("GPSLongitude") and g.get("GPSLongitudeRef"):
                lng = _dms(g["GPSLongitude"], g["GPSLongitudeRef"], "W")
    except Exception:
        pass
    return ts, lat, lng


def analyse_photo(data: bytes) -> PhotoAnalysis:
    """Run the full live analysis on raw uploaded image bytes."""
    img = Image.open(io.BytesIO(data))
    img.load()
    w, h = img.size

    gray = _to_gray_array(img)
    blur_var = _laplacian_variance(gray)
    brightness = float(gray.mean())

    # sharpness 0-1 between the documented floor/ceiling
    sharp = (blur_var - BLUR_FLOOR) / (BLUR_CEIL - BLUR_FLOOR)
    sharp = float(np.clip(sharp, 0.0, 1.0))
    # exposure penalty: ideal mean luma ~0.45; punish very dark / blown out
    exposure = 1.0 - float(np.clip(abs(brightness - 0.45) / 0.45, 0.0, 1.0))
    # resolution adequacy
    res = float(np.clip(min(w, h) / 720.0, 0.0, 1.0))

    quality = float(np.clip(0.55 * sharp + 0.25 * exposure + 0.20 * res, 0.0, 1.0))
    ts, lat, lng = _exif(img)

    return PhotoAnalysis(
        quality_score=round(quality, 3),
        blur_variance=round(blur_var, 2),
        is_blurry=bool(blur_var < BLUR_FLOOR),
        brightness=round(brightness, 3),
        width=w,
        height=h,
        phash=_phash(img),
        exif_timestamp=ts,
        exif_lat=lat,
        exif_lng=lng,
    )


def hamming(a: str, b: str) -> int:
    """Distance between two perceptual hashes (hex or bit-string)."""
    if not a or not b or len(a) != len(b):
        return 999
    if set(a) <= {"0", "1"}:
        return sum(1 for x, y in zip(a, b) if x != y)
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return sum(1 for x, y in zip(a, b) if x != y)


# Graded thresholds on a 64-bit perceptual hash.
#   <=2  : the SAME image (re-submitted) -> hard fraud signal
#   3-6  : near-duplicate (crop/recompress/minor edit) -> flag for a human
#   >6   : different images
# Deliberately strict: a false "photo reuse" is a false fraud accusation, so the
# hard flag only fires on a virtually-exact match; the grey band goes to a human.
REUSE_EXACT = 2
REUSE_SIMILAR = 6


def match_photo(phash: str, known: list[str]) -> tuple[str, int, str | None]:
    """Best match against previously-seen hashes.

    Returns (verdict, distance, matched_hash) where verdict is one of
    'reused' | 'similar' | 'unique'.
    """
    best_d, best_h = 999, None
    for k in known:
        if not k:
            continue
        d = hamming(phash, k)
        if d < best_d:
            best_d, best_h = d, k
    if best_d <= REUSE_EXACT:
        return "reused", best_d, best_h
    if best_d <= REUSE_SIMILAR:
        return "similar", best_d, best_h
    return "unique", best_d, best_h


def is_reused(phash: str, known: list[str]) -> bool:
    """Hard reuse flag only (feeds photo_reuse_flag -> the fraud model)."""
    return match_photo(phash, known)[0] == "reused"
