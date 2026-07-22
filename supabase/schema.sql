-- =============================================================================
-- ClaimOS — Supabase schema (Postgres)
-- Run this in Supabase Studio -> SQL Editor -> New query -> Run.
-- Then create a PUBLIC-off storage bucket named 'claim-evidence' (step at bottom).
-- =============================================================================

create extension if not exists "uuid-ossp";
create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- 1. POLICY  (in prod this is a read-through view of the core policy DB)
-- ---------------------------------------------------------------------------
create table if not exists policies (
  policy_id            text primary key,
  customer_id          text not null,
  owner_name           text,
  owner_phone          text,
  owner_geo            text check (owner_geo in ('metro','urban','rural')),
  vehicle_registration_no text,
  vehicle_make         text,
  vehicle_model        text,
  manufacture_year     int,
  fuel_type            text,
  idv                  numeric(12,2) not null,
  product_type         text check (product_type in ('comprehensive','third_party_only','od_only')),
  policy_status        text not null default 'active' check (policy_status in ('active','lapsed')),
  policy_start_date    date,
  policy_end_date      date,
  deductible           numeric(10,2) default 1000,
  ncb_percent          numeric(5,2) default 0,
  add_ons              text[] default '{}',
  exclusions           text[] default '{}',
  created_at           timestamptz default now()
);
create index if not exists idx_policies_customer on policies(customer_id);
create index if not exists idx_policies_vehicle  on policies(vehicle_registration_no);

-- ---------------------------------------------------------------------------
-- 2. CLAIMS  (the live workflow record — one row per FNOL)
-- ---------------------------------------------------------------------------
create table if not exists claims (
  claim_id             text primary key,
  policy_id            text references policies(policy_id),
  customer_id          text,
  -- FNOL intake
  claim_type           text check (claim_type in ('OD','TP','theft_total')),
  incident_date        timestamptz,
  fnol_timestamp       timestamptz default now(),
  intimation_delay_hours numeric(10,2),
  intimation_gt_48h    boolean default false,
  intimation_reason_valid boolean default true,
  intimation_reason_text text,
  incident_description text,
  incident_lat         numeric(10,6),
  incident_lng         numeric(10,6),
  geo                  text,
  incident_severity    text check (incident_severity in ('minor','moderate','severe','total')),
  claim_amount         numeric(12,2),
  idv                  numeric(12,2),
  vehicle_age_years    numeric(5,2),
  -- eligibility / process
  garage_id            text,
  garage_type          text check (garage_type in ('network','non_network')),
  surveyor_id          text,
  bank_account         text,
  driver_valid_license boolean default true,
  dui_flag             boolean default false,
  modification_actual  boolean default false,
  modification_declared boolean default false,
  modification_undeclared boolean generated always as
                        (modification_actual and not modification_declared) stored,
  fir_required         boolean default false,
  fir_filed            boolean default false,
  fir_number           text,
  third_party_involved boolean default false,
  injury_hint          boolean default false,
  -- evidence rollup (computed live from claim_photos)
  num_photos           int default 0,
  photo_quality_score  numeric(4,3),
  photo_reuse_flag     boolean default false,
  -- workflow state
  status               text not null default 'intake'
                        check (status in ('intake','evidence','verifying','scored','retake',
                                          'awaiting_officer','investigating','approved',
                                          'declined','paid')),
  lane                 text check (lane in ('lane1_touchless','lane2_assisted',
                                            'lane3_investigative','retake','coverage_reject')),
  assigned_to          text,
  created_at           timestamptz default now(),
  updated_at           timestamptz default now()
);
create index if not exists idx_claims_status on claims(status);
create index if not exists idx_claims_lane   on claims(lane);
create index if not exists idx_claims_policy on claims(policy_id);
create index if not exists idx_claims_garage on claims(garage_id);
create index if not exists idx_claims_surveyor on claims(surveyor_id);
create index if not exists idx_claims_bank   on claims(bank_account);

