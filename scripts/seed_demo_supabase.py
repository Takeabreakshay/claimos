"""Add the 4 clean demo scenarios to the PRODUCTION Supabase book.

Loads the real Supabase credentials (does NOT force local), forces fast local
OCR, verifies the store is actually in 'supabase' mode, then creates the four
showcase claims (Touchless / Assisted / Investigative / Coverage-reject).
"""
from __future__ import annotations
import os, io, sys, random
from dotenv import load_dotenv

load_dotenv()
os.environ["NVIDIA_OCR_DISABLED"] = "1"          # skip the dead Nemotron function
os.environ.pop("LLM_API_KEY", None)

from PIL import Image, ImageDraw
from src.live.store import get_store
from src.live import workflow as wf

st = get_store()
print(f"store mode = {st.mode}", flush=True)
if st.mode != "supabase":
    sys.exit(f"ABORT: store is '{st.mode}', not 'supabase' — Supabase unreachable, not writing.")

KIT = r"C:\Users\HP\AppData\Local\Temp\claude\C--Users-HP-New-folder\9471c6f6-7090-4e30-9f52-c4b54de0bb04\scratchpad\testkit"
import pathlib
KIT = pathlib.Path(KIT)
DOCS = {"rc": "ClaimOS_MOCK_01_RC.png", "license": "ClaimOS_MOCK_02_Driving_Licence.png",
        "policy_ok": "ClaimOS_MOCK_03_Policy_Comprehensive_Active.png",
        "policy_lapsed": "ClaimOS_MOCK_04_Policy_TPonly_Lapsed.png",
        "estimate_ok": "ClaimOS_MOCK_05_Repair_Estimate_Clean_18400.png",
        "estimate_mod": "ClaimOS_MOCK_07_Repair_Estimate_Moderate_42000.png",
        "bill_inflated": "ClaimOS_MOCK_08_Final_Bill_Inflated_110500.png",
        "fir": "ClaimOS_MOCK_09_FIR_Accident_Injury.png", "bank": "ClaimOS_MOCK_10_Bank_Details.png"}
DOC_TYPE = {"rc": "rc_copy", "license": "driving_licence",
            "policy_ok": "policy_copy", "policy_lapsed": "policy_copy",
            "estimate_ok": "repair_estimate", "estimate_mod": "repair_estimate",
            "bill_inflated": "final_bill", "fir": "fir", "bank": "bank_details"}

# --- idempotency: remove any prior DEMO-* claims (incl. a partial re-run) ---
DEMO_CUSTOMERS = ["DEMO-SWIFT-01", "DEMO-INFL-02", "DEMO-LAPSE-03", "DEMO-INJURY-04"]
try:
    existing = st._sb.table("claims").select("claim_id").in_("customer_id", DEMO_CUSTOMERS).execute()
    old_ids = [r["claim_id"] for r in (existing.data or [])]
    for cid in old_ids:
        for tbl in ("claim_documents", "claim_photos", "claim_scores",
                    "claim_decisions", "claim_events", "settlements"):
            try: st._sb.table(tbl).delete().eq("claim_id", cid).execute()
            except Exception: pass
        st._sb.table("claims").delete().eq("claim_id", cid).execute()
    if old_ids:
        print(f"cleared {len(old_ids)} prior DEMO claim(s): {old_ids}", flush=True)
except Exception as e:
    print("cleanup warn:", e, flush=True)

def load_doc(key):
    im = Image.open(KIT / DOCS[key]).convert("RGB")
    w, h = im.size; s = min(1.0, 1500 / max(w, h))
    if s < 1.0: im = im.resize((int(w*s), int(h*s)), Image.LANCZOS)
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()

