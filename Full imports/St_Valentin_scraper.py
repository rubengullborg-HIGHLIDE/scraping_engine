import json
import re
from abc import ABC, abstractmethod

import requests
from bs4 import BeautifulSoup


class BaseScraper(ABC):
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "da-DK,da;q=0.9",
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


class STValentinScraper(BaseScraper):
    BASE_URL = "https://stvalentin.dk"

    def get_product_links(self, html):
        soup = BeautifulSoup(html, "html.parser")
        links = []

        for card in soup.select("product-card[handle]"):
            handle = card.get("handle")
            if handle:
                links.append(f"{self.BASE_URL}/products/{handle}")

        if not links:
            for a in soup.select('a[href*="/products/"]'):
                href = a["href"].split("?")[0].split("#")[0]
                if href.startswith("/"):
                    href = f"{self.BASE_URL}{href}"
                if "/products/" in href:
                    links.append(href)

        return list(dict.fromkeys(links))

    def _parse_price(self, text):
        if not text:
            return 0.0
        cleaned = text.replace("kr", "").replace(".", "").replace(",", ".").strip()
        try:
            return float("".join(c for c in cleaned if c.isdigit() or c == "."))
        except ValueError:
            return 0.0

    def _normalize_url(self, url):
        if not url:
            return None
        if url.startswith("//"):
            url = f"https:{url}"
        return url.split("?")[0] if "cdn/shop/files/" in url else url.split("?")[0]

    def _best_image_from_img(self, img):
        srcset = img.get("srcset")
        if srcset:
            candidates = []
            for part in srcset.split(","):
                part = part.strip()
                if not part:
                    continue
                pieces = part.split()
                url = pieces[0]
                width = 0
                if len(pieces) > 1 and pieces[1].endswith("w"):
                    try:
                        width = int(pieces[1][:-1])
                    except ValueError:
                        width = 0
                candidates.append((width, url))
            if candidates:
                return self._normalize_url(max(candidates, key=lambda x: x[0])[1])

        return self._normalize_url(img.get("src"))

    def _extract_product_group_ld_json(self, soup):
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get("@type") == "ProductGroup":
                return data
        return None

    def _extract_meta_product(self, html):
        match = re.search(r"var meta = (\{.*?\});\s*\n", html, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(1)).get("product")
        except json.JSONDecodeError:
            return None

    def _extract_breadcrumbs(self, soup):
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get("@type") == "BreadcrumbList":
                return [
                    item.get("name")
                    for item in data.get("itemListElement", [])
                    if item.get("name")
                ]
        return []

    def _extract_sizes(self, soup, product_group, meta_product):
        size_status = []

        if product_group and product_group.get("hasVariant"):
            for variant in product_group["hasVariant"]:
                size_name = variant.get("name", "").rsplit(" - ", 1)[-1].strip()
                availability = variant.get("offers", {}).get("availability", "")
                in_stock = "InStock" in availability
                if size_name:
                    size_status.append({"size": size_name, "in_stock": in_stock})
            if size_status:
                return size_status

        if meta_product and meta_product.get("variants"):
            for variant in meta_product["variants"]:
                size_name = variant.get("public_title") or variant.get("title")
                if size_name:
                    size_status.append(
                        {
                            "size": size_name,
                            "in_stock": bool(variant.get("available", False)),
                        }
                    )
            if size_status:
                return size_status

        picker = soup.select_one(
            'variant-picker[context="main_product"]'
        ) or soup.select_one("variant-picker")
        if picker:
            for label in picker.select("label.block-swatch"):
                size_name = label.get_text(strip=True)
                if size_name:
                    size_status.append(
                        {
                            "size": size_name,
                            "in_stock": "is-disabled" not in label.get("class", []),
                        }
                    )

        return size_status

    def _extract_images(self, soup, product_group):
        images = []

        for img in soup.select(".product-gallery__media img, .product-gallery img"):
            url = self._best_image_from_img(img)
            if url and url not in images and not url.startswith("data:image"):
                images.append(url)

        if not images and product_group:
            for variant in product_group.get("hasVariant", []):
                url = self._normalize_url(variant.get("image"))
                if url and url not in images:
                    images.append(url)

        if not images:
            og_image = soup.select_one('meta[property="og:image"]')
            if og_image and og_image.get("content"):
                images.append(self._normalize_url(og_image["content"]))

        return images

    def _parse_description_details(self, description):
        color = "Ukendt"
        fit = "Ukendt"
        materials = []

        for line in description.split("\n"):
            line = line.strip().lstrip("-").strip()
            if not line:
                continue
            if re.search(r"\bfit\b", line, re.IGNORECASE):
                fit = line
            elif re.search(r"\bg?sm\b", line, re.IGNORECASE) and "%" in line:
                materials.append(line)
            elif re.search(r"\d+%", line):
                materials.append(line)
            elif (
                line
                and color == "Ukendt"
                and not re.search(r"modellen|model", line, re.IGNORECASE)
            ):
                if len(line.split()) <= 4:
                    color = line

        return color, fit, materials

    def parse_product_details(self, html):
        soup = BeautifulSoup(html, "html.parser")
        product_group = self._extract_product_group_ld_json(soup)
        meta_product = self._extract_meta_product(html)

        title_tag = soup.select_one("h1.product-title") or soup.select_one(
            ".product-title"
        )
        title = title_tag.get_text(strip=True) if title_tag else "Ukendt"
        if product_group and product_group.get("name"):
            title = product_group["name"]

        price = 0.0
        sale_price_tag = soup.select_one("sale-price")
        if sale_price_tag:
            price = self._parse_price(sale_price_tag.get_text())
        elif product_group and product_group.get("hasVariant"):
            first_offer = product_group["hasVariant"][0].get("offers", {})
            try:
                price = float(first_offer.get("price", 0))
            except (TypeError, ValueError):
                price = 0.0
        elif meta_product and meta_product.get("variants"):
            price = meta_product["variants"][0].get("price", 0) / 100

        brand_tag = soup.select_one("a.vendor")
        brand = brand_tag.get_text(strip=True) if brand_tag else "S.T. VALENTIN"
        if product_group and product_group.get("brand", {}).get("name"):
            brand = product_group["brand"]["name"]

        desc_div = soup.select_one(
            '[data-block-type="description"] .prose'
        ) or soup.select_one(".prose")
        description = desc_div.get_text("\n", strip=True) if desc_div else ""
        if not description and product_group:
            description = product_group.get("description", "")

        color, fit, materials = self._parse_description_details(description)
        size_status = self._extract_sizes(soup, product_group, meta_product)
        images = self._extract_images(soup, product_group)
        breadcrumbs = self._extract_breadcrumbs(soup)

        if product_group and product_group.get("category"):
            if product_group["category"] not in breadcrumbs:
                breadcrumbs.append(product_group["category"])

        return {
            "navn": title,
            "pris": price,
            "brand": brand,
            "description": description,
            "materials": materials,
            "color": color,
            "fit": fit,
            "sizes": size_status,
            "images": images,
            "categories": breadcrumbs,
            "store": "S.T. VALENTIN",
        }
