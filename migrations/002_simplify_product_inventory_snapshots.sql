alter table product_inventory_snapshots
  add column if not exists store text,
  add column if not exists price numeric;

update product_inventory_snapshots snapshots
set store = products.store
from products
where snapshots.product_id = products.id
  and snapshots.store is null;

update product_inventory_snapshots
set price = current_price
where price is null
  and current_price is not null;

alter table product_inventory_snapshots
  drop column if exists store_id,
  drop column if exists source_product_id,
  drop column if exists current_price,
  drop column if exists sale_price,
  drop column if exists currency,
  drop column if exists available_sizes,
  drop column if exists stock_status,
  drop column if exists is_active,
  drop column if exists raw;
