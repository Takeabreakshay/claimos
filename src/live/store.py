"""Persistence layer - Supabase (Postgres + Storage) with a local fallback.

If SUPABASE_URL / SUPABASE_SERVICE_KEY are set, everything is written to your
Supabase project (claims, photos, docs, scores, decisions, audit events, files).
If they are NOT set, the same API writes to a local SQLite + data/uploads folder
so the workflow is fully usable immediately and you can point it at Supabase
later without changing a line of app code.

Run supabase/schema.sql in the Supabase SQL editor first.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src import constants

BUCKET = os.getenv("SUPABASE_BUCKET", "claim-evidence")
LOCAL_DB = constants.DATA_DIR / "live" / "claimos.db"
LOCAL_FILES = constants.DATA_DIR / "live" / "uploads"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_claim_id() -> str:
    return "CLM-" + uuid.uuid4().hex[:10].upper()


# --------------------------------------------------------------------------- #
class Store:
    """One interface, two backends."""

    def __init__(self) -> None:
        # Tolerate a pasted REST endpoint: the SDK appends /rest/v1 itself.
        url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        for suffix in ("/rest/v1", "/rest"):
            if url.endswith(suffix):
                url = url[: -len(suffix)]
        self.url = url
        self.key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
        self.mode = "supabase" if (self.url and self.key) else "local"
        self.init_error: str | None = None
        self._sb = None
        # Write-through overlay (Supabase only). A pending migration means the
        # backend silently drops columns it doesn't have yet; the overlay keeps
        # the FULL row in-process so the running session still sees every field
        # (rate-card estimate, coverage inputs, settlement...). It is per-process
        # - migrations are what make the fields durable ACROSS restarts.
        self._full_claims: dict[str, dict[str, Any]] = {}
        self._full_children: dict[tuple[str, str], list[dict[str, Any]]] = {}

        if self.mode == "supabase":
            # Retry the startup probe: a transient DNS/network blip during a cold
            # start (common on Render free tier) must NOT pin the whole process to
            # local storage for its lifetime. A schema error won't self-heal, so we
            # stop retrying on that immediately.
            attempts = int(os.getenv("SUPABASE_CONNECT_ATTEMPTS", "4"))
            last_msg = ""
            for i in range(attempts):
                try:
                    from supabase import create_client

                    self._sb = create_client(self.url, self.key)
                    # Credentials can be valid while the schema was never applied.
                    # Probe so we degrade cleanly instead of 500-ing later (PGRST205).
                    self._sb.table("claims").select("claim_id").limit(1).execute()
                    self.init_error = None
                    break
                except Exception as exc:
                    last_msg = str(exc)
                    if "PGRST205" in last_msg or "schema cache" in last_msg:
                        self.init_error = (
                            "Supabase reachable but the schema is missing - run "
                            "supabase/schema.sql in the SQL editor, then restart."
                        )
                        self.mode = "local"
                        break
                    self.init_error = f"Supabase unavailable: {last_msg[:200]}"
                    if i < attempts - 1:
                        import time
                        time.sleep(1.5 * (i + 1))   # 1.5s, 3s, 4.5s backoff
            else:
                self.mode = "local"                 # exhausted retries -> local

        if self.mode == "local":
            self._init_local()

    # ---------------- local backend ----------------
    def _init_local(self) -> None:
        LOCAL_DB.parent.mkdir(parents=True, exist_ok=True)
        LOCAL_FILES.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(LOCAL_DB)
        con.executescript(
            """
            create table if not exists claims(claim_id text primary key, data text);
            create table if not exists policies(id text primary key, claim_id text, data text);
            create table if not exists claim_photos(id text primary key, claim_id text, data text);
            create table if not exists claim_documents(id text primary key, claim_id text, data text);
            create table if not exists claim_scores(id text primary key, claim_id text, data text);
            create table if not exists claim_decisions(id text primary key, claim_id text, data text);
            create table if not exists settlements(id text primary key, claim_id text, data text);
            create table if not exists claim_events(id integer primary key autoincrement,
                                                    claim_id text, data text);
            """
        )
        con.commit()
        con.close()

    def _lcon(self):
        return sqlite3.connect(LOCAL_DB)

    @staticmethod
    def _missing_col(err: str) -> str | None:
        """Extract the column PostgREST says it can't find (PGRST204)."""
        import re

        m = re.search(r"Could not find the '([^']+)' column", err)
        return m.group(1) if m else None

    # ---------------- generic ops ----------------
    def _overlay_put(self, table: str, row: dict[str, Any]) -> None:
        """Remember the full row in-process so backend column-stripping is invisible."""
        if self.mode != "supabase":
            return
        if table == "claims":
            cid = row.get("claim_id")
            if cid:
                self._full_claims[cid] = {**self._full_claims.get(cid, {}), **row}
        elif table in ("claim_photos", "claim_documents", "claim_scores"):
            cid = row.get("claim_id")
            if cid:
                self._full_children.setdefault((table, cid), []).append(dict(row))

    def insert(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        row = {k: v for k, v in row.items() if v is not None}
        self._overlay_put(table, row)
        if self.mode == "supabase":
            # Self-heal on schema drift: if the code writes a column the table
            # doesn't have yet (a pending migration), drop it and retry rather
            # than 500 the request. Newer fields degrade to "not persisted".
            attempt = dict(row)
            # Budget one retry per column so a pending migration with many new
            # columns still drops them all rather than giving up mid-strip and
            # skipping the insert (which would break child-row foreign keys).
            for _ in range(len(row) + 2):
                try:
                    res = self._sb.table(table).insert(attempt).execute()
                    return (res.data or [attempt])[0]
                except Exception as exc:
                    col = self._missing_col(str(exc))
                    if col and col in attempt:
                        attempt.pop(col)
                        continue
                    raise
            return attempt
        rid = str(row.get("id") or row.get("claim_id") or uuid.uuid4())
        con = self._lcon()
        if table == "claim_events":
            con.execute("insert into claim_events(claim_id,data) values(?,?)",
                        (row.get("claim_id"), json.dumps(row, default=str)))
        else:
            con.execute(
                f"insert or replace into {table}(id,claim_id,data) values(?,?,?)"
                if table != "claims" else
                "insert or replace into claims(claim_id,data) values(?,?)",
                (rid, row.get("claim_id"), json.dumps(row, default=str))
                if table != "claims" else (rid, json.dumps(row, default=str)),
            )
        con.commit()
        con.close()
        return row

    def upsert(self, table: str, row: dict[str, Any], on_conflict: str) -> dict[str, Any]:
        """Insert-or-update by ``on_conflict`` key (used for the policy master)."""
        row = {k: v for k, v in row.items() if v is not None}
        if self.mode == "supabase":
            res = self._sb.table(table).upsert(row, on_conflict=on_conflict).execute()
            return (res.data or [row])[0]
        return self.insert(table, row)

    def update_claim(self, claim_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        if self.mode == "supabase":
            self._full_claims[claim_id] = {
                **self._full_claims.get(claim_id, {}), "claim_id": claim_id, **patch}
            attempt = dict(patch)
            for _ in range(len(patch) + 2):
                try:
                    res = self._sb.table("claims").update(attempt).eq(
                        "claim_id", claim_id).execute()
                    return (res.data or [attempt])[0]
                except Exception as exc:
                    col = self._missing_col(str(exc))
                    if col and col in attempt:
                        attempt.pop(col)
                        continue
                    raise
            return attempt
        cur = self.get_claim(claim_id) or {"claim_id": claim_id}
        cur.update(patch)
        con = self._lcon()
        con.execute("insert or replace into claims(claim_id,data) values(?,?)",
                    (claim_id, json.dumps(cur, default=str)))
        con.commit()
        con.close()
        return cur

    def _exec_read(self, query, tries: int = 3):
        """Run a Supabase read, retrying transient stalls (free-tier reads
        occasionally time out). Reads are idempotent, so retrying is safe."""
        import time as _t
        last = None
        for i in range(tries):
            try:
                return query.execute()
            except Exception as exc:
                last = exc
                if i < tries - 1:
                    _t.sleep(0.4 * (i + 1))
        raise last

    def get_claim(self, claim_id: str) -> dict[str, Any] | None:
        if self.mode == "supabase":
            res = self._exec_read(self._sb.table("claims").select("*").eq("claim_id", claim_id))
            row = (res.data or [None])[0]
            cached = self._full_claims.get(claim_id)
            if cached:
                return {**(row or {}), **cached}  # overlay recovers stripped fields
            return row
        con = self._lcon()
        r = con.execute("select data from claims where claim_id=?", (claim_id,)).fetchone()
        con.close()
        return json.loads(r[0]) if r else None

    def list_claims(self, limit: int = 200) -> list[dict[str, Any]]:
        if self.mode == "supabase":
            res = self._exec_read(self._sb.table("claims").select("*")
                   .order("created_at", desc=True).limit(limit))
            rows = res.data or []
            if self._full_claims:
                rows = [{**r, **self._full_claims.get(r.get("claim_id"), {})} for r in rows]
            return rows
        con = self._lcon()
        rows = con.execute("select data from claims").fetchall()
        con.close()
        out = [json.loads(r[0]) for r in rows]
        return sorted(out, key=lambda c: c.get("created_at", ""), reverse=True)[:limit]

    def list_child(self, table: str, claim_id: str) -> list[dict[str, Any]]:
        if self.mode == "supabase":
            cached = self._full_children.get((table, claim_id))
            if cached is not None:
                return [dict(r) for r in cached]  # full rows, no stripped columns
            res = self._exec_read(self._sb.table(table).select("*").eq("claim_id", claim_id))
            return res.data or []
        con = self._lcon()
        rows = con.execute(f"select data from {table} where claim_id=?", (claim_id,)).fetchall()
        con.close()
        return [json.loads(r[0]) for r in rows]

    def latest_score(self, claim_id: str) -> dict[str, Any] | None:
        rows = self.list_child("claim_scores", claim_id)
        if not rows:
            return None
        return sorted(rows, key=lambda r: r.get("scored_at", ""), reverse=True)[0]

    def latest_scores(self, claim_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Latest score for many claims in ONE query (avoids N+1 on list views)."""
        if not claim_ids:
            return {}
        if self.mode == "supabase":
            res = self._exec_read(self._sb.table("claim_scores").select("*")
                   .in_("claim_id", claim_ids))
            rows = res.data or []
        else:
            con = self._lcon()
            qs = ",".join("?" * len(claim_ids))
            rows = [json.loads(r[0]) for r in con.execute(
                f"select data from claim_scores where claim_id in ({qs})",
                claim_ids).fetchall()]
            con.close()
        latest: dict[str, dict[str, Any]] = {}
        for r in rows:
            cid = r.get("claim_id")
            if not cid:
                continue
            cur = latest.get(cid)
            if cur is None or r.get("scored_at", "") > cur.get("scored_at", ""):
                latest[cid] = r
        # in-process overlay (Supabase column-stripping guard) wins when present
        for cid in claim_ids:
            ov = self._full_children.get(("claim_scores", cid))
            if ov:
                latest[cid] = sorted(ov, key=lambda r: r.get("scored_at", ""),
                                     reverse=True)[0]
        return latest

    # ---------------- photo-hash corpus (cross-claim reuse detection) --------
    def known_phashes(self, exclude_claim: str | None = None) -> list[tuple[str, str]]:
        """[(phash, claim_id)] for every photo already in the system."""
        if self.mode == "supabase":
            res = self._exec_read(self._sb.table("claim_photos").select("phash,claim_id"))
            rows = res.data or []
        else:
            con = self._lcon()
            raw = con.execute("select data from claim_photos").fetchall()
            con.close()
            rows = [json.loads(r[0]) for r in raw]
        return [(r["phash"], r["claim_id"]) for r in rows
                if r.get("phash") and r.get("claim_id") != exclude_claim]

    # ---------------- storage ----------------
    def upload(self, claim_id: str, filename: str, data: bytes,
               content_type: str = "application/octet-stream") -> str:
        path = f"{claim_id}/{uuid.uuid4().hex[:8]}_{filename}"
        if self.mode == "supabase":
            try:
                self._sb.storage.from_(BUCKET).upload(
                    path, data, {"content-type": content_type, "upsert": "true"})
                return path
            except Exception:
                pass  # fall through to local
        dest = LOCAL_FILES / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return str(dest)

    def signed_url(self, path: str, seconds: int = 3600) -> str | None:
        if self.mode == "supabase" and not Path(path).exists():
            try:
                res = self._sb.storage.from_(BUCKET).create_signed_url(path, seconds)
                return res.get("signedURL") or res.get("signedUrl") or res.get("signed_url")
            except Exception:
                return None
        return path

    def download(self, path: str) -> bytes | None:
        """Fetch the raw bytes of a stored file (Supabase Storage or local). Used
        to stream evidence through our own /api/media endpoint - robust across
        both backends and free of signed-URL / CORS quirks."""
        if not path:
            return None
        p = Path(path)
        if p.exists():
            try:
                return p.read_bytes()
            except Exception:
                return None
        if self.mode == "supabase":
            try:
                return self._sb.storage.from_(BUCKET).download(path)
            except Exception:
                return None
        return None

    # ---------------- audit ----------------
    def event(self, claim_id: str, event: str, detail: dict | None = None,
              actor: str = "SYSTEM") -> None:
        self.insert("claim_events", {"claim_id": claim_id, "event": event,
                                     "detail": detail or {}, "actor": actor,
                                     "created_at": _now()})


_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store
