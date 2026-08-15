"""Seed ~100 diverse, realistic Indian motor claims into the LIVE store.

Runs the real workflow (create_claim -> patch evidence -> score_and_route) so the
claims land in genuine lanes with genuine model scores. Every claim is a distinct
policyholder / vehicle / incident (no duplication). Loads .env, so with Supabase
credentials set it writes to Supabase and the deployed app shows them instantly.

    python scripts/seed_live_claims.py [N]     # default N = 100
"""

from __future__ import annotations

import random
import secrets
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

from src.live import workflow as wf              # noqa: E402
from src.live.store import get_store             # noqa: E402

random.seed(42)
RUN = secrets.token_hex(2).upper()   # per-run salt so IDs never collide across runs

# --- realistic Indian pools ------------------------------------------------- #
VEHICLES = [
    # make, model, segment, cc, idv_low, idv_high
    ("Maruti Suzuki", "Swift", "hatchback", 1197, 480_000, 650_000),
    ("Maruti Suzuki", "Baleno", "hatchback", 1197, 620_000, 820_000),
    ("Maruti Suzuki", "Dzire", "sedan", 1197, 650_000, 860_000),
    ("Maruti Suzuki", "Brezza", "compact_suv", 1462, 900_000, 1_180_000),
    ("Maruti Suzuki", "Ertiga", "suv", 1462, 1_050_000, 1_320_000),
    ("Hyundai", "i20", "hatchback", 1197, 720_000, 960_000),
    ("Hyundai", "Creta", "suv", 1497, 1_150_000, 1_620_000),
    ("Hyundai", "Venue", "compact_suv", 1197, 820_000, 1_120_000),
    ("Hyundai", "Verna", "sedan", 1497, 1_050_000, 1_420_000),
    ("Honda", "City", "sedan", 1498, 1_050_000, 1_360_000),
    ("Honda", "Amaze", "sedan", 1199, 720_000, 940_000),
    ("Tata", "Nexon", "compact_suv", 1199, 860_000, 1_240_000),
    ("Tata", "Punch", "compact_suv", 1199, 620_000, 860_000),
    ("Tata", "Harrier", "suv", 1956, 1_520_000, 2_180_000),
    ("Tata", "Tiago", "hatchback", 1199, 520_000, 720_000),
    ("Kia", "Seltos", "suv", 1497, 1_220_000, 1_820_000),
    ("Kia", "Sonet", "compact_suv", 1197, 800_000, 1_120_000),
    ("Mahindra", "XUV700", "suv", 1997, 1_650_000, 2_460_000),
    ("Mahindra", "Scorpio-N", "suv", 1997, 1_560_000, 2_320_000),
    ("Mahindra", "Thar", "suv", 1997, 1_320_000, 1_820_000),
    ("Toyota", "Innova Crysta", "suv", 2393, 1_820_000, 2_640_000),
    ("Toyota", "Glanza", "hatchback", 1197, 720_000, 920_000),
    ("Renault", "Kwid", "hatchback", 999, 360_000, 520_000),
    ("Nissan", "Magnite", "compact_suv", 999, 720_000, 980_000),
    ("MG", "Hector", "suv", 1451, 1_520_000, 2_120_000),
    ("Skoda", "Slavia", "sedan", 1498, 1_120_000, 1_520_000),
    ("Volkswagen", "Virtus", "sedan", 1498, 1_220_000, 1_620_000),
    ("Bajaj", "Pulsar 150", "two_wheeler", 149, 90_000, 130_000),
    ("Honda", "Activa 6G", "two_wheeler", 109, 75_000, 105_000),
    ("Royal Enfield", "Classic 350", "two_wheeler", 349, 180_000, 240_000),
    ("TVS", "Jupiter", "two_wheeler", 109, 72_000, 98_000),
]

