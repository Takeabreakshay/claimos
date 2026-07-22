"""End-to-end LIVE workflow smoke test.

Run:  poetry run python scripts/smoke_live.py

Proves, without any UI: intake -> photo vision (quality/blur/reuse) -> OCR ->
real model scoring -> triage routing -> settlement math -> audit timeline.
"""

from __future__ import annotations

import io

import numpy as np
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFilter

load_dotenv()

from src.live import workflow as wf  # noqa: E402
from src.live.store import get_store  # noqa: E402


def mk(blur: float = 0, seed: int = 1) -> bytes:
    rng = np.random.default_rng(seed)
    a = (rng.random((900, 1200, 3)) * 90 + 70).astype("uint8")
    im = Image.fromarray(a)
    d = ImageDraw.Draw(im)
    for i in range(14):
        d.rectangle([50 + i * 80, 150, 100 + i * 80, 500], outline=(15, 15, 15), width=7)
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=92)
    return b.getvalue()


def main() -> None:
    st = get_store()
    print(f"STORE MODE: {st.mode}")

    # 1) clean low-value claim -> expect Touchless
    cid = wf.create_claim({
        "policy_id": "POL-TEST-001", "customer_id": "CUST-1", "claim_type": "OD",
        "claim_amount": 24000, "idv": 450000, "incident_severity": "minor",
        "garage_type": "network", "geo": "urban", "vehicle_age_years": 3,
        "intimation_delay_hours": 5, "garage_id": "GAR-A",
        "surveyor_id": "SUR-A", "bank_account": "AC-1",
    })
    print("opened", cid)

    p = wf.add_photo(cid, "front.jpg", mk(0, 1), "front-left")
    print("photo1 q=%.2f blurry=%s reuse=%s dist=%s"
          % (p["quality_score"], p["is_blurry"], p["reuse_verdict"], p["reuse_distance"]))

    res = wf.score_and_route(cid)
    s = res["score"]
    print("LANE: %s | p_fraud=%.3f conf=%.3f cost_p50=%.0f ring=%.2f"
          % (s["lane"], s["p_fraud"], s["model_confidence"], s["cost_p50"], s["ring_risk"]))
    print("reasons:", res["decision"].reasons)

    # 2) blurry evidence -> evidence-gap retake
    cid2 = wf.create_claim({
        "policy_id": "POL-TEST-002", "customer_id": "CUST-2", "claim_type": "OD",
        "claim_amount": 18000, "idv": 400000, "incident_severity": "minor",
        "garage_type": "network", "geo": "metro", "vehicle_age_years": 2,
        "intimation_delay_hours": 3, "garage_id": "GAR-B", "surveyor_id": "SUR-B",
    })
    pb = wf.add_photo(cid2, "blur.jpg", mk(7, 5))
    print("photo2 q=%.2f blurry=%s" % (pb["quality_score"], pb["is_blurry"]))
    r2 = wf.score_and_route(cid2)
    print("LANE2:", r2["score"]["lane"], r2["decision"].reasons)

    # 3) photo reuse + shared entities -> collusion signal
    cid3 = wf.create_claim({
        "policy_id": "POL-TEST-003", "customer_id": "CUST-3", "claim_type": "OD",
        "claim_amount": 30000, "idv": 500000, "incident_severity": "minor",
        "garage_type": "non_network", "geo": "urban", "vehicle_age_years": 4,
        "intimation_delay_hours": 4, "garage_id": "GAR-A",
        "surveyor_id": "SUR-A", "bank_account": "AC-1",
    })
    p3 = wf.add_photo(cid3, "same.jpg", mk(0, 1))
    print("REUSE -> verdict=%s dist=%s matched=%s"
          % (p3["reuse_verdict"], p3["reuse_distance"], p3["matched_claim"]))
    r3 = wf.score_and_route(cid3)
    print("LANE3: %s ring_risk=%.2f comp=%d"
          % (r3["score"]["lane"], r3["score"]["ring_risk"], r3["score"]["component_size"]))

    # 4) settlement on the touchless claim (real IRDAI grids)
    sett = wf.settle(cid)
    print("SETTLE gross=%.0f dep=%.0f consum=%.0f ded=%.0f NET=%.0f total_loss=%s"
          % (sett["gross_amount"], sett["depreciation"], sett["consumables"],
             sett["deductible"], sett["net_payable"], sett["total_loss"]))

    print("TIMELINE:", [e["event"] for e in wf.timeline(cid)])
    print("claims in book:", len(st.list_claims()))


if __name__ == "__main__":
    main()
