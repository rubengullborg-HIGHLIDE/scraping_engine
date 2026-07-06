from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOG = logging.getLogger("refresh_inventory")


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


def env_column(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    if value.lower() in {"", "none", "null", "skip", "false"}:
        return ""
    return value


@dataclass(frozen=True)
class RefreshConfig:
    supabase_url: str
    supabase_key: str
    supabase_key_type: str
    products_table: str = "products"
    id_column: str = "id"
    url_column: str = "product_url"
    active_column: str = "is_active"
    store_filter_column: str = "store"
    checked_at_column: str = "last_inventory_checked_at"
    current_price_column: str = "price"
    sale_price_column: str = "sale_price"
    currency_column: str = "currency"
    available_sizes_column: str = "available_sizes"
    size_status_column: str = "sizes"
    stock_status_column: str = "stock_status"
    refresh_error_column: str = "last_refresh_error"
    raw_refresh_column: str = "last_refresh_raw"
    history_table: Optional[str] = None
    price_history_table: Optional[str] = None
    error_table: Optional[str] = None
    json_text_columns: tuple[str, ...] = ()
    history_bucket_minutes: int = 120

    @classmethod
    def from_env(cls) -> "RefreshConfig":
        supabase_url = env("SUPABASE_URL")
        supabase_key = env("SUPABASE_SECRET_KEY") or env("SUPABASE_SERVICE_ROLE_KEY")
        if not supabase_url or not supabase_key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SECRET_KEY are required. "
                "SUPABASE_SERVICE_ROLE_KEY is also supported for legacy projects."
            )

        return cls(
            supabase_url=supabase_url.rstrip("/"),
            supabase_key=supabase_key,
            supabase_key_type="legacy_jwt" if supabase_key.count(".") == 2 else "secret",
            products_table=env("SUPABASE_PRODUCTS_TABLE", "products"),
            id_column=env("SUPABASE_PRODUCT_ID_COLUMN", "id"),
            url_column=env("SUPABASE_PRODUCT_URL_COLUMN", "product_url"),
            active_column=env_column("SUPABASE_ACTIVE_COLUMN", "is_active"),
            store_filter_column=env("SUPABASE_STORE_FILTER_COLUMN", "store"),
            checked_at_column=env_column(
                "SUPABASE_CHECKED_AT_COLUMN", "last_inventory_checked_at"
            ),
            current_price_column=env_column("SUPABASE_CURRENT_PRICE_COLUMN", "price"),
            sale_price_column=env_column("SUPABASE_SALE_PRICE_COLUMN", "sale_price"),
            currency_column=env_column("SUPABASE_CURRENCY_COLUMN", "currency"),
            available_sizes_column=env_column(
                "SUPABASE_AVAILABLE_SIZES_COLUMN", "available_sizes"
            ),
            size_status_column=env_column("SUPABASE_SIZE_STATUS_COLUMN", "sizes"),
            stock_status_column=env_column("SUPABASE_STOCK_STATUS_COLUMN", "stock_status"),
            refresh_error_column=env_column(
                "SUPABASE_REFRESH_ERROR_COLUMN", "last_refresh_error"
            ),
            raw_refresh_column=env_column("SUPABASE_RAW_REFRESH_COLUMN", "last_refresh_raw"),
            history_table=env("SUPABASE_INVENTORY_HISTORY_TABLE"),
            price_history_table=env("SUPABASE_PRICE_HISTORY_TABLE"),
            error_table=env("SUPABASE_REFRESH_ERROR_TABLE"),
            json_text_columns=tuple(
                column.strip()
                for column in (env("SUPABASE_JSON_TEXT_COLUMNS", "") or "").split(",")
                if column.strip()
            ),
            history_bucket_minutes=int(env("SUPABASE_HISTORY_BUCKET_MINUTES", "120")),
        )