def make_photo(body, dents, seed):
    rnd = random.Random(seed); W, H = 900, 640
    bt = (rnd.randint(150,220), rnd.randint(160,225), rnd.randint(170,230))
    bb = (rnd.randint(90,150), rnd.randint(95,155), rnd.randint(100,160))
    im = Image.new("RGB", (W, H)); d = ImageDraw.Draw(im)
    for y in range(H):
        t = y/H; d.line([(0,y),(W,y)], fill=tuple(int(bt[i]*(1-t)+bb[i]*t) for i in range(3)))
    gy = rnd.randint(int(H*0.62), int(H*0.78)); d.rectangle([0,gy,W,H], fill=(rnd.randint(90,130),)*3)
    cw = rnd.randint(560,740); ch = rnd.randint(240,300)
    cx0 = rnd.randint(60, W-cw-60); cy0 = rnd.randint(int(H*0.30), int(H*0.36))
    d.rounded_rectangle([cx0,cy0,cx0+cw,cy0+ch], 40, fill=body)
    rh = rnd.randint(80,120)
    d.polygon([(cx0+cw*0.18,cy0),(cx0+cw*0.32,cy0-rh),(cx0+cw*0.68,cy0-rh),(cx0+cw*0.82,cy0)], fill=body)
    gw = int(cw*0.11)
    for i in range(rnd.randint(2,4)):
        gx = int(cx0+cw*0.24+i*(gw+18)); d.rounded_rectangle([gx,cy0-rh+22,gx+gw,cy0-8], 8, fill=(55,66,82))
    for wx in (int(cx0+cw*0.22), int(cx0+cw*0.78)):
        wy = cy0+ch-10; d.ellipse([wx-55,wy-45,wx+55,wy+65], fill=(24,24,27))
        d.ellipse([wx-24,wy-14,wx+24,wy+34], fill=(150,152,156))
    d.rectangle([cx0+20,cy0+ch*0.28,cx0+cw-20,cy0+ch*0.28+7], fill=(0,0,0))
    for _ in range(dents):
        cx, cy = rnd.randint(cx0+40,cx0+cw-40), rnd.randint(int(cy0+ch*0.25),int(cy0+ch*0.7))
        r = rnd.randint(30,70); d.ellipse([cx-r,cy-r,cx+r,cy+int(r*0.6)], fill=(38,38,42))
        for _ in range(24):
            x0 = rnd.randint(cx-r,cx+r); y0 = rnd.randint(cy-r,cy+r)
            d.line([x0,y0,x0+rnd.randint(-45,45),y0+rnd.randint(-22,22)], fill=(232,232,236), width=1)
    px = im.load()
    for _ in range(230000):
        x, y = rnd.randint(0,W-1), rnd.randint(0,H-1); r0,g0,b0 = px[x,y]; j = rnd.randint(-40,40)
        px[x,y] = (max(0,min(255,r0+j)), max(0,min(255,g0+j)), max(0,min(255,b0+j)))
    b = io.BytesIO(); im.save(b, "JPEG", quality=94); return b.getvalue()

def run(name, fnol, body, dents, seed, docs):
    print("\n" + "="*70 + f"\n  {name}", flush=True)
    cid = wf.create_claim(fnol, actor="demo")
    wf.add_photo(cid, "damage_front.jpg", make_photo(body, dents, seed), "damage_front")
    for dk in docs:
        wf.add_document(cid, DOCS[dk], load_doc(dk), DOC_TYPE[dk])
    s = wf.score_and_route(cid, actor="demo")
    print(f"  {cid} -> {s['decision'].outcome.upper()}", flush=True)
    return cid

run("A - TOUCHLESS  (minor OD, active comprehensive, 18.4k)",
    {"claim_type":"OD","incident_severity":"minor","garage_type":"network","product_type":"comprehensive",
     "policy_status":"active","claim_amount":18400,"make":"Maruti Suzuki","model":"Swift","segment":"hatchback",
     "city_tier":"metro","vehicle_age_years":4,"customer_id":"DEMO-SWIFT-01","incident_description":"Front bumper + headlamp, parking hit",
     "cv_parts":["front_bumper","headlamp","front_fender"]},
    (150,40,40), 1, 111, ["rc","license","policy_ok","estimate_ok"])

run("B - ASSISTED  (estimate 42k vs inflated bill 110.5k)",
    {"claim_type":"OD","incident_severity":"moderate","garage_type":"non_network","product_type":"comprehensive",
     "policy_status":"active","claim_amount":42000,"make":"Maruti Suzuki","model":"Swift","segment":"hatchback",
     "city_tier":"metro","vehicle_age_years":4,"customer_id":"DEMO-INFL-02","incident_description":"Front-end collision, non-network garage",
     "cv_parts":["front_bumper","bonnet","headlamp","front_fender"]},
    (30,60,130), 3, 222, ["rc","estimate_mod","bill_inflated","bank"])

run("C - COVERAGE DECLINE  (OD on lapsed TP-only policy)",
    {"claim_type":"OD","incident_severity":"moderate","product_type":"tp_only","policy_status":"lapsed",
     "claim_amount":35000,"garage_type":"network","make":"Maruti Suzuki","model":"Swift","segment":"hatchback",
     "customer_id":"DEMO-LAPSE-03","incident_description":"OD damage claimed on a third-party-only, lapsed policy",
     "cv_parts":["front_bumper","grille"]},
    (40,90,40), 2, 337, ["policy_lapsed","rc"])

run("D - INVESTIGATIVE  (TP accident with injury + FIR)",
    {"claim_type":"TP","incident_severity":"severe","third_party_involved":True,"injury_hint":True,"fir_filed":True,
     "claim_amount":180000,"garage_type":"network","make":"Maruti Suzuki","model":"Swift","segment":"hatchback",
     "customer_id":"DEMO-INJURY-04","incident_description":"Head-on with injury to third party; FIR filed",
     "cv_parts":["front_bumper","bonnet","headlamp","radiator","airbag"]},
    (60,60,65), 5, 449, ["fir","rc","bank"])

print("\nDONE — 4 demo claims written to Supabase.", flush=True)
