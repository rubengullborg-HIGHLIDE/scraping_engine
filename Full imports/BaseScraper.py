import json
import random
import time
from abc import ABC, abstractmethod

import requests
from bs4 import BeautifulSoup


class BaseScraper(ABC):
    def __init__(self, base_url):
        self.base_url = base_url
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

    def fetch_html(self, url):
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            return response.text
        except Exception as e:
            print(f"  [!] Fejl ved hentning af {url}: {e}")
            return None

    def scrape_site(self, catalog_url):
        print(f"\n--- Starter scraping af: {catalog_url} ---")
        html = self.fetch_html(catalog_url)
        if not html:
            return []

        product_links = self.get_product_links(html)
        print(f"Found {len(product_links)} product links. Starting extraction...")

        all_products = []
        for index, link in enumerate(product_links):
            time.sleep(random.uniform(1.5, 3.0))

            print(f"[{index + 1}/{len(product_links)}] Processing: {link}")
            product_html = self.fetch_html(link)

            if product_html:
                details = self.parse_product_details(product_html)
                details["url"] = link
                print(f"    -> Saved: {details['name']} ({details['price']} kr.)")
                all_products.append(details)

        return all_products

    @abstractmethod
    def get_product_links(self, html):
        pass

    @abstractmethod
    def parse_product_details(self, html):
        pass