-- ---------------------------------------------------------------------------
-- 3. CLAIM PHOTOS  — storage refs + LIVE computed vision signals
--    phash powers cross-claim photo-reuse fraud detection.
-- ---------------------------------------------------------------------------
create table if not exists claim_photos (
  id              uuid primary key default gen_random_uuid(),
  claim_id        text references claims(claim_id) on delete cascade,
  storage_path    text not null,
  angle_label     text,
  phash           text,                      -- perceptual hash (reuse detection)
  quality_score   numeric(4,3),              -- 0-1 sharpness/exposure
  blur_variance   numeric(12,4),             -- Laplacian variance
  is_blurry       boolean default false,
  exif_timestamp  timestamptz,
  exif_lat        numeric(10,6),
  exif_lng        numeric(10,6),
  width           int,
  height          int,
  created_at      timestamptz default now()
);
create index if not exists idx_photos_claim on claim_photos(claim_id);
create index if not exists idx_photos_phash on claim_photos(phash);

-- ---------------------------------------------------------------------------
-- 4. CLAIM DOCUMENTS — uploads + LIVE OCR output
-- ---------------------------------------------------------------------------
create table if not exists claim_documents (
  id              uuid primary key default gen_random_uuid(),
  claim_id        text references claims(claim_id) on delete cascade,
  doc_type        text check (doc_type in ('rc_copy','driving_licence','policy_copy',
                                           'fir','repair_estimate','final_bill',
                                           'claim_form','bank_details','other')),
  storage_path    text not null,
  ocr_text        text,
  ocr_fields      jsonb default '{}'::jsonb,   -- {reg_no, dl_no, name, amount, ...}
  ocr_confidence  numeric(4,3),
  verified        boolean default false,
  verified_by     text,                        -- 'VAHAN' | 'DigiLocker' | 'MOCK:...'
  verification_note text,
  created_at      timestamptz default now()
);
create index if not exists idx_docs_claim on claim_documents(claim_id);

-- ---------------------------------------------------------------------------
-- 5. CLAIM SCORES — every model output + the triage decision (audit-grade)
-- ---------------------------------------------------------------------------
create table if not exists claim_scores (
  id                  uuid primary key default gen_random_uuid(),
  claim_id            text references claims(claim_id) on delete cascade,
  p_fraud             numeric(6,5),
  p_escalation        numeric(6,5),
  model_confidence    numeric(6,5),
  c_fraud             numeric(6,5),
  c_escalation        numeric(6,5),
  c_cost              numeric(6,5),
  cost_p10            numeric(12,2),
  cost_p50            numeric(12,2),
  cost_p90            numeric(12,2),
  ring_risk           numeric(6,5),
  component_size      int,
  coverage_clear      text,
  coverage_reason     text,
  legal_weak_reject_flag boolean default false,
  lane                text,
  lane_reasons        text[],
  fraud_drivers       jsonb,      -- SHAP top contributors
  cost_drivers        jsonb,
  escalation_drivers  jsonb,
  plain_reason        text,
  model_version       text,
  scored_at           timestamptz default now()
);
create index if not exists idx_scores_claim on claim_scores(claim_id, scored_at desc);

-- ---------------------------------------------------------------------------
-- 6. DECISIONS / OVERRIDES — the human-in-the-loop feedback label
-- ---------------------------------------------------------------------------
create table if not exists claim_decisions (
  id              uuid primary key default gen_random_uuid(),
  claim_id        text references claims(claim_id) on delete cascade,
  actor           text not null,            -- officer id or 'SYSTEM'
  action          text not null check (action in ('approve','decline','override',
                                                  'request_evidence','assign_investigator',
                                                  'settle')),
  from_lane       text,
  to_lane         text,
  override_reason text,                     -- feeds the model feedback loop
  settlement_amount numeric(12,2),
  created_at      timestamptz default now()
);
create index if not exists idx_decisions_claim on claim_decisions(claim_id);

