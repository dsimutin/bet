-- Supabase/PostgreSQL runtime persistence for the betting analytics bot.
-- Run once in Supabase Dashboard -> SQL Editor before enabling REQUIRE_DATABASE=true.

create table if not exists public.bot_signal_ledger (
    signal_id text primary key,
    sport text,
    strategy_id text,
    event_id text,
    event_date date,
    market_key text,
    selection text,
    ledger_status text not null default 'open',
    delivery_status text not null default 'registered',
    payload jsonb not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists bot_signal_ledger_status_idx
    on public.bot_signal_ledger (ledger_status, sport);
create index if not exists bot_signal_ledger_event_idx
    on public.bot_signal_ledger (event_date, event_id);
create index if not exists bot_signal_ledger_delivery_idx
    on public.bot_signal_ledger (delivery_status);

create table if not exists public.bot_job_runs (
    id bigserial primary key,
    job_name text not null,
    status text not null,
    message text,
    meta_json jsonb not null default '{}'::jsonb,
    started_at timestamptz not null default now(),
    finished_at timestamptz
);

create index if not exists bot_job_runs_job_started_idx
    on public.bot_job_runs (job_name, started_at desc);

-- Tables are backend-only. The bot connects with the PostgreSQL DATABASE_URL,
-- not through the public Data API. Keep them inaccessible to anon/authenticated API users.
alter table public.bot_signal_ledger enable row level security;
alter table public.bot_job_runs enable row level security;

comment on table public.bot_signal_ledger is
    'Persistent source of truth for paper signals, Telegram delivery and settlement state.';
comment on table public.bot_job_runs is
    'Append-only operational audit trail for scheduled bot jobs.';
