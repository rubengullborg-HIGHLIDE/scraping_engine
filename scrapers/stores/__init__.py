from __future__ import annotations

from urllib.parse import urlparse

from scrapers.base import BaseRefreshScraper
from scrapers.stores.kaufmann import KaufmannRefreshScraper
from scrapers.stores.st_valentin import STValentinRefreshScraper


def scraper_for_product(row: dict, **kwargs) -> BaseRefreshScraper:
    url = row.get("product_url") or row.get("url") or ""
    store = str(row.get("store") or row.get("store_name") or "").lower()
    host = urlparse(url).netloc.lower()

    if "kaufmann" in host or "kaufmann" in store:
        return KaufmannRefreshScraper(**kwargs)
    if "stvalentin" in host or "s.t. valentin" in store or "st valentin" in store:
        kwargs.pop("use_playwright", None)
        return STValentinRefreshScraper(**kwargs)

    raise ValueError(f"No refresh scraper registered for store/url: {store or url}")
