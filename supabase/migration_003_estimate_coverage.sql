-- =============================================================================
-- ClaimOS migration 003 — line-item estimate, coverage matrix, settlement
-- (LOGIC §1/§2/§3/§4/§5). Run in Supabase Studio -> SQL Editor. Safe to re-run.
--
-- The app self-heals if these are absent (the fields simply aren't persisted, so
-- they won't survive a reload), and the routing reason chain (lane_reasons)
-- already persists without this migration. Running it makes the richer panels —
-- the line-item estimate, the settlement waterfall, the 4-state coverage read —
-- durable across reloads. It does NOT enable the logic; the engine runs regardless.
-- =============================================================================

-- --- claims: FNOL coverage-matrix + rate-card inputs -------------------------
alter table claims add column if not exists product_type          text;
alter table claims add column if not exists period_from           timestamptz;
alter table claims add column if not exists period_to             timestamptz;
alter table claims add column if not exists cubic_capacity        numeric(7,1);
alter table claims add column if not exists vehicle_type          text;
alter table claims add column if not exists voluntary_excess      numeric(10,2);
alter table claims add column if not exists add_ons               text[];
alter table claims add column if not exists usage_class           text;
alter table claims add column if not exists claim_free_years      int;
alter table claims add column if not exists od_premium_next_year  numeric(12,2);
alter table claims add column if not exists claims_this_year      int;
alter table claims add column if not exists invoice_value         numeric(12,2);
alter table claims add column if not exists engine_damage         boolean default false;
alter table claims add column if not exists make                  text;
alter table claims add column if not exists model                 text;
alter table claims add column if not exists segment               text;
alter table claims add column if not exists city_tier             text;
alter table claims add column if not exists is_ev                 boolean default false;
alter table claims add column if not exists is_import             boolean default false;
alter table claims add column if not exists cv_parts_all          text[];
alter table claims add column if not exists cv_confidence         numeric(4,3);

-- --- claim_scores: line-item estimate + coverage state + settlement ---------
alter table claim_scores add column if not exists coverage_state          text;
alter table claim_scores add column if not exists coverage_state_reasons  text[];
alter table claim_scores add column if not exists line_item_estimate      numeric(12,2);
alter table claim_scores add column if not exists line_item_p10           numeric(12,2);
alter table claim_scores add column if not exists line_item_p90           numeric(12,2);
alter table claim_scores add column if not exists line_items              jsonb;
alter table claim_scores add column if not exists reconciliation_ratio    numeric(8,3);
alter table claim_scores add column if not exists inflation_flag          boolean default false;
alter table claim_scores add column if not exists has_structural          boolean default false;
alter table claim_scores add column if not exists has_airbag              boolean default false;
alter table claim_scores add column if not exists damage_mismatch         jsonb;
alter table claim_scores add column if not exists settlement              jsonb;
