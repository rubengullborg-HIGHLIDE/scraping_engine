from __future__ import annotations

import json
import re
from typing import Any, Optional, Tuple

from bs4 import BeautifulSoup

from scrapers.base import (
    BaseRefreshScraper,
    InventorySnapshot,
    normalize_size_status,
    parse_danish_price,
    stock_status_from_sizes,
)


class STValentinRefreshScraper(BaseRefreshScraper):
    store_key = "st_valentin"

    def _extract_product_group_ld_json(
        self, soup: BeautifulSoup
    ) -> Optional[dict[str, Any]]:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("@type") == "ProductGroup":
                return data
        return None

    def _extract_meta_product(self, html: str) -> Optional[dict[str, Any]]:
        match = re.search(r"var meta = (\{.*?\});\s*\n", html, re.DOTALL)
        if not match:
            return None
        try:
            meta = json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
        product = meta.get("product")
        return product if isinstance(product, dict) else None

    def _extract_sizes(
        self,
        soup: BeautifulSoup,
        product_group: Optional[dict[str, Any]],
        meta_product: Optional[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        sizes: list[dict[str, Any]] = []

        if product_group and product_group.get("hasVariant"):
            for variant in product_group["hasVariant"]:
                size_name = str(variant.get("name", "")).rsplit(" - ", 1)[-1].strip()
                availability = variant.get("offers", {}).get("availability", "")
                if size_name:
                    sizes.append({"size": size_name, "in_stock": "InStock" in availability})
            if sizes:
                return normalize_size_status(sizes)

        if meta_product and meta_product.get("variants"):
            for variant in meta_product["variants"]:
                size_name = variant.get("public_title") or variant.get("title")
                if size_name:
                    sizes.append(
                        {"size": size_name, "in_stock": bool(variant.get("available", False))}
                    )
            if sizes:
                return normalize_size_status(sizes)

        picker = soup.select_one('variant-picker[context="main_product"]') or soup.select_one(
            "variant-picker"
        )
        if picker:
            for label in picker.select("label.block-swatch"):
                size_name = label.get_text(strip=True)
                if size_name:
                    sizes.append(
                        {
                            "size": size_name,
                            "in_stock": "is-disabled" not in label.get("class", []),
                        }
                    )

        return normalize_size_status(sizes)

    def _extract_prices(
        self,
        soup: BeautifulSoup,
        product_group: Optional[dict[str, Any]],
        meta_product: Optional[dict[str, Any]],
    ) -> Tuple[Optional[float], Optional[float], str]:
        currency = "DKK"

        sale_price = parse_danish_price(
            soup.select_one("sale-price").get_text(" ", strip=True)
            if soup.select_one("sale-price")
            else None
        )
        compare_price = parse_danish_price(
            soup.select_one("compare-at-price").get_text(" ", strip=True)
            if soup.select_one("compare-at-price")
            else None
        )

        if product_group and product_group.get("hasVariant"):
            offers = product_group["hasVariant"][0].get("offers", {})
            currency = offers.get("priceCurrency") or currency
            group_price = parse_danish_price(offers.get("price"))
            if sale_price is None:
                sale_price = group_price

        if sale_price is None and meta_product and meta_product.get("variants"):
            raw_price = meta_product["variants"][0].get("price")
            sale_price = raw_price / 100 if isinstance(raw_price, int) else parse_danish_price(raw_price)

        current_price = compare_price if compare_price and sale_price and compare_price > sale_price else sale_price
        discounted_sale_price = sale_price if current_price and sale_price and sale_price < current_price else None
        return current_price, discounted_sale_price, currency

    def parse_inventory(
        self,
        html: str,
        url: Optional[str] = None,
        row: Optional[dict[str, Any]] = None,
    ) -> InventorySnapshot:
        soup = BeautifulSoup(html, "html.parser")
        product_group = self._extract_product_group_ld_json(soup)
        meta_product = self._extract_meta_product(html)

        sizes = self._extract_sizes(soup, product_group, meta_product)
        current_price, sale_price, currency = self._extract_prices(soup, product_group, meta_product)
        is_active = bool(current_price or sizes or product_group or meta_product)

        return InventorySnapshot(
            current_price=current_price,
            sale_price=sale_price,
            currency=currency,
            sizes=sizes,
            stock_status=stock_status_from_sizes(sizes, fallback_active=is_active),
            is_active=is_active,
            raw={
                "source": self.store_key,
                "url": url,
                "parser": "json_ld_meta_product",
                "variant_count": len(sizes),
            },
        )
