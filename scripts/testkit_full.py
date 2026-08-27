"""Definitive end-to-end run over the test kit — documents + a sharp damage photo
+ vision-detected parts, so claims clear the evidence gate and route to real lanes.
Writes to the same local SQLite the web server reads (claims show in the queue).
"""
from __future__ import annotations
import os, io, json, time, pathlib, random

os.environ.pop("NVIDIA_OCR_KEY", None)      # dead Nemotron fn id -> local OCR
os.environ.pop("LLM_API_KEY", None)
os.environ.pop("SUPABASE_URL", None)        # local SQLite
os.environ.pop("SUPABASE_SERVICE_KEY", None)

from PIL import Image, ImageDraw
from src.live.store import LOCAL_DB
# fresh local queue so the demo shows exactly these four scenarios
try:
    if LOCAL_DB.exists():
        LOCAL_DB.unlink()
except Exception as e:
    print("clear warn:", e)
from src.live import workflow as wf

KIT = pathlib.Path(r"C:\Users\HP\AppData\Local\Temp\claude\C--Users-HP-New-folder\9471c6f6-7090-4e30-9f52-c4b54de0bb04\scratchpad\testkit")
OUT = pathlib.Path(r"C:\Users\HP\AppData\Local\Temp\claude\C--Users-HP-New-folder\9471c6f6-7090-4e30-9f52-c4b54de0bb04\scratchpad\photos")
OUT.mkdir(parents=True, exist_ok=True)
DOCS = {"rc": "ClaimOS_MOCK_01_RC.png", "license": "ClaimOS_MOCK_02_Driving_Licence.png",
        "policy_ok": "ClaimOS_MOCK_03_Policy_Comprehensive_Active.png",
        "policy_lapsed": "ClaimOS_MOCK_04_Policy_TPonly_Lapsed.png",
        "estimate_ok": "ClaimOS_MOCK_05_Repair_Estimate_Clean_18400.png",
        "estimate_mod": "ClaimOS_MOCK_07_Repair_Estimate_Moderate_42000.png",
        "bill_inflated": "ClaimOS_MOCK_08_Final_Bill_Inflated_110500.png",
        "fir": "ClaimOS_MOCK_09_FIR_Accident_Injury.png", "bank": "ClaimOS_MOCK_10_Bank_Details.png"}
DOC_TYPE = {"rc": "rc", "license": "license", "policy_ok": "policy", "policy_lapsed": "policy",
            "estimate_ok": "repair_estimate", "estimate_mod": "repair_estimate",
            "bill_inflated": "final_bill", "fir": "fir", "bank": "bank"}

def load_doc(key: str) -> bytes:
    im = Image.open(KIT / DOCS[key]).convert("RGB")
    w, h = im.size; s = min(1.0, 1500 / max(w, h))
    if s < 1.0: im = im.resize((int(w*s), int(h*s)), Image.LANCZOS)
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()

def make_photo(name: str, body, dents: int, seed: int) -> bytes:
    """Sharp synthetic damage photo. Each seed gets a DIFFERENT composition
    (background tone, car position/size/colour, ground line, window layout) so the
    low-frequency perceptual hash is distinct -> no false cross-claim reuse match."""
    rnd = random.Random(seed)
    W, H = 900, 640
    # per-seed background gradient (shifts the whole low-freq structure)
    bg_top = (rnd.randint(150, 220), rnd.randint(160, 225), rnd.randint(170, 230))
    bg_bot = (rnd.randint(90, 150), rnd.randint(95, 155), rnd.randint(100, 160))
    im = Image.new("RGB", (W, H)); d = ImageDraw.Draw(im)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(bg_top[i]*(1-t) + bg_bot[i]*t) for i in range(3)))
    ground_y = rnd.randint(int(H*0.62), int(H*0.78))
    d.rectangle([0, ground_y, W, H], fill=(rnd.randint(90, 130),)*3)
    # car geometry varies per seed
    cw = rnd.randint(560, 740); ch = rnd.randint(240, 300)
    cx0 = rnd.randint(60, W - cw - 60); cy0 = rnd.randint(int(H*0.30), int(H*0.36))
    d.rounded_rectangle([cx0, cy0, cx0+cw, cy0+ch], 40, fill=body)
    roof_h = rnd.randint(80, 120)
    d.polygon([(cx0+cw*0.18, cy0), (cx0+cw*0.32, cy0-roof_h),
               (cx0+cw*0.68, cy0-roof_h), (cx0+cw*0.82, cy0)], fill=body)
    nwin = rnd.randint(2, 4); gw = int(cw*0.11)
    for i in range(nwin):                                          # windows
        gx = int(cx0 + cw*0.24 + i*(gw+18))
        d.rounded_rectangle([gx, cy0-roof_h+22, gx+gw, cy0-8], 8, fill=(55, 66, 82))
    for wx in (int(cx0+cw*0.22), int(cx0+cw*0.78)):               # wheels
        wy = cy0+ch-10
        d.ellipse([wx-55, wy-45, wx+55, wy+65], fill=(24, 24, 27))
        d.ellipse([wx-24, wy-14, wx+24, wy+34], fill=(150, 152, 156))
    d.rectangle([cx0+20, cy0+ch*0.28, cx0+cw-20, cy0+ch*0.28+7], fill=(0, 0, 0))  # trim (sharpness)
    for _ in range(dents):                                         # damage
        cx, cy = rnd.randint(cx0+40, cx0+cw-40), rnd.randint(int(cy0+ch*0.25), int(cy0+ch*0.7))
        r = rnd.randint(30, 70)
        d.ellipse([cx-r, cy-r, cx+r, cy+int(r*0.6)], fill=(38, 38, 42))
        for _ in range(24):
            x0 = rnd.randint(cx-r, cx+r); y0 = rnd.randint(cy-r, cy+r)
            d.line([x0, y0, x0+rnd.randint(-45, 45), y0+rnd.randint(-22, 22)],
                   fill=(232, 232, 236), width=1)
    px = im.load()                                                # dense speckle -> high sharpness
    for _ in range(230000):
        x, y = rnd.randint(0, W-1), rnd.randint(0, H-1); r0, g0, b0 = px[x, y]
        j = rnd.randint(-40, 40)
        px[x, y] = (max(0, min(255, r0+j)), max(0, min(255, g0+j)), max(0, min(255, b0+j)))
    b = io.BytesIO(); im.save(b, "JPEG", quality=94)
    (OUT / name).write_bytes(b.getvalue()); return b.getvalue()

