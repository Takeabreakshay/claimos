"""Migrate claims from the local SQLite store into Supabase.

Reads the local store directly (not through Store, which now resolves to
Supabase) and replays every row into Postgres in FK-safe order, uploading any
evidence files that still exist on disk.

Idempotent: claims already present in Supabase are skipped.

Run:  poetry run python scripts/migrate_local_to_supabase.py
      poetry run python scripts/migrate_local_to_supabase.py --dry-run
"""

from __future__ import annotations

import json
import mimetypes
import sqlite3
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from src import constants  # noqa: E402
from src.live.store import LOCAL_DB, Store  # noqa: E402

DRY = "--dry-run" in sys.argv

# Columns Supabase actually accepts. Anything else in the local JSON blob is
# dropped. `modification_undeclared` is a GENERATED column — never insert it.
COLS: dict[str, set[str]] = {
    "policies": {
        "policy_id", "customer_id", "owner_name", "owner_phone", "owner_geo",
        "vehicle_registration_no", "vehicle_make", "vehicle_model", "manufacture_year",
        "fuel_type", "idv", "product_type", "policy_status", "policy_start_date",
        "policy_end_date", "deductible", "ncb_percent", "add_ons", "exclusions", "created_at",
    },
    "claims": {
        "claim_id", "policy_id", "customer_id", "claim_type", "incident_date",
        "fnol_timestamp", "intimation_delay_hours", "intimation_gt_48h",
        "intimation_reason_valid", "intimation_reason_text", "incident_description",
        "incident_lat", "incident_lng", "geo", "incident_severity", "claim_amount",
        "idv", "vehicle_age_years", "garage_id", "garage_type", "surveyor_id",
        "bank_account", "driver_valid_license", "dui_flag", "modification_actual",
        "modification_declared", "fir_required", "fir_filed", "fir_number",
        "third_party_involved", "injury_hint", "num_photos", "photo_quality_score",
        "photo_reuse_flag", "status", "lane", "assigned_to", "created_at", "updated_at",
    },
    "claim_photos": {
        "claim_id", "storage_path", "angle_label", "phash", "quality_score",
        "blur_variance", "is_blurry", "exif_timestamp", "exif_lat", "exif_lng",
        "width", "height", "created_at",
    },
    "claim_documents": {
        "claim_id", "doc_type", "storage_path", "ocr_text", "ocr_fields",
        "ocr_confidence", "verified", "verified_by", "verification_note", "created_at",
    },
    "claim_scores": {
        "claim_id", "p_fraud", "p_escalation", "model_confidence", "c_fraud",
        "c_escalation", "c_cost", "cost_p10", "cost_p50", "cost_p90", "ring_risk",
        "component_size", "coverage_clear", "coverage_reason",
        "legal_weak_reject_flag", "lane", "lane_reasons", "fraud_drivers",
        "cost_drivers", "escalation_drivers", "plain_reason", "model_version", "scored_at",
    },
    "claim_decisions": {
        "claim_id", "actor", "action", "from_lane", "to_lane", "override_reason",
        "settlement_amount", "created_at",
    },
    "claim_events": {"claim_id", "event", "detail", "actor", "created_at"},
    "settlements": {
        "claim_id", "gross_amount", "depreciation", "consumables", "deductible",
        "net_payable", "total_loss", "utr_reference", "paid_at", "created_at",
    },
}

CHILD_TABLES = ["claim_photos", "claim_documents", "claim_scores",
                "claim_decisions", "claim_events", "settlements"]


def clean(table: str, row: dict[str, Any]) -> dict[str, Any]:
    allowed = COLS[table]
    return {k: v for k, v in row.items() if k in allowed and v is not None}


def read_local() -> tuple[list[dict], dict[str, list[dict]]]:
    if not LOCAL_DB.exists():
        print(f"no local store at {LOCAL_DB} — nothing to migrate")
        return [], {}
    con = sqlite3.connect(LOCAL_DB)
    con.row_factory = sqlite3.Row
    existing = {r[0] for r in con.execute(
        "select name from sqlite_master where type='table'").fetchall()}

    claims = [json.loads(r[0]) for r in con.execute("select data from claims").fetchall()] \
        if "claims" in existing else []
    children: dict[str, list[dict]] = {}
    for t in CHILD_TABLES:
        if t in existing:
            children[t] = [json.loads(r[0]) for r in
                           con.execute(f"select data from {t}").fetchall()]
        else:
            children[t] = []
    con.close()
    return claims, children


def main() -> None:
    store = Store()
    if store.mode != "supabase":
        raise SystemExit(f"target is not Supabase (mode={store.mode}). "
                         f"{store.init_error or 'check .env'}")
    sb = store._sb

    claims, children = read_local()
    if not claims:
        return
    print(f"local store: {len(claims)} claim(s), "
          + ", ".join(f"{t}={len(v)}" for t, v in children.items()))

    already = {r["claim_id"] for r in
               (sb.table("claims").select("claim_id").execute().data or [])}
    todo = [c for c in claims if c.get("claim_id") not in already]
    print(f"already in Supabase: {len(already)} · to migrate: {len(todo)}")
    if not todo:
        print("nothing to do")
        return
    if DRY:
        for c in todo:
            print(f"  would migrate {c['claim_id']} "
                  f"({c.get('claim_type')} {c.get('claim_amount')} lane={c.get('lane')})")
        return

    ids = {c["claim_id"] for c in todo}

    # 1) policy master first (claims.policy_id has an FK to it)
    pols = {}
    for c in todo:
        pid = c.get("policy_id")
        if pid and pid not in pols:
            pols[pid] = clean("policies", {
                "policy_id": pid,
                "customer_id": c.get("customer_id") or "MIGRATED",
                "owner_geo": c.get("geo"),
                "idv": c.get("idv") or 0,
                "policy_status": c.get("policy_status", "active"),
            })
    if pols:
        sb.table("policies").upsert(list(pols.values()), on_conflict="policy_id").execute()
        print(f"  policies upserted: {len(pols)}")

    # 2) claims
    rows = [clean("claims", c) for c in todo]
    sb.table("claims").insert(rows).execute()
    print(f"  claims inserted: {len(rows)}")

    # 3) evidence files -> Supabase Storage, then child rows
    uploaded = failed = 0
    for table in CHILD_TABLES:
        batch = []
        for r in children.get(table, []):
            if r.get("claim_id") not in ids:
                continue
            row = dict(r)
            if table in ("claim_photos", "claim_documents"):
                p = Path(str(row.get("storage_path", "")))
                if p.exists():
                    try:
                        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
                        key = f"{row['claim_id']}/{p.name}"
                        sb.storage.from_(store.bucket if hasattr(store, "bucket")
                                         else "claim-evidence").upload(
                            key, p.read_bytes(), {"content-type": ctype, "upsert": "true"})
                        row["storage_path"] = key
                        uploaded += 1
                    except Exception as exc:
                        failed += 1
                        print(f"    ! upload failed {p.name}: {str(exc)[:90]}")
            batch.append(clean(table, row))
        if batch:
            sb.table(table).insert(batch).execute()
            print(f"  {table}: {len(batch)}")
    if uploaded or failed:
        print(f"  evidence files uploaded: {uploaded}, failed: {failed}")

    total = len((sb.table("claims").select("claim_id").execute().data or []))
    print(f"\ndone — Supabase now holds {total} claim(s)")


if __name__ == "__main__":
    main()
