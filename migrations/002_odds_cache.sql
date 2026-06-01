create table if not exists public.bot_odds_cache (
    cache_key text primary key,
    payload jsonb not null,
    fetched_at timestamptz not null default now(),
    expires_at timestamptz not null
);

create index if not exists bot_odds_cache_expires_idx
    on public.bot_odds_cache (expires_at);

alter table public.bot_odds_cache enable row level security;
