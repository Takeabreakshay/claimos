-- Migration 004: model-specific OEM parts pricing
--
-- A Honda City door costs ~2.5x a Maruti Alto door; a single segment multiplier
-- can't capture that. This table holds a per-model [low, high] price band for
-- each damageable part, so the rate card prices a matched claim from the model's
-- own OEM basket instead of a segment-scaled generic. Rows are seeded from
-- config/model_parts.yaml by scripts/seed_model_parts.py.
--
-- Run once in the Supabase SQL editor (or via psql). Safe to re-run.

create table if not exists model_parts (
    id          bigint generated always as identity primary key,
    make        text    not null,
    model       text    not null,
    segment     text,
    part        text    not null,
    price_low   numeric not null,
    price_high  numeric not null,
    currency    text    not null default 'INR',
    source      text    default 'curated-2025',
    updated_at  timestamptz not null default now(),
    unique (make, model, part)
);

create index if not exists model_parts_make_model_idx on model_parts (lower(make), lower(model));

-- Read-only to the anon/service roles the app uses; writes happen via the seed
-- script with the service key. RLS left off (reference data, no PII).
