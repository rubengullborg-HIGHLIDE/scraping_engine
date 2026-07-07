from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOG = logging.getLogger("import_kaufmann_products")


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


class SupabaseCatalogClient:
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

    def upsert_products(self, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return

        response = self.session.post(
            self._table_url(table),
            params={"on_conflict": "source_parent_id,source_color_id"},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            data=json.dumps(rows, ensure_ascii=False),
        )
        response.raise_for_status()


def product_urls_from_args(scraper: Any, args: argparse.Namespace) -> list[str]:
    urls: set[str] = set()

    for url in args.url or []:
        urls.add(scraper._clean_product_url(url))

    for catalog_url in args.catalog_url or []:
        html = scraper.fetch_html(catalog_url)
        if not html:
            continue
        urls.update(scraper.get_product_links(html))

    if not urls:
        urls.update(scraper.discover_product_links_from_sitemap(args.sitemap_url))

    ordered = sorted(urls)
    if args.offset:
        ordered = ordered[args.offset :]
    if args.limit:
        return ordered[: args.limit]
    return ordered


def import_kaufmann_products(args: argparse.Namespace) -> int:
    from scrapers.full_import.kaufmann import KaufmanScraper

    load_dotenv(ROOT / ".env")
    table = args.table or env("KAUFMANN_PRODUCTS_TABLE", "kaufmann_products")
    supabase_url = env("SUPABASE_URL")
    supabase_key = env("SUPABASE_SECRET_KEY") or env("SUPABASE_SERVICE_ROLE_KEY")

    if not args.dry_run and (not supabase_url or not supabase_key):
        raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY are required for writes.")

    client = SupabaseCatalogClient(supabase_url, supabase_key) if not args.dry_run else None
    scraper = KaufmanScraper()

    imported_rows = 0
    failed = 0

    try:
        product_urls = product_urls_from_args(scraper, args)
        LOG.info("Discovered %s Kaufmann product URLs.", len(product_urls))
        if args.discover_only:
            for product_url in product_urls[: args.preview]:
                LOG.info("Discovered URL: %s", product_url)
            return 0

        for index, product_url in enumerate(product_urls, start=1):
            if index > 1 and not args.no_delay:
                time.sleep(random.uniform(args.min_delay, args.max_delay))

            try:
                LOG.info("[%s/%s] Importing %s", index, len(product_urls), product_url)
                rows = scraper.parse_product_variants_with_js(product_url)
                if args.dry_run:
                    LOG.info("Dry run rows for %s:\n%s", product_url, json.dumps(rows, ensure_ascii=False, indent=2))
                else:
                    assert client is not None
                    client.upsert_products(table, rows)
                imported_rows += len(rows)
            except Exception:
                failed += 1
                LOG.exception("Failed to import %s", product_url)

    finally:
        scraper.close()

    LOG.info("Kaufmann import complete. rows=%s failed_products=%s", imported_rows, failed)
    return 1 if failed else 0


def parse_args() -> argparse.Namespace:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(
        description="Full-import Kaufmann product color variants into a dedicated Supabase table."
    )
    parser.add_argument("--table", default=env("KAUFMANN_PRODUCTS_TABLE", "kaufmann_products"))
    parser.add_argument("--url", action="append", help="Import a specific Kaufmann product URL.")
    parser.add_argument("--catalog-url", action="append", help="Discover product URLs from a listing page.")
    parser.add_argument(
        "--sitemap-url",
        default=env("KAUFMANN_SITEMAP_URL", "https://www.kaufmann.dk/sitemap.xml"),
    )
    parser.add_argument("--limit", type=int, help="Limit product pages for testing.")
    parser.add_argument("--offset", type=int, default=0, help="Skip this many discovered product URLs.")
    parser.add_argument("--discover-only", action="store_true", help="Only discover product URLs.")
    parser.add_argument("--preview", type=int, default=10, help="Number of discovered URLs to log.")
    parser.add_argument("--dry-run", action="store_true", help="Scrape without writing to Supabase.")
    parser.add_argument("--no-delay", action="store_true", help="Disable polite delay for local tests.")
    parser.add_argument("--min-delay", type=float, default=float(env("FULL_IMPORT_MIN_DELAY", "1.5")))
    parser.add_argument("--max-delay", type=float, default=float(env("FULL_IMPORT_MAX_DELAY", "3.0")))
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
    raise SystemExit(import_kaufmann_products(arguments))
