"""Seed the Supabase `model_parts` table from config/model_parts.yaml.

Prereq: run supabase/migration_004_model_parts.sql once (Supabase SQL editor) to
create the table - the REST/service key cannot issue DDL. Then:

    python scripts/seed_model_parts.py

Idempotent: upserts on (make, model, part), so re-running just refreshes prices.
If the table is missing it prints the exact next step instead of failing loudly.
The app works without this step - rate_card falls back to the bundled YAML - so
seeding only moves the source of truth into Supabase for other devices to share.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _rows() -> list[dict]:
    cfg = yaml.safe_load((ROOT / "config" / "model_parts.yaml").read_text(encoding="utf-8"))
    out: list[dict] = []
    for entry in (cfg.get("models") or {}).values():
        make, model = entry.get("make", ""), entry.get("model", "")
        seg = entry.get("segment")
        for part, band in (entry.get("parts") or {}).items():
            out.append({
                "make": make, "model": model, "segment": seg, "part": part,
                "price_low": float(band[0]), "price_high": float(band[1]),
                "currency": "INR", "source": "curated-2025",
            })
    return out


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except Exception:
        pass

    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_KEY", "").strip() or os.getenv("SUPABASE_KEY", "").strip()
    rows = _rows()
    print(f"Prepared {len(rows)} rows from config/model_parts.yaml "
          f"({len({(r['make'], r['model']) for r in rows})} models).")

    if not (url and key):
        print("No SUPABASE_URL / SUPABASE_SERVICE_KEY in env - nothing to seed. "
              "The app still prices from the YAML config.")
        return 0

    from supabase import create_client
    sb = create_client(url, key)
    try:
        sb.table("model_parts").upsert(rows, on_conflict="make,model,part").execute()
    except Exception as exc:
        msg = str(exc)
        if "model_parts" in msg and ("does not exist" in msg or "PGRST205" in msg or "404" in msg):
            print("\nTable `model_parts` does not exist yet. Create it first by running")
            print("  supabase/migration_004_model_parts.sql")
            print("in the Supabase SQL editor, then re-run this script.")
            return 2
        raise
    n = sb.table("model_parts").select("id", count="exact").execute()
    print(f"Seeded. model_parts now holds {getattr(n, 'count', '?')} rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
