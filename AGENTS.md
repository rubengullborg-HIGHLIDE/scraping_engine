# HIGHLIDE Scraping Engine

This repository contains the Python scraping and data-ingestion jobs for HIGHLIDE, a platform for showing clothing products available in smaller local fashion stores, starting in Aarhus, Denmark.

## Current Purpose

The scraper project has two separate ingestion concerns:

- Full catalog import: initial or occasional broad product imports.
- Inventory refresh: frequent dynamic refresh of price and size availability for already-known products.

Do not mix these two paths. Full import can collect names, descriptions, images, brand, category, materials, color, fit, and broad metadata. Refresh jobs should stay narrow and deterministic: current price and size availability for existing database rows.

## Project Structure

```text
.
├── AGENTS.md
├── requirements.txt
├── migrations/
│   ├── 001_product_inventory_snapshots.sql
│   ├── 002_simplify_product_inventory_snapshots.sql
│   ├── 003_kaufmann_products.sql
│   ├── 004_kaufmann_aarhus_inventory.sql
│   └── 005_clean_aarhus_inventory_interface.sql
├── scrapers/
│   ├── base.py
│   ├── full_import/
│   │   ├── base.py
│   │   ├── kaufmann.py
│   │   └── st_valentin.py
│   └── stores/
│       ├── kaufmann.py
│       └── st_valentin.py
└── scripts/
    ├── import_kaufmann_products.py
    └── refresh_inventory.py
```

## Scraper Boundaries

### Full Import

Location: `scrapers/full_import/`

These are store-specific catalog scrapers. They are allowed to parse broad product data:

- product URL
- name
- brand
- price at import time
- images
- description
- materials, color, fit, category
- size availability if visible
- store/source info

Kaufmann full import is currently wired through `scripts/import_kaufmann_products.py`. Other full-import scrapers are still saved implementations until dedicated runners are added.

### Kaufmann Full Import

Location:

- `scripts/import_kaufmann_products.py`
- `scrapers/full_import/kaufmann.py`
- `migrations/003_kaufmann_products.sql`

Kaufmann products should be imported into the dedicated `kaufmann_products` table, not the frontend-facing `products` table.

The Kaufmann importer discovers product URLs from:

```text
https://www.kaufmann.dk/sitemap.xml
```

That sitemap points to a compressed child sitemap ending in `.xml.gz`; the importer follows and decompresses it. As of July 7, 2026 it discovers about 4,878 Kaufmann product URLs.

The importer uses Playwright to read Kaufmann's live Alpine state:

```js
Alpine.store('productStore')
```

Kaufmann color variants are keyed by `colorId`. The stable unique key for rows in `kaufmann_products` is:

```text
source_parent_id + source_color_id
```

For each color variant, the importer stores:

- product/style metadata: URL, name, brand, description, materials, fit, images, price
- top-level `source_product_number` is currently null for Kaufmann variant rows; Kaufmann exposes product numbers at size level, so keep those source refs in `webshop_sizes` or `raw`, not in the frontend-facing inventory shape
- `category` is currently null unless a future scraper revision extracts a reliable Kaufmann category/breadcrumb
- `color`: the exact Kaufmann color name, for example `SORT`, `HVID`, `NAVY`
- `color_group`: Kaufmann's grouped color family, for example `Sort`, `Hvid`, `Blå`
- `webshop_sizes`: online-shop stock, kept only as reference
- `aarhus_inventory`: clean reusable Aarhus store inventory for product detail pages
- `aarhus_total_stock`
- `aarhus_available`

`aarhus_inventory` is a store-agnostic JSON interface. Keep source-specific ids such as Kaufmann `warehouse_id`, `productNumber`, and size variant ids out of this object; put them in `raw` if they are useful for debugging or future scraper work.

```json
{
  "stores": {
    "bruuns-galleri": {
      "name": "KAUFMANN Aarhus, Bruuns Galleri",
      "stock_known": true,
      "available": true,
      "total_stock": 4,
      "sizes": {
        "S": { "available": false, "stock": 0 },
        "M": { "available": false, "stock": 0 },
        "L": { "available": true, "stock": 2 }
      }
    }
  }
}
```

Use stable ASCII slugs as store keys, for example `aarhus-c`, `bruuns-galleri`, or `storcenter-nord`. Put the display label in `name`. If a future store only exposes whether a size is available but not exact counts, use `stock_known: false` and `stock: null`.

Keep `aarhus_total_stock` and `aarhus_available` as query-friendly summary columns. Do not recreate split top-level JSON columns such as `aarhus_sizes` or `aarhus_store_stock`.

The Aarhus stores currently tracked are:

```text
bruuns-galleri    KAUFMANN Aarhus, Bruuns Galleri
storcenter-nord   KAUFMANN Aarhus, Storcenter Nord
aarhus-c          KAUFMANN Aarhus, Strøget - Regina
```

Do not treat Kaufmann's top-level `availability` as local stock. That field is webshop availability. Local store inventory is under each size option's `stock` object, keyed by store/warehouse.

Useful commands:

```bash
python scripts/import_kaufmann_products.py --discover-only --preview 10
python scripts/import_kaufmann_products.py --dry-run --limit 1 --no-delay
python scripts/import_kaufmann_products.py --limit 100
python scripts/import_kaufmann_products.py --offset 100 --limit 100
```

Run large imports in batches with polite delays. A full Kaufmann import can involve thousands of product pages and multiple color variants per page.

### Inventory Refresh

Location:

- `scripts/refresh_inventory.py`
- `scrapers/base.py`
- `scrapers/stores/`

This path reads existing product rows from Supabase and updates only dynamic fields. It must not create duplicate product rows.

The stable update key is the database product row `id`, because the job first reads products and then patches the same row. For long-term product identity across tables, prefer `store + source_variant_id` or `store + normalized variant URL` once full import captures those fields.

## Current Database Assumptions

The current production shape is a single `products` table with fields similar to:

- `id`
- `url`
- `navn`
- `pris`
- `brand`
- `description`
- `materials`
- `color`
- `fit`
- `sizes`
- `images`
- `store`
- `category`

The refresh job is configured through environment variables so it can map to this Danish/current schema.

There is also a dedicated Kaufmann import table:

```text
kaufmann_products
├── source_parent_id
├── source_color_id
├── source_url
├── canonical_url
├── source_product_number
├── name
├── brand
├── color
├── color_group
├── current_price
├── list_price
├── description
├── materials
├── fit
├── category
├── images
├── webshop_sizes
├── aarhus_inventory
├── aarhus_total_stock
├── aarhus_available
└── raw
```

`kaufmann_products` has RLS enabled. Do not add broad public policies without checking frontend access requirements.

Recommended current `.env` mappings:

```bash
SUPABASE_PRODUCTS_TABLE=products
SUPABASE_PRODUCT_ID_COLUMN=id
SUPABASE_PRODUCT_URL_COLUMN=url
SUPABASE_ACTIVE_COLUMN=skip
SUPABASE_CURRENT_PRICE_COLUMN=pris
SUPABASE_SIZE_STATUS_COLUMN=sizes
SUPABASE_JSON_TEXT_COLUMNS=sizes
SUPABASE_INVENTORY_HISTORY_TABLE=product_inventory_snapshots
SUPABASE_HISTORY_BUCKET_MINUTES=120
```

Do not commit `.env`. Use `.env.example` for non-secret examples.

## Supabase Credentials

Use the Supabase project URL and the server-side secret key:

```bash
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_SECRET_KEY=your-sb-secret-key
```

Do not use the publishable key for cron/server refresh jobs. Do not expose the secret key to frontend code.

## History Table

The current history table is intentionally simple:

```text
product_inventory_snapshots
├── product_id
├── store
├── product_url
├── checked_at
├── checked_bucket
├── price
└── sizes
```

`checked_at` is stored in UTC. Denmark time should be handled in the frontend or reporting layer.

The refresh job upserts one row per product per `checked_bucket`, currently defaulting to 120 minutes. This makes cron retries idempotent inside the same two-hour window.

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Dry run:

```bash
python scripts/refresh_inventory.py --dry-run --limit 3 --no-delay
```

Limited write test:

```bash
python scripts/refresh_inventory.py --limit 3 --no-delay
```

Production-style run:

```bash
python scripts/refresh_inventory.py
```

## Cron Deployment

Recommended DigitalOcean cron entry:

```cron
0 */2 * * * cd /opt/highlide/scraping_engine && .venv/bin/python scripts/refresh_inventory.py >> logs/refresh_inventory.log 2>&1
```

Make sure the server has:

- Python virtualenv installed
- `requirements.txt` installed
- Playwright Chromium installed
- `.env` present with Supabase secret credentials
- `logs/` directory created

## Known Limitation: Color Variants

Many fashion store pages represent one clothing style with multiple color variants. Each color can have different size availability.

The current `products` table stores product rows with a `color` field, but not a stable source variant id. Kaufmann refresh currently tries to select the matching color using the row color text before extracting sizes. This is a heuristic and can be wrong when the store page exposes only family-level product data or image-only color swatches.

For Kaufmann, the full-import path now solves this by importing one row per color variant into `kaufmann_products`, using Kaufmann's `colorId` as `source_color_id`. Prefer this table for Kaufmann catalog experiments and local Aarhus inventory modeling.

The better future model is:

```text
products
  shared style/catalog fields

product_variants
  product_id
  store
  color
  source_variant_id
  variant_url
  images
  current_price

variant_inventory_snapshots
  variant_id
  checked_at
  checked_bucket
  price
  sizes
```

Until then, treat size availability on multi-color products as best-effort.

## Engineering Notes

- Keep refresh deterministic. Do not use AI for price or stock refresh.
- Prefer explicit store-specific selectors over a generic parser until there are enough stores to justify abstraction.
- Keep delays polite. Small local stores should not be hit aggressively.
- Store raw/debug data only when actively debugging; keep long-term history narrow.
- Use UTC timestamps in the database.
- Avoid schema churn in the frontend-facing `products` table unless the frontend is updated with it.