CITIES = [  # city, tier, geo, rto
    ("Mumbai", "metro", "metro", "MH01"), ("Mumbai", "metro", "metro", "MH02"),
    ("Delhi", "metro", "metro", "DL03"), ("Delhi", "metro", "metro", "DL08"),
    ("Bengaluru", "metro", "metro", "KA05"), ("Bengaluru", "metro", "metro", "KA41"),
    ("Chennai", "metro", "metro", "TN10"), ("Hyderabad", "metro", "metro", "TS09"),
    ("Kolkata", "metro", "metro", "WB06"), ("Pune", "tier2", "urban", "MH12"),
    ("Ahmedabad", "tier2", "urban", "GJ01"), ("Jaipur", "tier2", "urban", "RJ14"),
    ("Surat", "tier2", "urban", "GJ05"), ("Lucknow", "tier2", "urban", "UP32"),
    ("Nagpur", "tier2", "urban", "MH31"), ("Indore", "tier2", "urban", "MP09"),
    ("Coimbatore", "tier3", "urban", "TN38"), ("Nashik", "tier3", "urban", "MH15"),
    ("Vadodara", "tier3", "urban", "GJ06"), ("Mysuru", "tier3", "rural", "KA09"),
    ("Bhopal", "tier2", "urban", "MP04"), ("Chandigarh", "tier2", "urban", "CH01"),
]

FIRST = ["Rajesh", "Priya", "Amit", "Sneha", "Arjun", "Kavya", "Vikram", "Ananya",
         "Rohan", "Meera", "Suresh", "Divya", "Karan", "Pooja", "Aditya", "Neha",
         "Sanjay", "Ritu", "Manish", "Shreya", "Vishal", "Anjali", "Deepak", "Nisha",
         "Rahul", "Swati", "Nikhil", "Preeti", "Gaurav", "Isha", "Farhan", "Zoya",
         "Imran", "Ayesha", "Harpreet", "Simran", "Mohan", "Lakshmi", "Ravi", "Sunita"]
LAST = ["Sharma", "Verma", "Patel", "Reddy", "Nair", "Iyer", "Gupta", "Singh",
        "Kumar", "Rao", "Desai", "Mehta", "Joshi", "Chauhan", "Menon", "Das",
        "Khan", "Sheikh", "Gill", "Bhatt", "Kulkarni", "Naidu", "Pillai", "Bose",
        "Chowdhury", "Agarwal", "Malhotra", "Kapoor", "Shetty", "Trivedi"]

INSURERS = ["BJAZ", "ACKO", "TATA", "ICICI", "HDFC", "DIGIT", "SBIG", "RELG"]

# parts by severity — feed the line-item estimator + vision floor
PARTS = {
    "minor": [["front bumper"], ["rear bumper"], ["headlight"], ["front bumper", "headlight"],
              ["front door"], ["front fender"], ["tail lamp"], ["grille", "front bumper"],
              ["windshield"], ["orvm", "front door"]],
    "moderate": [["front bumper", "bonnet", "headlight"], ["front door", "rear door"],
                 ["bonnet", "radiator"], ["quarter panel"], ["rear bumper", "boot lid"],
                 ["front fender", "front door", "headlight"]],
    "severe": [["bonnet", "radiator", "condenser", "headlight", "airbag"],
               ["front bumper", "bonnet", "engine"], ["roof panel", "windshield"],
               ["front door", "rear door", "quarter panel", "airbag"]],
    "total": [["engine assembly", "chassis"], ["engine assembly", "gearbox", "airbag"]],
}
ADDON_SETS = [[], ["zero_depreciation"], ["zero_depreciation", "consumables"],
              ["zero_depreciation", "engine_protection", "consumables"], ["engine_protection"]]


def _now():
    return datetime.now(timezone.utc)


def _est(parts, seg, garage, tier, age):
    try:
        from src import rate_card
        return rate_card.estimate(parts, segment=seg, garage_type=garage,
                                  city_tier=tier, vehicle_age_years=age)["line_item_estimate"] or 0
    except Exception:
        return 0


