"""Verify NVIDIA NIM connectivity and model fallback.

Run:  poetry run python scripts/check_nvidia.py
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

from src.live import nvidia  # noqa: E402


def main() -> None:
    key = os.getenv("NVIDIA_LLM_KEY", "").strip()
    print(f"key set: {bool(key)}")

    models = nvidia.list_models(key)
    print(f"models visible to this key: {len(models)}")
    if models:
        interesting = [m for m in models if any(
            t in m.lower() for t in ("llama", "nemotron", "qwen", "mistral", "kimi", "gpt"))]
        for m in interesting[:20]:
            print("   ", m)

    configured = os.getenv("NVIDIA_LLM_MODEL", "").strip()
    resolved = nvidia.resolve_model(key, configured)
    print(f"\nconfigured : {configured}")
    print(f"resolved   : {resolved}")

    # Same shape the API now sends: plain language, no raw enums.
    cases = {
        "APPROVED (touchless)": {
            "claim_id": "CLM-TEST-1",
            "decision": "APPROVED — auto-settled straight through, no human touched it",
            "routing_triggers": ["all_lane1_conditions_met"],
            "fraud_probability": "0.8%", "model_confidence": "88%",
            "predicted_repair_cost_inr": 24800, "amount_claimed_inr": 24000,
            "coverage_status": "No rule hits — policy active and the claim is eligible",
            "legally_weak_rejection_flag": False, "note_on_flag": None,
        },
        "INVESTIGATION (fraud ring)": {
            "claim_id": "CLM-TEST-2",
            "decision": "SENT TO INVESTIGATION — surveyor and fraud investigator",
            "routing_triggers": ["fraud_prob>=0.5"],
            "fraud_probability": "100.0%", "model_confidence": "89%",
            "predicted_repair_cost_inr": 30984, "amount_claimed_inr": 61227,
            "coverage_status": "Rule hit: undeclared_modification",
            "legally_weak_rejection_flag": False, "note_on_flag": None,
        },
    }
    for label, payload in cases.items():
        print(f"\n-- {label} --")
        res = nvidia.claim_narrative(payload)
        print(f"ok    : {res.ok}  model: {res.model}")
        print(f"text  : {res.text.strip()[:420] if res.ok else str(res.error)[:300]}")
    print(f"\nretired during run: {sorted(nvidia._dead)}")


if __name__ == "__main__":
    main()
