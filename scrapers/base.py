from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Union

import requests


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "da-DK,da;q=0.9,en;q=0.8",
}


@dataclass
class FetchResult:
    url: str
    status_code: Optional[int]
    html: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.html is not None and self.status_code is not None


@dataclass
class InventorySnapshot:
    current_price: Optional[float]
    sale_price: Optional[float]
    currency: str
    sizes: list[dict[str, Any]]
    stock_status: str
    is_active: bool
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def available_sizes(self) -> list[str]:
        return [s["size"] for s in self.sizes if s.get("in_stock") is True]


class BaseRefreshScraper:
    store_key = "base"

    def __init__(
        self,
        min_delay_seconds: float = 1.5,
        max_delay_seconds: float = 3.0,
        timeout_seconds: int = 20,
    ):
        self.min_delay_seconds = min_delay_seconds
        self.max_delay_seconds = max_delay_seconds
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    def polite_delay(self) -> None:
        time.sleep(random.uniform(self.min_delay_seconds, self.max_delay_seconds))

    def fetch_html(self, url: str, row: Optional[dict[str, Any]] = None) -> FetchResult:
        try:
            response = self.session.get(url, timeout=self.timeout_seconds)
            if response.status_code in {404, 410}:
                return FetchResult(url=url, status_code=response.status_code)
            response.raise_for_status()
            return FetchResult(url=url, status_code=response.status_code, html=response.text)
        except requests.RequestException as exc:
            return FetchResult(url=url, status_code=None, error=str(exc))

    def close(self) -> None:
        self.session.close()

    def refresh_product(
        self, url: str, row: Optional[dict[str, Any]] = None
    ) -> InventorySnapshot:
        result = self.fetch_html(url, row=row)
        if result.status_code in {404, 410}:
            return InventorySnapshot(
                current_price=None,
                sale_price=None,
                currency="DKK",
                sizes=[],
                stock_status="unavailable",
                is_active=False,
                raw={"http_status": result.status_code},
            )
        if not result.html:
            raise RuntimeError(result.error or f"Failed to fetch {url}")
        return self.parse_inventory(result.html, url=url, row=row)

    def parse_inventory(
        self,
        html: str,
        url: Optional[str] = None,
        row: Optional[dict[str, Any]] = None,
    ) -> InventorySnapshot:
        raise NotImplementedError


def parse_danish_price(
    value: Optional[Union[str, int, float, Decimal]]
) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None

    text = text.replace("DKK", "").replace("kr.", "").replace("kr", "")
    text = text.replace("\xa0", " ").strip()
    match = re.search(r"\d[\d.\s]*([,]\d{1,2})?", text)
    if not match:
        return None

    normalized = match.group(0).replace(".", "").replace(" ", "").replace(",", ".")
    try:
        return float(Decimal(normalized))
    except (InvalidOperation, ValueError):
        return None


def normalize_size_status(sizes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in sizes:
        size = str(item.get("size", "")).strip()
        if not size or size in seen:
            continue
        normalized.append({"size": size, "in_stock": bool(item.get("in_stock"))})
        seen.add(size)

    return normalized


def stock_status_from_sizes(sizes: list[dict[str, Any]], fallback_active: bool = True) -> str:
    if not fallback_active:
        return "unavailable"
    if not sizes:
        return "unknown"
    if any(size.get("in_stock") for size in sizes):
        return "in_stock"
    return "out_of_stock"