class SupabaseRestClient:
    def __init__(self, config: RefreshConfig):
        try:
            import requests
        except ModuleNotFoundError as exc:
            raise RuntimeError("Install dependencies first: python3 -m pip install -r requirements.txt") from exc

        self.config = config
        self.session = requests.Session()
        headers = {
            "apikey": config.supabase_key,
            "Content-Type": "application/json",
        }
        if config.supabase_key_type == "legacy_jwt":
            headers["Authorization"] = f"Bearer {config.supabase_key}"
        self.session.headers.update(headers)

    def _table_url(self, table: str) -> str:
        return f"{self.config.supabase_url}/rest/v1/{quote(table)}"

    def list_active_products(
        self,
        limit: Optional[int] = None,
        store: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        params = {"select": "*", "order": f"{self.config.id_column}.asc"}
        if self.config.active_column:
            params[self.config.active_column] = "eq.true"
        if store:
            params[self.config.store_filter_column] = f"ilike.*{store}*"

        headers = {}
        if limit:
            headers["Range"] = f"0-{limit - 1}"

        response = self.session.get(
            self._table_url(self.config.products_table), params=params, headers=headers
        )
        if response.status_code == 400 and self.config.active_column:
            LOG.warning(
                "Active-column filter failed. Retrying without %s filter.",
                self.config.active_column,
            )
            params.pop(self.config.active_column, None)
            response = self.session.get(
                self._table_url(self.config.products_table), params=params, headers=headers
            )

        response.raise_for_status()
        rows = response.json()
        return [row for row in rows if row.get(self.config.url_column)]

    def update_product(self, product_id: Any, payload: dict[str, Any]) -> None:
        payload = self._encode_json_text_columns(payload)
        response = self.session.patch(
            self._table_url(self.config.products_table),
            params={self.config.id_column: f"eq.{product_id}"},
            headers={"Prefer": "return=minimal"},
            data=json.dumps(payload),
        )
        response.raise_for_status()

    def _encode_json_text_columns(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.config.json_text_columns:
            return payload

        encoded = dict(payload)
        for column in self.config.json_text_columns:
            if column in encoded and isinstance(encoded[column], (dict, list)):
                encoded[column] = json.dumps(encoded[column], ensure_ascii=False)
        return encoded

    def insert_optional(self, table: Optional[str], payload: dict[str, Any]) -> None:
        if not table:
            return
        response = self.session.post(
            self._table_url(table),
            headers={"Prefer": "return=minimal"},
            data=json.dumps(payload),
        )
        response.raise_for_status()

    def upsert_optional(
        self,
        table: Optional[str],
        payload: dict[str, Any],
        on_conflict: str,
    ) -> None:
        if not table:
            return
        response = self.session.post(
            self._table_url(table),
            params={"on_conflict": on_conflict},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            data=json.dumps(payload),
        )
        response.raise_for_status()


def build_update_payload(config: RefreshConfig, snapshot: Any) -> dict[str, Any]:
    sizes_reliable = snapshot.raw.get("sizes_reliable") is not False
    field_map = {
        config.checked_at_column: snapshot.checked_at,
        config.current_price_column: snapshot.current_price,
        config.sale_price_column: snapshot.sale_price,
        config.currency_column: snapshot.currency,
        config.available_sizes_column: snapshot.available_sizes,
        config.size_status_column: snapshot.sizes,
        config.stock_status_column: snapshot.stock_status,
        config.active_column: snapshot.is_active,
        config.refresh_error_column: None,
        config.raw_refresh_column: snapshot.raw,
    }
    payload = {key: value for key, value in field_map.items() if key}
    if not sizes_reliable:
        payload.pop(config.available_sizes_column, None)
        payload.pop(config.size_status_column, None)
    return payload


def build_history_payload(
    config: RefreshConfig,
    row: dict[str, Any],
    snapshot: Any,
) -> dict[str, Any]:
    checked_bucket = history_bucket(snapshot.checked_at, config.history_bucket_minutes)
    payload = {
        "product_id": row[config.id_column],
        "store": row.get("store"),
        "product_url": row.get(config.url_column),
        "checked_at": snapshot.checked_at,
        "checked_bucket": checked_bucket,
        "price": snapshot.current_price,
        "updated_at": snapshot.checked_at,
    }
    if snapshot.raw.get("sizes_reliable") is not False:
        payload["sizes"] = snapshot.sizes
    return payload


def should_write_inventory_history(snapshot: Any) -> bool:
    return snapshot.raw.get("sizes_reliable") is not False


def history_bucket(checked_at: str, bucket_minutes: int) -> str:
    checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    checked = checked.astimezone(timezone.utc)
    bucket_seconds = bucket_minutes * 60
    timestamp = int(checked.timestamp())
    bucket_timestamp = timestamp - (timestamp % bucket_seconds)
    return datetime.fromtimestamp(bucket_timestamp, tz=timezone.utc).isoformat()


def build_price_history_payload(
    config: RefreshConfig,
    row: dict[str, Any],
    snapshot: Any,
) -> dict[str, Any]:
    return {
        "product_id": row[config.id_column],
        "store_id": row.get("store_id"),
        "source_product_id": row.get("source_product_id"),
        "product_url": row.get(config.url_column),
        "checked_at": snapshot.checked_at,
        "current_price": snapshot.current_price,
        "sale_price": snapshot.sale_price,
        "currency": snapshot.currency,
    }


def build_error_update_payload(config: RefreshConfig, message: str) -> dict[str, Any]:
    payload = {}
    if config.refresh_error_column:
        payload[config.refresh_error_column] = message[:1000]
    if config.checked_at_column:
        payload[config.checked_at_column] = datetime.now(timezone.utc).isoformat()
    return payload


def refresh_inventory(args: argparse.Namespace) -> int:
    try:
        from scrapers.stores import scraper_for_product
    except ModuleNotFoundError as exc:
        raise RuntimeError("Install dependencies first: python3 -m pip install -r requirements.txt") from exc

    load_dotenv(ROOT / ".env")
    config = RefreshConfig.from_env()
    client = SupabaseRestClient(config)

    rows = client.list_active_products(limit=args.limit, store=args.store)
    LOG.info("Loaded %s active product rows.", len(rows))

    updated = 0
    failed = 0
    scraper_cache: dict[str, Any] = {}

    try:
        for index, row in enumerate(rows, start=1):
            product_id = row.get(config.id_column)
            product_url = row.get(config.url_column)

            try:
                scraper = scraper_for_product(
                    {
                        **row,
                        "product_url": product_url,
                    },
                    min_delay_seconds=args.min_delay,
                    max_delay_seconds=args.max_delay,
                )
                cache_key = scraper.store_key
                if cache_key not in scraper_cache:
                    scraper_cache[cache_key] = scraper
                else:
                    scraper.close()
                    scraper = scraper_cache[cache_key]

                if index > 1 and not args.no_delay:
                    scraper.polite_delay()

                LOG.info("[%s/%s] Refreshing %s", index, len(rows), product_url)
                snapshot = scraper.refresh_product(product_url, row=row)
                payload = build_update_payload(config, snapshot)

                if args.dry_run:
                    LOG.info("Dry run update for %s: %s", product_id, json.dumps(payload))
                else:
                    client.update_product(product_id, payload)
                    if should_write_inventory_history(snapshot):
                        client.upsert_optional(
                            config.history_table,
                            build_history_payload(config, row, snapshot),
                            on_conflict="product_id,checked_bucket",
                        )
                    elif config.history_table:
                        LOG.warning(
                            "Skipping inventory history for product id=%s because sizes were not reliable.",
                            product_id,
                        )
                    client.insert_optional(
                        config.price_history_table,
                        build_price_history_payload(config, row, snapshot),
                    )
                updated += 1

            except Exception as exc:
                failed += 1
                message = f"{type(exc).__name__}: {exc}"
                LOG.exception("Failed to refresh product id=%s url=%s", product_id, product_url)

                if not args.dry_run and product_id is not None:
                    error_payload = build_error_update_payload(config, message)
                    if error_payload:
                        client.update_product(product_id, error_payload)
                    client.insert_optional(
                        config.error_table,
                        {
                            "product_id": product_id,
                            "product_url": product_url,
                            "error": message[:2000],
                        },
                    )
    finally:
        for scraper in scraper_cache.values():
            scraper.close()

    LOG.info("Refresh complete. updated=%s failed=%s", updated, failed)
    return 1 if failed else 0


def parse_args() -> argparse.Namespace:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Refresh dynamic inventory fields in Supabase.")
    parser.add_argument("--limit", type=int, help="Limit rows for local testing.")
    parser.add_argument("--store", help="Optional store-name filter, for example Kaufmann.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and parse without DB writes.")
    parser.add_argument("--no-delay", action="store_true", help="Disable polite delay for local tests.")
    parser.add_argument("--min-delay", type=float, default=float(env("REFRESH_MIN_DELAY", "1.5")))
    parser.add_argument("--max-delay", type=float, default=float(env("REFRESH_MAX_DELAY", "3.0")))
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
    raise SystemExit(refresh_inventory(arguments))
