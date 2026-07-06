from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Optional, Tuple, Union

from bs4 import BeautifulSoup

from scrapers.base import (
    BaseRefreshScraper,
    FetchResult,
    InventorySnapshot,
    normalize_size_status,
    parse_danish_price,
    stock_status_from_sizes,
)


LOG = logging.getLogger(__name__)


class KaufmannRefreshScraper(BaseRefreshScraper):
    store_key = "kaufmann"

    def __init__(self, *args, use_playwright: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.use_playwright = use_playwright
        self._playwright = None
        self._browser = None
        self._last_selected_color = None
        self._last_rendered_with_playwright = False
        self._last_render_error = None

    def fetch_html(self, url: str, row: Optional[dict[str, Any]] = None) -> FetchResult:
        if self.use_playwright:
            rendered = self._fetch_html_with_playwright(url, row=row)
            if rendered.html or rendered.status_code in {404, 410}:
                return rendered
            LOG.warning(
                "Kaufmann Playwright render failed for %s; falling back to static HTML: %s",
                url,
                rendered.error,
            )
        return super().fetch_html(url)

    def _fetch_html_with_playwright(
        self, url: str, row: Optional[dict[str, Any]] = None
    ) -> FetchResult:
        self._last_rendered_with_playwright = False
        self._last_render_error = None

        try:
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError:
            self._last_render_error = "Playwright is not installed."
            return FetchResult(
                url=url,
                status_code=None,
                error=self._last_render_error,
            )

        page = None
        try:
            if self._playwright is None:
                self._playwright = sync_playwright().start()
                self._browser = self._playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--disable-software-rasterizer",
                        "--disable-extensions",
                        "--disable-background-networking",
                        "--disable-background-timer-throttling",
                        "--disable-renderer-backgrounding",
                        "--no-sandbox",
                    ],
                )

            return self._render_product_page(url, row=row)
        except Exception as exc:
            if self._is_browser_closed_error(exc):
                LOG.warning("Kaufmann browser closed while rendering %s; relaunching once.", url)
                self._discard_browser()
                try:
                    if self._playwright is None:
                        self._playwright = sync_playwright().start()
                    self._browser = self._playwright.chromium.launch(
                        headless=True,
                        args=[
                            "--disable-dev-shm-usage",
                            "--disable-gpu",
                            "--disable-software-rasterizer",
                            "--disable-extensions",
                            "--disable-background-networking",
                            "--disable-background-timer-throttling",
                            "--disable-renderer-backgrounding",
                            "--no-sandbox",
                        ],
                    )
                    return self._render_product_page(url, row=row)
                except Exception as retry_exc:
                    self._last_render_error = str(retry_exc)
                    return FetchResult(url=url, status_code=None, error=self._last_render_error)

            self._last_render_error = str(exc)
            return FetchResult(url=url, status_code=None, error=self._last_render_error)

    def _render_product_page(
        self, url: str, row: Optional[dict[str, Any]] = None
    ) -> FetchResult:
        context = None
        page = None
        try:
            context = self._browser.new_context(extra_http_headers=self.session.headers)
            page = context.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_load_state("networkidle", timeout=8000)

            target_color = self._target_color(row)
            selected_color = None
            if target_color:
                selected_color = self._select_color(page, target_color)
                self._last_selected_color = selected_color
                if selected_color:
                    page.wait_for_load_state("networkidle", timeout=8000)

            try:
                page.click('button:has-text("Vælg")', timeout=3000)
                page.wait_for_selector("ul.tw-font-medium.tw-text-base li", timeout=3000)
            except Exception:
                pass

            status_code = response.status if response else None
            html = page.content()
            if selected_color:
                html += f"\n<!-- highlide_selected_color={selected_color} -->"
            self._last_rendered_with_playwright = True
            return FetchResult(url=url, status_code=status_code, html=html)
        finally:
            if context:
                try:
                    context.close()
                except Exception:
                    pass

    def _is_browser_closed_error(self, exc: Exception) -> bool:
        message = str(exc).lower()
        return "target page, context or browser has been closed" in message

    def close(self) -> None:
        self._discard_browser()
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        super().close()

    def _discard_browser(self) -> None:
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None

    def _target_color(self, row: Optional[dict[str, Any]]) -> Optional[str]:
        if not row:
            return None
        color = row.get("color") or row.get("farve")
        if not color:
            return None
        return str(color)

    def _color_terms(self, color: str) -> list[str]:
        normalized = self._normalize_text(color)
        terms = {normalized}

        for token in normalized.split():
            if len(token) > 2:
                terms.add(token)

        color_aliases = {
            "green": ["green", "gron", "grøn"],
            "gron": ["green", "gron", "grøn"],
            "blue": ["blue", "bla", "blå"],
            "navy": ["navy", "marine", "bla", "blå"],
            "brown": ["brown", "brun"],
            "beige": ["beige", "sand"],
            "sand": ["sand", "beige"],
            "black": ["black", "sort"],
            "white": ["white", "hvid"],
            "grey": ["grey", "gray", "gra", "grå"],
            "gray": ["grey", "gray", "gra", "grå"],
            "red": ["red", "rod", "rød"],
            "yellow": ["yellow", "gul"],
            "orange": ["orange"],
            "purple": ["purple", "lilla"],
        }

        for token in list(terms):
            terms.update(color_aliases.get(token, []))

        return sorted(terms, key=len, reverse=True)

    def _normalize_text(self, value: str) -> str:
        value = value.lower().replace("&", " ")
        value = unicodedata.normalize("NFKD", value)
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        return re.sub(r"[^a-z0-9æøå]+", " ", value).strip()

    def _select_color(self, page: Any, color: str) -> Optional[str]:
        terms = self._color_terms(color)
        if not terms:
            return None

        return page.evaluate(
            """
            async (terms) => {
              const normalizedTerms = terms.map((term) => term.toLowerCase());
              const selector = [
                'button',
                'a',
                'label',
                '[role="button"]',
                '[x-on\\\\:click]',
                '[onclick]',
                'li',
                'div'
              ].join(',');

              function textFor(el) {
                const pieces = [
                  el.textContent,
                  el.getAttribute('aria-label'),
                  el.getAttribute('title'),
                  el.getAttribute('alt'),
                  el.getAttribute('data-color'),
                  el.getAttribute('data-colour'),
                  el.getAttribute('data-name')
                ];
                const img = el.querySelector && el.querySelector('img');
                if (img) {
                  pieces.push(img.getAttribute('alt'), img.getAttribute('title'));
                }
                return pieces.filter(Boolean).join(' ').toLowerCase();
              }

              const candidates = Array.from(document.querySelectorAll(selector))
                .filter((el) => {
                  const rect = el.getBoundingClientRect();
                  if (rect.width <= 0 || rect.height <= 0) return false;
                  const tag = el.tagName;
                  if (['BUTTON', 'A', 'LABEL', 'LI'].includes(tag)) return true;
                  if (el.getAttribute('role') === 'button') return true;
                  if (el.getAttribute('x-on:click') || el.getAttribute('onclick')) return true;
                  return textFor(el).length < 300;
                })
                .map((el) => ({ el, text: textFor(el) }))
                .filter((candidate) =>
                  normalizedTerms.some((term) => candidate.text.includes(term))
                );

              candidates.sort((a, b) => {
                const score = (candidate) => {
                  const text = candidate.text;
                  let value = 0;
                  if (candidate.el.tagName === 'BUTTON') value += 3;
                  if (candidate.el.tagName === 'A') value += 2;
                  if (text.length < 120) value += 2;
                  if (/farve|color|colour/.test(text)) value += 1;
                  return -value;
                };
                return score(a) - score(b);
              });

              if (!candidates.length) return null;
              candidates[0].el.click();
              await new Promise((resolve) => setTimeout(resolve, 750));
              return candidates[0].text.slice(0, 160);
            }
            """,
            terms,
        )

    def _extract_sizes(self, html: str) -> list[dict[str, Union[bool, str]]]:
        in_stock = re.findall(
            r"selectSize\([^)]+\)[^>]*>.*?<span[^>]*tw-leading-6\.5[^>]*>([^<]+)</span>",
            html,
            re.DOTALL,
        )
        out_of_stock = re.findall(
            r"<div[^>]*tw-flex[^>]*>\s*<span[^>]*tw-line-through[^>]*>([^<]+)</span>.*?remindMe",
            html,
            re.DOTALL,
        )

        sizes = [{"size": size.strip(), "in_stock": True} for size in in_stock if size.strip()]
        in_stock_names = {item["size"] for item in sizes}
        for size in out_of_stock:
            clean_size = size.strip()
            if clean_size and clean_size not in in_stock_names:
                sizes.append({"size": clean_size, "in_stock": False})

        return normalize_size_status(sizes)

    def _extract_prices(self, soup: BeautifulSoup) -> Tuple[Optional[float], Optional[float], str]:
        currency = "DKK"

        product_price_selectors = [
            '[itemprop="price"]',
            'meta[property="product:price:amount"]',
            'meta[property="og:price:amount"]',
            'div[x-text="$store.productStore.price"]',
            '[x-text="$store.productStore.price"]',
        ]

        for selector in product_price_selectors:
            price_tag = soup.select_one(selector)
            if not price_tag:
                continue
            price_value = price_tag.get("content") or price_tag.get_text(" ", strip=True)
            parsed = parse_danish_price(price_value)
            if parsed is not None:
                return parsed, None, currency

        title_text = soup.title.get_text(" ", strip=True) if soup.title else ""
        title_parts = [part.strip() for part in title_text.split("|")]
        title_price = parse_danish_price(title_parts[3]) if len(title_parts) >= 4 else None
        if title_price is not None:
            return title_price, None, currency

        for script in soup.find_all("script"):
            text = script.string or script.get_text()
            if "productStore" not in text and "price" not in text:
                continue
            for pattern in [
                r'"price"\s*:\s*"?(DKK\s*)?([\d.,]+)"?',
                r"price\s*:\s*'?(DKK\s*)?([\d.,]+)'?",
            ]:
                match = re.search(pattern, text)
                if match:
                    parsed = parse_danish_price(match.group(2))
                    if parsed is not None:
                        return parsed, None, currency

        return None, None, currency

    def parse_inventory(
        self,
        html: str,
        url: Optional[str] = None,
        row: Optional[dict[str, Any]] = None,
    ) -> InventorySnapshot:
        soup = BeautifulSoup(html, "html.parser")

        sizes = self._extract_sizes(html)
        current_price, sale_price, currency = self._extract_prices(soup)
        is_active = bool(current_price or sizes or soup.select_one('meta[property="og:url"]'))

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
                "parser": "kaufmann_html_selectors",
                "variant_count": len(sizes),
                "rendered_with_playwright": self._browser is not None,
                "sizes_reliable": bool(sizes) and self._last_rendered_with_playwright,
                "render_error": self._last_render_error,
                "target_color": self._target_color(row),
                "selected_color": self._last_selected_color,
            },
        )
