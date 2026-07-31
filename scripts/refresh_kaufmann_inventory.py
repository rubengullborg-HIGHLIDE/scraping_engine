from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOG = logging.getLogger("refresh_kaufmann_inventory")

DYNAMIC_PRODUCT_COLUMNS = (
    "current_price",
    "list_price",
    "webshop_sizes",
    "aarhus_inventory",
    "aarhus_total_stock",
    "aarhus_available",
    "scraped_at",
    "updated_at",
)

SNAPSHOT_COLUMNS = (
    "kaufmann_product_id",
    "source_parent_id",
    "source_color_id",
    "canonical_url",
    "source_url",
    "checked_at",
    "checked_bucket",
    "refresh_status",
    "current_price",
    "list_price",
    "webshop_sizes",
    "aarhus_inventory",
    "aarhus_total_stock",
    "aarhus_available",
    "updated_at",
)

EMPTY_AARHUS_INVENTORY = {"stores": {}}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


class SupabaseKaufmannRefreshClient:
    def __init__(self, supabase_url: str, supabase_key: str):
        try:
            import requests
        except ModuleNotFoundError as exc:
            raise RuntimeError("Install dependencies first: python3 -m pip install -r requirements.txt") from exc

        self.supabase_url = supabase_url.rstrip("/")
        self.session = requests.Session()
        headers = {
            "apikey": supabase_key,
            "Content-Type": "application/json",
        }
        if supabase_key.count(".") == 2:
            headers["Authorization"] = f"Bearer {supabase_key}"
        self.session.headers.update(headers)

    def _table_url(self, table: str) -> str:
        return f"{self.supabase_url}/rest/v1/{quote(table)}"

    def list_existing_variants(
        self,
        table: str,
        page_size: int = 1000,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        start = 0
        select = ",".join(
            (
                "id",
                "source_parent_id",
                "source_color_id",
                "source_url",
                "canonical_url",
                "updated_at",
            )
        )

        while True:
            end = start + page_size - 1
            response = self.session.get(
                self._table_url(table),
                params={"select": select, "order": "updated_at.asc,id.asc"},
                headers={"Range": f"{start}-{end}"},
            )
            response.raise_for_status()
            batch = response.json()
            rows.extend(batch)
            if len(batch) < page_size:
                return rows
            start += page_size

    def upsert_products_by_id(self, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        response = self.session.post(
            self._table_url(table),
            params={"on_conflict": "id"},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            data=json.dumps(rows, ensure_ascii=False),
        )
        response.raise_for_status()

    def upsert_snapshots(self, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        response = self.session.post(
            self._table_url(table),
            params={"on_conflict": "kaufmann_product_id,checked_bucket"},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            data=json.dumps(rows, ensure_ascii=False),
        )
        response.raise_for_status()


def product_pages_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    pages: "OrderedDict[str, dict[str, str]]" = OrderedDict()
    for row in rows:
        canonical_url = row.get("canonical_url")
        if not canonical_url:
            continue
        pages.setdefault(
            canonical_url,
            {
                "canonical_url": canonical_url,
                "source_parent_id": row.get("source_parent_id") or "",
            },
        )
    return list(pages.values())


def rows_by_canonical_url(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        canonical_url = row.get("canonical_url")
        if not canonical_url:
            continue
        grouped.setdefault(canonical_url, []).append(row)
    return grouped


def dynamic_product_payload(scraped_row: dict[str, Any]) -> dict[str, Any]:
    return {column: scraped_row[column] for column in DYNAMIC_PRODUCT_COLUMNS if column in scraped_row}


def dynamic_product_upsert_row(product_id: Any, scraped_row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": product_id,
        **dynamic_product_payload(scraped_row),
    }


def snapshot_payload(
    product_row: dict[str, Any],
    scraped_row: dict[str, Any],
    checked_at: str,
    refresh_status: str = "ok",
) -> dict[str, Any]:
    payload = {
        "kaufmann_product_id": product_row["id"],
        "source_parent_id": product_row["source_parent_id"],
        "source_color_id": product_row["source_color_id"],
        "canonical_url": product_row.get("canonical_url"),
        "source_url": product_row.get("source_url"),
        "checked_at": checked_at,
        "checked_bucket": checked_at[:10],
        "refresh_status": refresh_status,
        "updated_at": checked_at,
        "current_price": scraped_row.get("current_price"),
        "list_price": scraped_row.get("list_price"),
        "webshop_sizes": scraped_row.get("webshop_sizes") or [],
        "aarhus_inventory": scraped_row.get("aarhus_inventory") or EMPTY_AARHUS_INVENTORY,
        "aarhus_total_stock": scraped_row.get("aarhus_total_stock") or 0,
        "aarhus_available": bool(scraped_row.get("aarhus_available")),
    }
    return {column: payload[column] for column in SNAPSHOT_COLUMNS}


def unavailable_product_payload(checked_at: str, canonical_url: Optional[str]) -> dict[str, Any]:
    return {
        "current_price": None,
        "list_price": None,
        "webshop_sizes": [],
        "aarhus_inventory": EMPTY_AARHUS_INVENTORY,
        "aarhus_total_stock": 0,
        "aarhus_available": False,
        "scraped_at": checked_at,
        "updated_at": checked_at,
    }


def unavailable_product_upsert_row(
    product_id: Any,
    checked_at: str,
    canonical_url: Optional[str],
) -> dict[str, Any]:
    return {
        "id": product_id,
        **unavailable_product_payload(checked_at, canonical_url),
    }


def unavailable_snapshot_payload(
    product_row: dict[str, Any],
    checked_at: str,
) -> dict[str, Any]:
    return snapshot_payload(
        product_row,
        {
            "current_price": None,
            "list_price": None,
            "webshop_sizes": [],
            "aarhus_inventory": EMPTY_AARHUS_INVENTORY,
            "aarhus_total_stock": 0,
            "aarhus_available": False,
        },
        checked_at,
        refresh_status="page_unavailable",
    )


def refresh_kaufmann_inventory(args: argparse.Namespace) -> int:
    from scrapers.full_import.kaufmann import KaufmanScraper

    load_dotenv(ROOT / ".env")
    products_table = args.products_table or env("KAUFMANN_PRODUCTS_TABLE", "kaufmann_products")
    snapshots_table = args.snapshots_table or env(
        "KAUFMANN_INVENTORY_SNAPSHOTS_TABLE",
        "kaufmann_inventory_snapshots",
    )
    supabase_url = env("SUPABASE_URL")
    supabase_key = env("SUPABASE_SECRET_KEY") or env("SUPABASE_SERVICE_ROLE_KEY")

    if not args.dry_run and (not supabase_url or not supabase_key):
        raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY are required for writes.")

    client = (
        SupabaseKaufmannRefreshClient(supabase_url, supabase_key)
        if supabase_url and supabase_key
        else None
    )
    scraper = KaufmanScraper()

    refreshed_variants = 0
    unavailable_variants = 0
    skipped_new_variants = 0
    failed_pages = 0

    try:
        if args.url:
            if client is None:
                existing_rows = []
                pages = [{"canonical_url": scraper._clean_product_url(url), "source_parent_id": ""} for url in args.url]
            else:
                existing_rows = client.list_existing_variants(products_table)
                requested_urls = {scraper._clean_product_url(url) for url in args.url}
                pages = [page for page in product_pages_from_rows(existing_rows) if page["canonical_url"] in requested_urls]
                if args.dry_run:
                    matched_urls = {page["canonical_url"] for page in pages}
                    pages.extend(
                        {"canonical_url": url, "source_parent_id": ""}
                        for url in sorted(requested_urls - matched_urls)
                    )
        else:
            if client is None:
                raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY are required unless --dry-run uses --url.")
            existing_rows = client.list_existing_variants(products_table)
            pages = product_pages_from_rows(existing_rows)

        if args.offset:
            pages = pages[args.offset :]
        if args.limit:
            pages = pages[: args.limit]

        rows_by_variant = {
            (row["source_parent_id"], row["source_color_id"]): row
            for row in existing_rows
            if row.get("source_parent_id") and row.get("source_color_id")
        }
        page_rows = rows_by_canonical_url(existing_rows)

        LOG.info("Loaded %s Kaufmann product pages for refresh.", len(pages))

        for index, page in enumerate(pages, start=1):
            canonical_url = page["canonical_url"]
            if index > 1 and not args.no_delay:
                time.sleep(random.uniform(args.min_delay, args.max_delay))

            checked_at = datetime.now(timezone.utc).isoformat()
            try:
                LOG.info("[%s/%s] Refreshing %s", index, len(pages), canonical_url)
                scraped_rows = scraper.parse_product_variants_with_js(
                    canonical_url,
                    allow_unavailable=True,
                )

                if not scraped_rows:
                    product_rows = page_rows.get(canonical_url, [])
                    LOG.warning(
                        "No variants found for %s. Marking %s existing variants unavailable.",
                        canonical_url,
                        len(product_rows),
                    )
                    for product_row in product_rows:
                        if args.dry_run:
                            LOG.info(
                                "Dry run unavailable update for id=%s: %s",
                                product_row["id"],
                                json.dumps(unavailable_product_payload(checked_at, canonical_url)),
                            )
                        unavailable_variants += 1
                    if product_rows and not args.dry_run:
                        assert client is not None
                        client.upsert_products_by_id(
                            products_table,
                            [
                                unavailable_product_upsert_row(
                                    product_row["id"],
                                    checked_at,
                                    canonical_url,
                                )
                                for product_row in product_rows
                            ],
                        )
                        client.upsert_snapshots(
                            snapshots_table,
                            [
                                unavailable_snapshot_payload(product_row, checked_at)
                                for product_row in product_rows
                            ],
                        )
                    continue

                product_updates = []
                snapshots = []
                for scraped_row in scraped_rows:
                    source_parent_id = scraped_row.get("source_parent_id")
                    source_color_id = scraped_row.get("source_color_id")
                    product_row = rows_by_variant.get((source_parent_id, source_color_id))
                    if not product_row:
                        if args.dry_run and client is None:
                            LOG.info(
                                "Dry run scraped unmatched variant: %s",
                                json.dumps(dynamic_product_payload(scraped_row), ensure_ascii=False),
                            )
                            refreshed_variants += 1
                            continue
                        skipped_new_variants += 1
                        LOG.warning(
                            "Skipping new Kaufmann variant not already in table: parent=%s color=%s url=%s",
                            source_parent_id,
                            source_color_id,
                            scraped_row.get("source_url"),
                        )
                        continue

                    if args.dry_run:
                        LOG.info(
                            "Dry run dynamic update for id=%s: %s",
                            product_row["id"],
                            json.dumps(dynamic_product_payload(scraped_row), ensure_ascii=False),
                        )
                    else:
                        product_updates.append(
                            dynamic_product_upsert_row(product_row["id"], scraped_row)
                        )
                        snapshots.append(snapshot_payload(product_row, scraped_row, checked_at))
                    refreshed_variants += 1

                if product_updates:
                    assert client is not None
                    client.upsert_products_by_id(products_table, product_updates)
                    client.upsert_snapshots(snapshots_table, snapshots)

            except Exception:
                failed_pages += 1
                LOG.exception("Failed to refresh Kaufmann product page %s", canonical_url)

    finally:
        scraper.close()

    LOG.info(
        "Kaufmann refresh complete. refreshed_variants=%s unavailable_variants=%s "
        "skipped_new_variants=%s failed_pages=%s",
        refreshed_variants,
        unavailable_variants,
        skipped_new_variants,
        failed_pages,
    )
    return 1 if failed_pages else 0


def parse_args() -> argparse.Namespace:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(
        description="Refresh dynamic Kaufmann price and Aarhus inventory fields."
    )
    parser.add_argument("--products-table", default=env("KAUFMANN_PRODUCTS_TABLE", "kaufmann_products"))
    parser.add_argument(
        "--snapshots-table",
        default=env("KAUFMANN_INVENTORY_SNAPSHOTS_TABLE", "kaufmann_inventory_snapshots"),
    )
    parser.add_argument("--url", action="append", help="Refresh a specific Kaufmann product URL.")
    parser.add_argument("--limit", type=int, help="Limit product pages for testing.")
    parser.add_argument("--offset", type=int, default=0, help="Skip this many product pages.")
    parser.add_argument("--dry-run", action="store_true", help="Scrape without writing to Supabase.")
    parser.add_argument("--no-delay", action="store_true", help="Disable polite delay for local tests.")
    parser.add_argument("--min-delay", type=float, default=float(env("KAUFMANN_REFRESH_MIN_DELAY", "1.5")))
    parser.add_argument("--max-delay", type=float, default=float(env("KAUFMANN_REFRESH_MAX_DELAY", "3.0")))
    parser.add_argument(
        "--log-level",
        default=env("LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    logging.basicConfig(
        level=getattr(logging, arguments.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    raise SystemExit(refresh_kaufmann_inventory(arguments))