def make_claim(i: int) -> tuple[dict, dict]:
    """Return (intake, evidence_patch) for a diverse, unique claim i.

    Claim amounts are aligned to the rate-card repair estimate so each claim
    lands in the lane its PROFILE intends (a matched estimate stays touchless;
    a heavily-padded estimate goes investigative) — a realistic book spread.
    """
    city, tier, geo, rto = random.choice(CITIES)
    name = f"{random.choice(FIRST)} {random.choice(LAST)}"
    ins = random.choice(INSURERS)
    policy_id = f"{ins}/MC/26{RUN}/{1000 + i}"
    incident = _now() - timedelta(days=random.randint(1, 85), hours=random.randint(0, 20))
    period_from = incident - timedelta(days=random.randint(70, 300))
    period_to = period_from + timedelta(days=365)
    ncb_years = random.randint(0, 5)
    garage = random.choice(["network", "network", "network", "non_network"])
    add_ons = random.choice(ADDON_SETS)
    conf = round(random.uniform(0.84, 0.96), 2)
    n_photos = random.randint(4, 7)
    product = "comprehensive"; policy_status = "active"; engine_damage = False
    fir = False; injury = False; claim_type = "OD"

    r = random.random()
    profile = ("touchless" if r < 0.34 else "assisted" if r < 0.68
               else "investigative" if r < 0.83 else "coverage" if r < 0.92 else "retake")

    # vehicle — touchless steers to smaller/cheaper vehicles (realistic)
    small = [v for v in VEHICLES if v[2] in ("hatchback", "sedan", "compact_suv")]
    make, model, seg, cc, idv_lo, idv_hi = random.choice(small if profile == "touchless" else VEHICLES)
    is_2w = seg == "two_wheeler"
    idv = random.randint(idv_lo, idv_hi) // 1000 * 1000
    age = random.randint(1, 8)
    reg = f"{rto}{random.choice(chr(65)+chr(66)+chr(67)+chr(68)+chr(69)+chr(70)+chr(71)+chr(72)+chr(74)+chr(75)+chr(76)+chr(77)+chr(78)+chr(80)+chr(81)+chr(82))}{random.choice('ABCDEFGHJKLMNPQR')}{random.randint(1000,9999)}"

    if profile == "touchless":
        sev = "minor" if random.random() < 0.72 else "moderate"
        parts = random.choice(PARTS["minor"])
        est = _est(parts, seg, garage, tier, age) or random.randint(14000, 34000)
        amount = int(est * random.uniform(0.95, 1.08))
        conf = round(random.uniform(0.88, 0.96), 2); garage = "network"
    elif profile == "assisted":
        sev = random.choice(["minor", "moderate", "moderate", "severe"])
        parts = random.choice(PARTS[sev])
        est = _est(parts, seg, garage, tier, age) or random.randint(40000, 120000)
        if random.random() < 0.45:
            amount = int(est * random.uniform(1.32, 1.62))       # mild inflation
        else:
            amount = int(max(est, 54000) * random.uniform(1.0, 1.15))  # over the 50k anchor
    elif profile == "investigative":
        kind = random.choice(["highvalue", "severe", "total", "inflated", "injury"])
        if kind == "highvalue":
            sev = "moderate"; parts = random.choice(PARTS["moderate"])
            amount = max(int(_est(parts, seg, garage, tier, age) * 1.1), random.randint(210000, 460000))
        elif kind == "severe":
            sev = "severe"; parts = random.choice(PARTS["severe"])
            amount = int((_est(parts, seg, garage, tier, age) or 180000) * random.uniform(1.0, 1.2))
        elif kind == "total":
            sev = "total"; claim_type = "theft_total"; fir = True
            parts = random.choice(PARTS["total"]); amount = int(idv * random.uniform(0.85, 0.97))
        elif kind == "inflated":
            sev = random.choice(["minor", "moderate"]); parts = random.choice(PARTS[sev])
            amount = int((_est(parts, seg, garage, tier, age) or 30000) * random.uniform(2.1, 3.2))
        else:
            claim_type = "TP"; sev = "moderate"; injury = True; fir = True
            parts = random.choice(PARTS["moderate"]); amount = random.randint(90000, 280000)
    elif profile == "coverage":
        sev = random.choice(["minor", "moderate"]); parts = random.choice(PARTS[sev])
        amount = int((_est(parts, seg, garage, tier, age) or 40000) * random.uniform(1.0, 1.2))
        kind = random.choice(["lapsed", "tp_only", "engine"])
        if kind == "lapsed": policy_status = "lapsed"
        elif kind == "tp_only": product = "tp_only"
        else: engine_damage = True; add_ons = []
    else:  # retake
        sev = random.choice(["minor", "moderate"]); parts = random.choice(PARTS[sev])
        amount = int((_est(parts, seg, garage, tier, age) or 30000) * random.uniform(1.0, 1.15))
        conf = round(random.uniform(0.40, 0.58), 2); n_photos = random.choice([1, 2, 3])

    amount = max(3000, int(amount)) // 100 * 100

    intake = {
        "policy_id": policy_id, "customer_id": f"CUST-{RUN}-{name.replace(chr(32), chr(95))}-{i}",
        "customer_name": name, "claim_type": claim_type, "incident_severity": sev,
        "incident_date": incident.isoformat(), "claim_amount": float(amount),
        "make": make, "model": model, "segment": seg, "cubic_capacity": float(cc),
        "vehicle_type": "two_wheeler" if is_2w else "private_car",
        "registration_no": reg, "idv": float(idv), "vehicle_age_years": float(age),
        "product_type": product, "policy_status": policy_status,
        "period_from": period_from.isoformat(), "period_to": period_to.isoformat(),
        "add_ons": add_ons, "voluntary_excess": float(random.choice([0, 0, 2500, 5000])),
        "claim_free_years": ncb_years, "od_premium_next_year": float(int(idv * 0.028)),
        "city_tier": tier, "geo": geo, "garage_type": garage,
        "fir_filed": fir, "injury_hint": injury, "engine_damage": engine_damage,
        "intimation_delay_hours": float(random.choice([2, 6, 12, 20, 30, 60])),
        "incident_description": f"{sev.title()} damage reported in {city}.",
    }
    patch = {
        "num_photos": n_photos, "photo_quality_score": round(random.uniform(0.55, 0.9), 2),
        "cv_parts_all": parts, "cv_severity": sev, "cv_confidence": conf,
        "cv_severity_mismatch": False,
    }
    return intake, patch