def hr(t): print("\n" + "=" * 78 + f"\n  {t}\n" + "=" * 78, flush=True)

def run(name, fnol, photo, docs):
    hr(name)
    cid = wf.create_claim(fnol, actor="demo")
    ph = wf.add_photo(cid, photo[0], photo[1], "damage_front")
    print(f"claim {cid}  |  photo quality={ph['quality_score']} blurry={ph['is_blurry']} "
          f"reuse={ph['reuse_verdict']}", flush=True)
    for dk in docs:
        r = wf.add_document(cid, DOCS[dk], load_doc(dk), DOC_TYPE[dk])
        if r.get("applied"): print(f"  + {dk:13} applied={r['applied']}", flush=True)
    s = wf.score_and_route(cid, actor="demo"); dec = s["decision"]; sc = s["score"]
    claim = s.get("claim") or {}
    amt = claim.get("claim_amount") or sc.get("claim_amount") or 0
    print(f"\n  >>> LANE: {dec.outcome.upper()}", flush=True)
    print(f"  fraud={sc.get('p_fraud'):.2f}  escalation={sc.get('p_escalation'):.2f}  "
          f"confidence={sc.get('model_confidence'):.2f}  claim=Rs{int(amt):,}", flush=True)
    for r in (dec.reasons or [])[:12]:
        print(f"      - {r}", flush=True)
    return cid

hr("DEFINITIVE RUN -- documents + damage photo + vision parts")

run("A - TOUCHLESS   minor OD | active comprehensive | clean 18.4k estimate",
    {"claim_type": "OD", "incident_severity": "minor", "garage_type": "network",
     "product_type": "comprehensive", "policy_status": "active", "claim_amount": 18400,
     "make": "Maruti Suzuki", "model": "Swift", "segment": "hatchback", "city_tier": "metro",
     "vehicle_age_years": 4, "customer_id": "CUST-SWIFT-01",
     "cv_parts": ["front_bumper", "headlamp", "front_fender"]},
    ("dmg_minor.jpg", make_photo("dmg_minor.jpg", (150, 40, 40), 1, 11), "damage_front"),
    ["rc", "license", "policy_ok", "estimate_ok"])

run("B - INVESTIGATIVE   estimate 42k vs final bill 110.5k -> inflation",
    {"claim_type": "OD", "incident_severity": "moderate", "garage_type": "non_network",
     "product_type": "comprehensive", "policy_status": "active", "claim_amount": 42000,
     "make": "Maruti Suzuki", "model": "Swift", "segment": "hatchback", "city_tier": "metro",
     "vehicle_age_years": 4, "customer_id": "CUST-INFL-02",
     "cv_parts": ["front_bumper", "bonnet", "headlamp", "front_fender"]},
    ("dmg_mod.jpg", make_photo("dmg_mod.jpg", (30, 60, 130), 3, 22), "damage_front"),
    ["rc", "estimate_mod", "bill_inflated", "bank"])

run("C - COVERAGE DECLINE   OD claim on a TP-only, lapsed policy",
    {"claim_type": "OD", "incident_severity": "moderate", "product_type": "tp_only",
     "policy_status": "lapsed", "claim_amount": 35000, "garage_type": "network",
     "make": "Maruti Suzuki", "model": "Swift", "segment": "hatchback",
     "customer_id": "CUST-LAPSE-03", "cv_parts": ["front_bumper", "grille"]},
    ("dmg_lapse.jpg", make_photo("dmg_lapse.jpg", (40, 90, 40), 2, 33), "damage_front"),
    ["policy_lapsed", "rc"])

run("D - INVESTIGATIVE   third-party accident with injury + FIR",
    {"claim_type": "TP", "incident_severity": "severe", "third_party_involved": True,
     "injury_hint": True, "fir_filed": True, "claim_amount": 180000, "garage_type": "network",
     "make": "Maruti Suzuki", "model": "Swift", "segment": "hatchback",
     "customer_id": "CUST-INJURY-04", "cv_parts": ["front_bumper", "bonnet", "headlamp", "radiator", "airbag"]},
    ("dmg_sev.jpg", make_photo("dmg_sev.jpg", (60, 60, 65), 5, 44), "damage_front"),
    ["fir", "rc", "bank"])

print("\n\nDONE.", flush=True)
