create table if not exists product_inventory_snapshots (
  id bigserial primary key,
  product_id bigint not null references products(id) on delete cascade,
  store text,
  product_url text not null,
  checked_at timestamptz not null,
  checked_bucket timestamptz not null,
  price numeric,
  sizes jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (product_id, checked_bucket)
);

create index if not exists product_inventory_snapshots_product_checked_at_idx
  on product_inventory_snapshots (product_id, checked_at desc);

create index if not exists product_inventory_snapshots_checked_bucket_idx
  on product_inventory_snapshots (checked_bucket desc);
