alter table public.kaufmann_products
  add column if not exists aarhus_inventory jsonb not null default '{}'::jsonb;

do $$
begin
  if exists (
    select 1
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'kaufmann_products'
      and column_name = 'aarhus_sizes'
  ) then
    execute $backfill$
      with expanded as (
        select
          kp.id,
          size_item,
          store_item,
          store_item->>'seo_url' as seo_url,
          coalesce((store_item->>'stock')::integer, 0) as stock
        from public.kaufmann_products kp
        cross join lateral jsonb_array_elements(kp.aarhus_sizes) as size_item
        cross join lateral jsonb_array_elements(coalesce(size_item->'stores', '[]'::jsonb)) as store_item
        where kp.aarhus_inventory = '{}'::jsonb
      ),
      store_sizes as (
        select
          id,
          seo_url,
          max(store_item->>'label') as label,
          max(store_item->>'warehouse_id') as warehouse_id,
          sum(stock) as total_stock,
          bool_or(stock > 0 or coalesce((store_item->>'available')::boolean, false)) as available,
          jsonb_agg(
            jsonb_build_object(
              'size', size_item->>'size',
              'stock', stock,
              'available', stock > 0 or coalesce((store_item->>'available')::boolean, false),
              'source_size_variant_id', size_item->>'source_size_variant_id',
              'source_product_number', size_item->>'source_product_number'
            )
            order by size_item->>'size'
          ) as sizes
        from expanded
        where seo_url is not null
        group by id, seo_url
      ),
      inventory as (
        select
          id,
          jsonb_object_agg(
            seo_url,
            jsonb_build_object(
              'label', label,
              'seo_url', seo_url,
              'warehouse_id', warehouse_id,
              'total_stock', total_stock,
              'available', available,
              'sizes', sizes
            )
          ) as aarhus_inventory
        from store_sizes
        group by id
      )
      update public.kaufmann_products kp
      set aarhus_inventory = inventory.aarhus_inventory
      from inventory
      where kp.id = inventory.id
    $backfill$;
  end if;
end $$;

drop index if exists public.kaufmann_products_aarhus_sizes_gin_idx;

create index if not exists kaufmann_products_aarhus_inventory_gin_idx
  on public.kaufmann_products using gin (aarhus_inventory);

alter table public.kaufmann_products
  drop column if exists aarhus_sizes,
  drop column if exists aarhus_store_stock;
