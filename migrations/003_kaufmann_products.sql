create table if not exists public.kaufmann_products (
  id bigserial primary key,
  source_parent_id text not null,
  source_color_id text not null,
  source_url text not null,
  canonical_url text,
  source_product_number text,
  name text,
  brand text,
  color text,
  color_group text,
  current_price numeric,
  list_price numeric,
  currency text not null default 'DKK',
  description text,
  materials text[] not null default '{}'::text[],
  fit text,
  category text,
  images text[] not null default '{}'::text[],
  webshop_sizes jsonb not null default '[]'::jsonb,
  aarhus_inventory jsonb not null default '{"stores": {}}'::jsonb,
  aarhus_total_stock integer not null default 0,
  aarhus_available boolean not null default false,
  raw jsonb not null default '{}'::jsonb,
  first_seen_at timestamptz not null default now(),
  scraped_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (source_parent_id, source_color_id)
);

create index if not exists kaufmann_products_source_parent_idx
  on public.kaufmann_products (source_parent_id);

create index if not exists kaufmann_products_source_color_idx
  on public.kaufmann_products (source_color_id);

create index if not exists kaufmann_products_brand_idx
  on public.kaufmann_products (brand);

create index if not exists kaufmann_products_aarhus_available_idx
  on public.kaufmann_products (aarhus_available);

create index if not exists kaufmann_products_scraped_at_idx
  on public.kaufmann_products (scraped_at desc);

create index if not exists kaufmann_products_aarhus_inventory_gin_idx
  on public.kaufmann_products using gin (aarhus_inventory);

alter table public.kaufmann_products enable row level security;
