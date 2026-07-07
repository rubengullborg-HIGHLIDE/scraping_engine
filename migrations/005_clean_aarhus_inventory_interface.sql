alter table public.kaufmann_products
  alter column aarhus_inventory set default '{"stores": {}}'::jsonb;

update public.kaufmann_products kp
set aarhus_inventory = jsonb_build_object(
  'stores',
  coalesce(
    (
      select jsonb_object_agg(
        store_key,
        jsonb_build_object(
          'name', coalesce(store_value->>'label', store_value->>'name', store_key),
          'stock_known', true,
          'available', coalesce((store_value->>'available')::boolean, false),
          'total_stock', coalesce((store_value->>'total_stock')::integer, 0),
          'sizes', coalesce(
            (
              select jsonb_object_agg(
                size_item->>'size',
                jsonb_build_object(
                  'available', coalesce((size_item->>'available')::boolean, false),
                  'stock', coalesce((size_item->>'stock')::integer, 0)
                )
              )
              from jsonb_array_elements(coalesce(store_value->'sizes', '[]'::jsonb)) as size_item
              where size_item ? 'size'
            ),
            '{}'::jsonb
          )
        )
      )
      from jsonb_each(kp.aarhus_inventory) as store(store_key, store_value)
      where store_key <> 'stores'
    ),
    '{}'::jsonb
  )
)
where kp.aarhus_inventory <> '{}'::jsonb
  and not (kp.aarhus_inventory ? 'stores');

update public.kaufmann_products
set aarhus_inventory = '{"stores": {}}'::jsonb
where aarhus_inventory = '{}'::jsonb;
