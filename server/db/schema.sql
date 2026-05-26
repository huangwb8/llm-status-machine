create table if not exists schema_migrations (
  version text primary key,
  applied_at timestamptz not null default now()
);

create table if not exists documents (
  collection text not null,
  id text not null,
  body jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (collection, id)
);

create table if not exists runs (
  id text primary key,
  body jsonb not null,
  status text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists sessions (
  id text primary key,
  run_id text not null references runs(id) on delete cascade,
  body jsonb not null,
  status text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists session_events (
  id text primary key,
  run_id text not null references runs(id) on delete cascade,
  session_id text not null references sessions(id) on delete cascade,
  body jsonb not null,
  ts timestamptz not null default now()
);

create index if not exists documents_collection_updated_idx on documents(collection, updated_at desc);
create index if not exists runs_status_updated_idx on runs(status, updated_at desc);
create index if not exists sessions_run_id_idx on sessions(run_id);
create index if not exists session_events_session_id_ts_idx on session_events(session_id, ts);
