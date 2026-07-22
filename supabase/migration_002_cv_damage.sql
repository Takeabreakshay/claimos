-- =============================================================================
-- ClaimOS migration 002 — AI damage-assessment fields (PS deliverable #3)
-- Run in Supabase Studio -> SQL Editor. Safe to re-run (IF NOT EXISTS).
-- The app self-heals if these are absent (CV fields simply aren't persisted),
-- so this migration is what turns damage assessment durable, not what enables it.
-- =============================================================================

alter table claim_photos add column if not exists cv_severity     text;
alter table claim_photos add column if not exists cv_parts        text[];
alter table claim_photos add column if not exists cv_confidence   numeric(4,3);

alter table claims add column if not exists cv_severity           text;
alter table claims add column if not exists cv_severity_mismatch  boolean default false;
