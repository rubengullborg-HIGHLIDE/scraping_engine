import json
import random
import re
import time
from abc import ABC, abstractmethod

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


class BaseScraper(ABC):
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

    def fetch_html(self, url):
        try:
            res = requests.get(url, headers=self.headers, timeout=10)
            res.raise_for_status()
            return res.text
        except Exception as e:
            print(f"  [!] Fejl ved {url}: {e}")
            return None

    @abstractmethod
    def get_product_links(self, html):
        pass

    @abstractmethod
    def parse_product_details(self, html):
        pass


class KaufmanScraper(BaseScraper):
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "da-DK,da;q=0.9",
        }
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)

    def fetch_html_with_js(self, url):
        page = self._browser.new_page()
        page.goto(url, wait_until="networkidle")
        try:
            page.click('button:has-text("Vælg")', timeout=3000)
            page.wait_for_selector("ul.tw-font-medium.tw-text-base li", timeout=3000)
        except:
            pass
        html = page.content()
        page.close()
        return html

    def close(self):
        self._browser.close()
        self._playwright.stop()

    def _get_product_base_id(self, soup: BeautifulSoup) -> str | None:
        og = soup.select_one('meta[property="og:url"]')
        canonical = soup.select_one('link[rel="canonical"]')

        url = None
        if og and og.get("content"):
            url = og["content"]
        elif canonical and canonical.get("href"):
            url = canonical["href"]

        if not url:
            return None

        m = re.search(r"-(\d+)(?:$|\?)", url)
        if not m:
            return None

        digits = m.group(1)
        if len(digits) >= 6:
            return digits[:6]
        return digits

    def _extract_images_from_raw_html(
        self, html: str, base: str | None = None
    ) -> list[str]:

        if not html:
            return []

        if not base:
            base_match = re.search(
                r"/media/[^\"'\s>]+/(\d{5,8})_[A-Za-z0-9_-]+\.(?:png|jpe?g)",
                html,
                flags=re.IGNORECASE,
            )
            if not base_match:
                return []
            base = base_match.group(1)

        url_pattern = rf"https:(?:\\/\\/|//)www\.kaufmann\.dk(?:\\/|/)cdn-cgi(?:\\/|/)image(?:\\/|/)[^\"'\s>]+?(?:\\/|/)media(?:\\/|/)[^\"'\s>]+?(?:\\/|/){base}_[A-Za-z0-9_-]+\.(?:png|jpe?g)"
        urls = re.findall(url_pattern, html, flags=re.IGNORECASE)
        if not urls:
            return []

        preferred_prefix = "https://www.kaufmann.dk/cdn-cgi/image/width%3D1472%2Cheight%3D1472%2Cformat%3Dauto"

        by_filename: dict[str, str] = {}
        for u in urls:
            u = u.replace("\\/", "/")
            try:
                tail = u[u.index("/media/") :]
            except ValueError:
                continue

            filename = tail.rsplit("/", 1)[-1]
            by_filename.setdefault(filename, preferred_prefix + tail)

        def sort_key(item: tuple[str, str]):
            filename, _ = item
            m = re.search(r"-(\d{2})\.(?:png|jpe?g)$", filename, flags=re.IGNORECASE)
            return (0, int(m.group(1))) if m else (1, filename.lower())

        return [u for _, u in sorted(by_filename.items(), key=sort_key)]

    def get_product_links(self, html):
        soup = BeautifulSoup(html, "html.parser")
        links = []

        for a in soup.find_all("a", href=True):
            href = a["href"]

            if "/produkt/" not in href:
                continue

            clean_url = href.split("#")[0]

            if clean_url.startswith("/"):
                clean_url = f"https://www.kaufmann.dk{clean_url}"

            links.append(clean_url)

        return list(set(links))

    def parse_product_details(self, html):
        soup = BeautifulSoup(html, "html.parser")

        title_text = soup.title.get_text(strip=True) if soup.title else ""
        parts = [p.strip() for p in title_text.split("|")]

        produkt_navn = parts[0] if len(parts) >= 1 else ""
        farve = parts[1] if len(parts) >= 2 else ""
        brand = parts[2] if len(parts) >= 3 else "Ukendt"

        price_tag = soup.select_one('div[x-text="$store.productStore.price"]')
        if price_tag:
            price_text = price_tag.get_text(strip=True)  # fx "DKK 800"
            price_numbers = re.sub(r"[^\d]", "", price_text)
            pris = float(price_numbers) if price_numbers else 0.0
        else:
            raw_price = parts[3] if len(parts) >= 4 else ""
            price_numbers = re.sub(r"[^\d]", "", raw_price)
            pris = float(price_numbers) if price_numbers else 0.0

        desc_div = soup.select_one('div[itemprop="description"]')
        description = desc_div.get_text(" ", strip=True) if desc_div else ""

        page_text = str(soup)

        in_stock = re.findall(
            r"selectSize\([^)]+\)[^>]*>.*?<span[^>]*tw-leading-6\.5[^>]*>([^<]+)</span>",
            page_text,
            re.DOTALL,
        )

        out_of_stock = re.findall(
            r"<div[^>]*tw-flex[^>]*>\s*<span[^>]*tw-line-through[^>]*>([^<]+)</span>.*?remindMe",
            page_text,
            re.DOTALL,
        )

        in_stock_names = {s.strip() for s in in_stock}
        size_status = []

        for size in in_stock:
            size = size.strip()
            if size:
                size_status.append({"size": size, "in_stock": True})

        for size in out_of_stock:
            size = size.strip()
            if size and size not in in_stock_names:
                size_status.append({"size": size, "in_stock": False})

        # Deduplicate preserving order
        seen = set()
        unique_sizes = []
        for s in size_status:
            if s["size"] not in seen:
                seen.add(s["size"])
                unique_sizes.append(s)
        size_status = unique_sizes

        idx = page_text.find("selectSize")
        print("RAW HTML around selectSize:")
        print(repr(page_text[idx - 100 : idx + 300]))

        base_id = self._get_product_base_id(soup)
        images = self._extract_images_from_raw_html(html, base=base_id)

        if not images:
            thumb_imgs = soup.select(
                'div.tw-flex.tw-flex-col.tw-gap-2.tw-items-center img[sizes="80px"]'
            )

            for img in thumb_imgs:
                url = img.get("src")
                if url and not url.startswith("data:image") and url not in images:
                    images.append(url)

        if not images:
            gallery_imgs = soup.select(
                "#image-gallery-desktop img, #image-gallery-mobile img"
            )
            for img in gallery_imgs:
                srcset = img.get("srcset")
                if srcset:
                    url = srcset.split(",")[-1].strip().split(" ")[0]
                else:
                    url = img.get("src")

                if url and not url.startswith("data:image") and url not in images:
                    images.append(url)

        materials = []
        color = farve or "Ukendt"
        fit = "Ukendt"

        specs_list = soup.select("#product-description ul li")
        for li in specs_list:
            text = li.get_text(" ", strip=True)

            if "Farve:" in text and not farve:
                color = text.replace("Farve:", "").strip()
            elif "Fit:" in text:
                fit = text.replace("Fit:", "").strip()
            elif "Materiale:" in text:
                m_text = text.replace("Materiale:", "").strip()
                materials.extend([m.strip() for m in m_text.split(",")])

        return {
            "navn": produkt_navn,
            "pris": pris,
            "brand": brand,
            "description": description,
            "materials": materials,
            "color": color,
            "fit": fit,
            "sizes": size_status,
            "images": images,
            "store": "Kaufmann Aarhus",
        }
