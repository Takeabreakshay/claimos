"""In-process full test-run over the ClaimOS test kit.

Bypasses HTTP (no server saturation): calls the workflow + OCR engine directly,
forces the fast local OCR engine, downscales the big scans, and writes claims to
the same local SQLite the web server reads — so they also appear in the queue.
"""
from __future__ import annotations
import os, io, json, time, pathlib

# force fast local path BEFORE importing the engine
os.environ.pop("NVIDIA_OCR_KEY", None)     # dead Nemotron function id -> skip
os.environ.pop("LLM_API_KEY", None)
os.environ.pop("SUPABASE_URL", None)       # go straight to local SQLite
os.environ.pop("SUPABASE_SERVICE_KEY", None)

from PIL import Image
from src.live import workflow as wf
from src.live.ocr import run_ocr

KIT = pathlib.Path(r"C:\Users\HP\AppData\Local\Temp\claude\C--Users-HP-New-folder\9471c6f6-7090-4e30-9f52-c4b54de0bb04\scratchpad\testkit")
DOCS = {
    "rc": "ClaimOS_MOCK_01_RC.png", "license": "ClaimOS_MOCK_02_Driving_Licence.png",
    "policy_ok": "ClaimOS_MOCK_03_Policy_Comprehensive_Active.png",
    "policy_lapsed": "ClaimOS_MOCK_04_Policy_TPonly_Lapsed.png",
    "estimate_ok": "ClaimOS_MOCK_05_Repair_Estimate_Clean_18400.png",
    "bill_ok": "ClaimOS_MOCK_06_Final_Bill_Clean_18400.png",
    "estimate_mod": "ClaimOS_MOCK_07_Repair_Estimate_Moderate_42000.png",
    "bill_inflated": "ClaimOS_MOCK_08_Final_Bill_Inflated_110500.png",
    "fir": "ClaimOS_MOCK_09_FIR_Accident_Injury.png", "bank": "ClaimOS_MOCK_10_Bank_Details.png",
}
DOC_TYPE = {"rc": "rc", "license": "license", "policy_ok": "policy", "policy_lapsed": "policy",
            "estimate_ok": "repair_estimate", "bill_ok": "final_bill",
            "estimate_mod": "repair_estimate", "bill_inflated": "final_bill",
            "fir": "fir", "bank": "bank"}

def load(key: str) -> bytes:
    """Downscale big scans to <=1500px for fast, accurate local OCR."""
    im = Image.open(KIT / DOCS[key]).convert("RGB")
    w, h = im.size
    scale = min(1.0, 1500 / max(w, h))
    if scale < 1.0:
        im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO(); im.save(buf, "PNG"); return buf.getvalue()

def hr(t): print("\n" + "=" * 76 + f"\n  {t}\n" + "=" * 76, flush=True)

hr("PART 1 -- OCR EXTRACTION (local engine) on all 10 kit documents")
for key in DOCS:
    t0 = time.time()
    res = run_ocr(load(key), DOC_TYPE[key], prefer="local")
    f = res.fields or {}
    print(f"\n[{key:14}] engine={res.engine} conf={res.confidence:.2f} ({time.time()-t0:.1f}s)", flush=True)
    print(f"   {json.dumps(f, ensure_ascii=False)[:420]}", flush=True)

def run(name, fnol, docs):
    hr(name)
    cid = wf.create_claim(fnol, actor="demo")
    print(f"claim {cid} created", flush=True)
    for d in docs:
        r = wf.add_document(cid, DOCS[d], load(d), DOC_TYPE[d])
        print(f"  + {d:14} engine={r['engine']} applied={r.get('applied')}", flush=True)
    s = wf.score_and_route(cid, actor="demo")
    dec = s["decision"]
    print(f"\n  >>> LANE: {dec.outcome}", flush=True)
    if getattr(dec, "legal_check", None):
        print(f"  >>> legal_check: {dec.legal_check}", flush=True)
    for r in (dec.reasons or [])[:12]:
        print(f"      - {r}", flush=True)
    return cid

hr("PART 2 -- FOUR END-TO-END SCENARIOS")
run("A - TOUCHLESS  (minor OD, active comprehensive, clean 18.4k estimate)",
    {"claim_type": "OD", "incident_severity": "minor", "garage_type": "network",
     "product_type": "comprehensive", "policy_status": "active", "claim_amount": 18400,
     "customer_id": "CUST-SWIFT-01"},
    ["rc", "license", "policy_ok", "estimate_ok"])

run("B - INVESTIGATIVE  (estimate 42k vs final bill 110.5k -- inflation)",
    {"claim_type": "OD", "incident_severity": "moderate", "garage_type": "non_network",
     "product_type": "comprehensive", "policy_status": "active", "claim_amount": 42000,
     "customer_id": "CUST-INFL-02"},
    ["rc", "estimate_mod", "bill_inflated", "bank"])

run("C - COVERAGE DECLINE  (OD claim on a TP-only, lapsed policy)",
    {"claim_type": "OD", "incident_severity": "moderate", "product_type": "tp_only",
     "policy_status": "lapsed", "claim_amount": 35000, "customer_id": "CUST-LAPSE-03"},
    ["policy_lapsed", "rc"])

run("D - INVESTIGATIVE  (third-party accident with injury + FIR)",
    {"claim_type": "TP", "incident_severity": "severe", "third_party_involved": True,
     "injury_hint": True, "claim_amount": 180000, "customer_id": "CUST-INJURY-04"},
    ["fir", "rc", "bank"])

print("\n\nDONE.", flush=True)
