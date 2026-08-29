"""End-to-end QA of the live API: exercises every engine + records timing.
create -> photo(evidence) -> documents(OCR+tamper) -> score(decision pipeline)
-> brain -> detail -> decision -> settle -> dashboard. Prints PASS/FAIL + ms."""
from __future__ import annotations
import io, time, json, pathlib, requests
from PIL import Image, ImageDraw
import random

API = "http://127.0.0.1:8600"
KIT = pathlib.Path(r"C:\Users\HP\AppData\Local\Temp\claude\C--Users-HP-New-folder\9471c6f6-7090-4e30-9f52-c4b54de0bb04\scratchpad\testkit")
issues = []

def t(label, fn):
    t0 = time.time()
    try:
        r = fn(); ms = (time.time()-t0)*1000
        slow = " [SLOW]" if ms > 12000 else ""
        print(f"  {label:38} {ms:7.0f}ms{slow}")
        if ms > 12000: issues.append(f"SLOW: {label} took {ms:.0f}ms")
        return r
    except Exception as e:
        print(f"  {label:38} FAILED: {e}"); issues.append(f"FAIL: {label}: {e}"); return None

def sharp_photo(seed):
    rnd=random.Random(seed); W,H=900,640
    im=Image.new("RGB",(W,H),(200,205,212)); d=ImageDraw.Draw(im)
    d.rounded_rectangle([80,200,820,500],40,fill=(150,40,40))
    for _ in range(200000):
        x,y=rnd.randint(0,W-1),rnd.randint(0,H-1); r,g,b=im.getpixel((x,y)); j=rnd.randint(-40,40)
        im.putpixel((x,y),(max(0,min(255,r+j)),max(0,min(255,g+j)),max(0,min(255,b+j))))
    b=io.BytesIO(); im.save(b,"JPEG",quality=92); return b.getvalue()

def doc(name):
    im=Image.open(KIT/name).convert("RGB"); s=min(1.0,1500/max(im.size)); im=im.resize((int(im.width*s),int(im.height*s)))
    b=io.BytesIO(); im.save(b,"PNG"); return b.getvalue()

print("=== E2E pipeline ===")
cid=None
def create():
    global cid
    r=requests.post(f"{API}/api/claims",json={"claim_type":"OD","incident_severity":"minor","garage_type":"network",
        "product_type":"comprehensive","policy_status":"active","claim_amount":18400,"make":"Maruti Suzuki",
        "model":"Swift","segment":"hatchback","customer_id":"QA-E2E-01","cv_parts":["front_bumper","headlamp"]},timeout=30)
    r.raise_for_status(); cid=r.json().get("claim_id") or r.json().get("id"); return cid
t("create claim (intake)", create)
print(f"  -> {cid}")

def photo():
    files={"file":("dmg.jpg",sharp_photo(1),"image/jpeg")}
    r=requests.post(f"{API}/api/claims/{cid}/photos",files=files,data={"angle":"front-left"},timeout=60); r.raise_for_status(); return r.json()
ph=t("add photo (evidence: blur/reuse)", photo)
if ph: print(f"     quality={ph.get('quality_score')} blurry={ph.get('is_blurry')} reuse={ph.get('reuse_verdict')}")

def docu(name,dt):
    files={"file":(name,doc(name),"image/png")}
    r=requests.post(f"{API}/api/claims/{cid}/documents",files=files,data={"doc_type":dt},timeout=90); r.raise_for_status(); return r.json()
d1=t("OCR document: RC (rc_copy)", lambda: docu("ClaimOS_MOCK_01_RC.png","rc_copy"))
if d1: print(f"     engine={d1.get('engine')} fields={list((d1.get('fields') or {}))[:5]}")
d2=t("OCR document: estimate", lambda: docu("ClaimOS_MOCK_05_Repair_Estimate_Clean_18400.png","repair_estimate"))
if d2: print(f"     applied={d2.get('applied')}")

def score():
    r=requests.post(f"{API}/api/claims/{cid}/score",timeout=60); r.raise_for_status(); return r.json()
sc=t("score & route (decision pipeline)", score)
if sc: print(f"     LANE={sc.get('lane')} reasons={sc.get('reasons')}")

br=t("brain trace (metacognition)", lambda: requests.get(f"{API}/api/claims/{cid}/brain",timeout=30).json())
if br: print(f"     entitled={br.get('entitled')} outcome={br.get('outcome')}")

det=t("claim detail (score/photos/docs/timeline)", lambda: requests.get(f"{API}/api/claims/{cid}",timeout=30).json())
if det: print(f"     photos={len(det.get('photos',[]))} docs={len(det.get('documents',[]))} timeline={len(det.get('timeline',[]))}")

dec=t("decision: approve", lambda: requests.post(f"{API}/api/claims/{cid}/decision",json={"action":"approve","actor":"officer.demo"},timeout=30).json())
st=t("settle (waterfall)", lambda: requests.post(f"{API}/api/claims/{cid}/settle?actor=manager.demo",timeout=30).json())
if st: print(f"     net_payable={st.get('net_payable')} utr={st.get('utr_reference')}")

dash=t("dashboard aggregate", lambda: requests.get(f"{API}/api/dashboard",timeout=30).json())
if dash: print(f"     n_claims={dash.get('n_claims')} lanes={dash.get('lane_mix')}")

print("\n=== ISSUES ===")
print("\n".join(issues) if issues else "none")
