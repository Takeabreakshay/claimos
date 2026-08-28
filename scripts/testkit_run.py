"""Full test-run over the ClaimOS_Test_Kit_Documents against the live local API.

Runs each mock document through the OCR engine, then drives four end-to-end
claim scenarios (touchless / inflation-investigative / coverage-decline /
injury-investigative) and prints lane + reasons for each.
"""
from __future__ import annotations
import json, sys, time, pathlib, requests

API = "http://127.0.0.1:8600"
KIT = pathlib.Path(r"C:\Users\HP\AppData\Local\Temp\claude\C--Users-HP-New-folder\9471c6f6-7090-4e30-9f52-c4b54de0bb04\scratchpad\testkit")

DOCS = {
    "rc":            "ClaimOS_MOCK_01_RC.png",
    "license":       "ClaimOS_MOCK_02_Driving_Licence.png",
    "policy_ok":     "ClaimOS_MOCK_03_Policy_Comprehensive_Active.png",
    "policy_lapsed": "ClaimOS_MOCK_04_Policy_TPonly_Lapsed.png",
    "estimate_ok":   "ClaimOS_MOCK_05_Repair_Estimate_Clean_18400.png",
    "bill_ok":       "ClaimOS_MOCK_06_Final_Bill_Clean_18400.png",
    "estimate_mod":  "ClaimOS_MOCK_07_Repair_Estimate_Moderate_42000.png",
    "bill_inflated": "ClaimOS_MOCK_08_Final_Bill_Inflated_110500.png",
    "fir":           "ClaimOS_MOCK_09_FIR_Accident_Injury.png",
    "bank":          "ClaimOS_MOCK_10_Bank_Details.png",
}
DOC_TYPE = {
    "rc": "rc", "license": "license", "policy_ok": "policy", "policy_lapsed": "policy",
    "estimate_ok": "repair_estimate", "bill_ok": "final_bill",
    "estimate_mod": "repair_estimate", "bill_inflated": "final_bill",
    "fir": "fir", "bank": "bank",
}

def create(fnol: dict) -> str:
    r = requests.post(f"{API}/api/claims", json=fnol, timeout=30)
    r.raise_for_status()
    return r.json()["id"] if "id" in r.json() else r.json().get("claim_id") or r.json()

def upload_doc(cid: str, key: str) -> dict:
    path = KIT / DOCS[key]
    with open(path, "rb") as fh:
        files = {"file": (DOCS[key], fh, "image/png")}
        data = {"doc_type": DOC_TYPE[key]}
        r = requests.post(f"{API}/api/claims/{cid}/documents", files=files, data=data, timeout=90)
    r.raise_for_status()
    return r.json()

def score(cid: str) -> dict:
    r = requests.post(f"{API}/api/claims/{cid}/score", timeout=60)
    r.raise_for_status()
    return r.json()

def hr(t): print("\n" + "="*74 + f"\n  {t}\n" + "="*74)

# ---- create a throwaway claim to attach OCR docs to ----
hr("PART 1 — OCR EXTRACTION on all 10 kit documents")
probe = create({"claim_type": "OD", "incident_severity": "minor", "claim_amount": 1})
print(f"(probe claim {probe})")
for key in DOCS:
    t0 = time.time()
    try:
        res = upload_doc(probe, key)
        f = res.get("fields") or {}
        conf = res.get("confidence")
        shown = {k: f[k] for k in list(f)[:6]}
        print(f"\n[{key:14}] engine={res.get('engine')} conf={conf} ({time.time()-t0:.1f}s)")
        print(f"   fields: {json.dumps(shown, ensure_ascii=False)[:300]}")
        if res.get("applied"):
            print(f"   applied-> {json.dumps(res['applied'], ensure_ascii=False)[:200]}")
        if res.get("error"):
            print(f"   ERROR: {res['error']}")
    except Exception as e:
        print(f"\n[{key:14}] FAILED: {e}")

# ---------------- scenario runner ----------------
def run(name, fnol, docs):
    hr(name)
    cid = create(fnol)
    print(f"claim {cid}  (created)")
    for d in docs:
        try:
            res = upload_doc(cid, d)
            print(f"  + {d:14} conf={res.get('confidence')} applied={res.get('applied')}")
        except Exception as e:
            print(f"  + {d:14} FAILED: {e}")
    s = score(cid)
    print(f"\n  >>> LANE: {s.get('lane')}")
    if s.get("legal_check"): print(f"  >>> legal_check: {s['legal_check']}")
    for r in (s.get("reasons") or [])[:10]:
        print(f"      - {r}")
    return cid, s

hr("PART 2 — FOUR END-TO-END SCENARIOS")

run("A · TOUCHLESS  (minor OD, active comprehensive, clean 18.4k estimate)",
    {"claim_type": "OD", "incident_severity": "minor", "garage_type": "network",
     "product_type": "comprehensive", "policy_status": "active", "claim_amount": 18400},
    ["rc", "license", "policy_ok", "estimate_ok"])

run("B · INVESTIGATIVE  (estimate 42k vs final bill 110.5k — inflation)",
    {"claim_type": "OD", "incident_severity": "moderate", "garage_type": "non_network",
     "product_type": "comprehensive", "policy_status": "active", "claim_amount": 42000},
    ["rc", "estimate_mod", "bill_inflated", "bank"])

run("C · COVERAGE DECLINE  (OD claim on a TP-only, lapsed policy)",
    {"claim_type": "OD", "incident_severity": "moderate",
     "product_type": "tp_only", "policy_status": "lapsed", "claim_amount": 35000},
    ["policy_lapsed", "rc"])

run("D · INVESTIGATIVE  (third-party accident with injury + FIR)",
    {"claim_type": "TP", "incident_severity": "severe", "third_party_involved": True,
     "injury_hint": True, "fir_filed": True, "claim_amount": 180000},
    ["fir", "rc", "bank"])

print("\n\nDONE.")