-- ---------------------------------------------------------------------------
-- 7. AUDIT TRAIL — every state transition (regulator-facing)
-- ---------------------------------------------------------------------------
create table if not exists claim_events (
  id          bigserial primary key,
  claim_id    text references claims(claim_id) on delete cascade,
  event       text not null,
  detail      jsonb default '{}'::jsonb,
  actor       text default 'SYSTEM',
  created_at  timestamptz default now()
);
create index if not exists idx_events_claim on claim_events(claim_id, created_at);

-- ---------------------------------------------------------------------------
-- 8. SETTLEMENTS
-- ---------------------------------------------------------------------------
create table if not exists settlements (
  id                uuid primary key default gen_random_uuid(),
  claim_id          text references claims(claim_id) on delete cascade,
  gross_amount      numeric(12,2),
  depreciation      numeric(12,2) default 0,
  consumables       numeric(12,2) default 0,
  deductible        numeric(12,2) default 0,
  net_payable       numeric(12,2),
  total_loss        boolean default false,
  utr_reference     text,
  paid_at           timestamptz,
  created_at        timestamptz default now()
);

-- ---------------------------------------------------------------------------
-- 9. LIVE OPS VIEWS — power the console + dashboard without extra queries
-- ---------------------------------------------------------------------------
create or replace view v_claim_latest_score as
select distinct on (claim_id) *
from claim_scores
order by claim_id, scored_at desc;

create or replace view v_triage_queue as
select c.claim_id, c.policy_id, c.claim_type, c.claim_amount, c.incident_severity,
       c.status, c.lane, c.assigned_to, c.created_at,
       s.p_fraud, s.p_escalation, s.model_confidence,
       s.cost_p50, s.ring_risk, s.legal_weak_reject_flag, s.plain_reason
from claims c
left join v_claim_latest_score s on s.claim_id = c.claim_id;

-- Cross-claim photo reuse: same perceptual hash on DIFFERENT claims = red flag.
create or replace view v_photo_reuse as
select p.phash, count(distinct p.claim_id) as claim_count,
       array_agg(distinct p.claim_id) as claim_ids
from claim_photos p
where p.phash is not null
group by p.phash
having count(distinct p.claim_id) > 1;

-- Shared-entity collusion candidates (garage / surveyor / bank reused across claims).
create or replace view v_entity_links as
select 'garage'   as entity_kind, garage_id    as entity, count(*) n, array_agg(claim_id) claim_ids
  from claims where garage_id is not null group by garage_id having count(*) > 1
union all
select 'surveyor', surveyor_id, count(*), array_agg(claim_id)
  from claims where surveyor_id is not null group by surveyor_id having count(*) > 1
union all
select 'bank', bank_account, count(*), array_agg(claim_id)
  from claims where bank_account is not null group by bank_account having count(*) > 1;

-- ---------------------------------------------------------------------------
-- 10. updated_at trigger
-- ---------------------------------------------------------------------------
create or replace function touch_updated_at() returns trigger as $$
begin new.updated_at = now(); return new; end;
$$ language plpgsql;

drop trigger if exists trg_claims_touch on claims;
create trigger trg_claims_touch before update on claims
for each row execute function touch_updated_at();

-- ---------------------------------------------------------------------------
-- 11. RLS — service_role (our backend) bypasses RLS. Enable + lock down anon.
--     The ops console runs server-side with the SERVICE key, so this is safe.
-- ---------------------------------------------------------------------------
alter table policies         enable row level security;
alter table claims           enable row level security;
alter table claim_photos     enable row level security;
alter table claim_documents  enable row level security;
alter table claim_scores     enable row level security;
alter table claim_decisions  enable row level security;
alter table claim_events     enable row level security;
alter table settlements      enable row level security;

-- =============================================================================
-- STORAGE BUCKET (run after the tables):
--   Supabase Studio -> Storage -> New bucket
--     name: claim-evidence
--     public: OFF  (we serve via signed URLs)
-- =============================================================================
insert into storage.buckets (id, name, public)
values ('claim-evidence', 'claim-evidence', false)
on conflict (id) do nothing;
