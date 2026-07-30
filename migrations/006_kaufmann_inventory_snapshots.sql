create table if not exists public.kaufmann_inventory_snapshots (
  id bigserial primary key,
  kaufmann_product_id bigint not null references public.kaufmann_products(id) on delete cascade,
  source_parent_id text not null,
  source_color_id text not null,
  canonical_url text,
  source_url text,
  checked_at timestamptz not null,
  checked_bucket date not null,
  refresh_status text not null default 'ok',
  current_price numeric,
  list_price numeric,
  webshop_sizes jsonb not null default '[]'::jsonb,
  aarhus_inventory jsonb not null default '{"stores": {}}'::jsonb,
  aarhus_total_stock integer not null default 0,
  aarhus_available boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (kaufmann_product_id, checked_bucket)
);

create index if not exists kaufmann_inventory_snapshots_product_checked_at_idx
  on public.kaufmann_inventory_snapshots (kaufmann_product_id, checked_at desc);

create index if not exists kaufmann_inventory_snapshots_checked_bucket_idx
  on public.kaufmann_inventory_snapshots (checked_bucket desc);

create index if not exists kaufmann_inventory_snapshots_aarhus_available_idx
  on public.kaufmann_inventory_snapshots (aarhus_available);

alter table public.kaufmann_inventory_snapshots enable row level security;
