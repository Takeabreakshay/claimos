"""Exercise the brain's cognitive stack, including the abstention behaviour.

Run:  poetry run python scripts/check_brain.py
"""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

from src.live import brain, workflow as wf  # noqa: E402
from src.live.store import get_store  # noqa: E402


def show(t) -> None:
    print(f"\n=== {t.claim_id} ===")
    for lv in t.levels:
        print(f"  {lv.level:<20} -> {lv.decision}")
        for r in lv.reasons[:3]:
            print(f"       · {r}")
    sa = t.self_assessment
    print(f"  SELF: completeness={sa.get('completeness')} conf={sa.get('confidence')} "
          f"novelty={sa.get('novelty')} entitled={sa.get('entitled_to_decide')}")
    print(f"  OUTCOME: {t.outcome} — {t.outcome_reason}")


def main() -> None:
    st = get_store()
    claims = st.list_claims(limit=50)
    scored = [c for c in claims if st.latest_score(c["claim_id"])]
    print(f"claims available: {len(claims)} (scored: {len(scored)})")

    # a couple of real, already-scored claims
    for c in scored[:2]:
        show(brain.think(c["claim_id"]))

    # A deliberately ALIEN claim: values far outside anything in training.
    # The brain should notice it is unfamiliar and abstain rather than guess.
    print("\n\n########## NEVER-SEEN, OUT-OF-DISTRIBUTION CLAIM ##########")
    alien = wf.create_claim({
        "policy_id": "POL-ALIEN-1", "customer_id": "CUST-ALIEN",
        "claim_type": "TP", "incident_severity": "severe",
        "claim_amount": 4_800_000,        # ~200x a typical OD claim
        "idv": 9_500_000,                 # far above the training IDV range
        "vehicle_age_years": 24,          # outside the 0-15 training band
        "geo": "rural", "garage_type": "non_network",
        "garage_id": "GAR-ALIEN", "surveyor_id": "SUR-ALIEN",
        "bank_account": "AC-ALIEN",
        "intimation_delay_hours": 900,    # ~37 days late
        "intimation_reason_valid": False,
        "driver_valid_license": True, "fir_filed": False,
        "third_party_involved": True, "injury_hint": True,
    }, actor="brain-test")
    show(brain.think(alien))


if __name__ == "__main__":
    main()