def clear_seed(st):
    """Delete previously seeded claims (policy ids carry the /MC/26 marker)."""
    if st.mode != "supabase":
        return 0
    rows = st._sb.table("claims").select("claim_id,policy_id").execute().data or []
    ids = [r["claim_id"] for r in rows if "/MC/26" in (r.get("policy_id") or "")]
    for cid in ids:
        try: st._sb.table("claims").delete().eq("claim_id", cid).execute()
        except Exception: pass
    return len(ids)


def main(n: int = 100) -> None:
    st = get_store()
    if "--clear" in sys.argv:
        print(f"cleared {clear_seed(st)} prior seed claims")
    print(f"store mode: {st.mode}  (writing {n} claims)\n")
    lanes: dict[str, int] = {}
    for i in range(1, n + 1):
        intake, patch = make_claim(i)
        try:
            cid = wf.create_claim(intake, actor="seed")
            st.update_claim(cid, patch)
            res = wf.score_and_route(cid, actor="seed")
            lane = res["score"]["lane"]
            lanes[lane] = lanes.get(lane, 0) + 1
            if i % 10 == 0:
                print(f"  {i:3d}/{n}  ...{cid[-6:]}  {intake['make']} {intake['model']:<14} "
                      f"Rs{int(intake['claim_amount']):>9,}  -> {lane}")
        except Exception as exc:
            print(f"  {i:3d} FAILED: {str(exc)[:120]}")
    print("\n=== lane distribution ===")
    for k, v in sorted(lanes.items(), key=lambda x: -x[1]):
        print(f"  {k:22s} {v}")
    print(f"  {'TOTAL':22s} {sum(lanes.values())}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 100)
