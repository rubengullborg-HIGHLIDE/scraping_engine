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
│   └── 002_simplify_product_inventory_snapshots.sql
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

These files are not currently wired into a single import runner. Treat them as the saved full-import implementation until a dedicated `scripts/full_import.py` is added.

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
