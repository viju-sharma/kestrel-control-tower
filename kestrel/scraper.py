"""BazaarPulse scraper.

Honours robots.txt (Disallow /internal/, Crawl-delay 1). Each city writes the
price differently and paginates differently; the in-page pager on the two
"index" cities links to ?p=N, which a static server answers with page 1, so we
build page URLs ourselves from the "page 1 of N" breadcrumb."""
import re
import time
import urllib.robotparser as robotparser
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from . import config
from .db import cache_conn
from .matching import match_listing, parse_pack

PAGE_SCHEMES = {
    # city slug -> function(n) -> path
    "mumbai":    lambda n: f"/city/mumbai/page/{n}.html",
    "delhi":     lambda n: f"/city/delhi/page/{n}.html",
    "bengaluru": lambda n: "/city/bengaluru/index.html" if n == 1 else f"/city/bengaluru/index_p{n}.html",
    "chennai":   lambda n: "/city/chennai/index.html" if n == 1 else f"/city/chennai/index_p{n}.html",
}

_money = re.compile(r"(\d[\d,]*\.?\d*)")


class Site:
    def __init__(self, base_url=None, log=None):
        self.base = (base_url or config.BAZAAR_URL).rstrip("/")
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "kestrel-control-tower/0.1 (price position; respects robots.txt)"
        self.log = log or (lambda *_: None)
        self.robots = robotparser.RobotFileParser()
        try:
            self.robots.parse(self.session.get(f"{self.base}/robots.txt", timeout=10).text.splitlines())
        except requests.RequestException:
            self.robots.parse(["User-agent: *", "Allow: /"])
        delay = self.robots.crawl_delay("*")
        self.delay = float(delay) if delay else 0.0
        self._last = 0.0

    def allowed(self, path):
        return self.robots.can_fetch("*", f"{self.base}{path}")

    def fetch(self, path):
        """GET a path, or None on 404. Raises for anything robots.txt forbids."""
        if not self.allowed(path):
            raise PermissionError(f"robots.txt disallows {path}")
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        r = self.session.get(f"{self.base}{path}", timeout=20)
        if r.status_code == 404:
            self.log(f"  404 {path}")
            return None
        r.raise_for_status()
        return r.text


def parse_price(card):
    """Four markups, one number. Returns rupees as float or None."""
    el = card.select_one("[data-price-paise]")                    # Bengaluru
    if el:
        try:
            return int(el["data-price-paise"]) / 100.0
        except (ValueError, TypeError):
            pass
    for sel in (".price", ".sellingPrice", ".amt"):               # Mumbai, Chennai, Delhi
        el = card.select_one(sel)
        if el:
            m = _money.search(el.get_text(" ", strip=True).replace("₹", ""))
            if m:
                return float(m.group(1).replace(",", ""))
    return None


def parse_card(card, city):
    a = card.select_one("a[href]")
    title = a.get_text(strip=True) if a else card.get_text(" ", strip=True)[:80]
    muted = [m.get_text(" ", strip=True) for m in card.select(".muted")]
    retailer, pack_text, category = None, None, None
    mrp, in_stock, last_seen = None, None, None
    for line in muted:
        parts = [p.strip() for p in line.split("·")]
        if line.startswith("MRP"):
            m = _money.search(parts[0])
            mrp = float(m.group(1).replace(",", "")) if m else None
            in_stock = 0 if any("unavailable" in p.lower() for p in parts) else 1
        elif line.startswith("Last seen"):
            last_seen = line.split(":", 1)[1].strip()
        elif len(parts) == 3 and retailer is None:
            retailer, pack_text, category = parts
    pack_value, pack_uom = parse_pack(pack_text or title)
    try:
        listing_id = int(card.get("data-listing-id"))
    except (TypeError, ValueError):
        m = re.search(r"/product/(\d+)", a["href"]) if a else None
        listing_id = int(m.group(1)) if m else None
    return {
        "listing_id": listing_id, "city": city, "retailer": retailer, "title": title,
        "category": category, "pack_value": pack_value, "pack_uom": pack_uom,
        "price_inr": parse_price(card), "mrp_inr": mrp, "in_stock": in_stock,
        "last_seen": last_seen, "url": urljoin("/", a["href"]) if a else None,
    }


def page_count(html):
    m = re.search(r"page\s+\d+\s+of\s+(\d+)", html)
    return int(m.group(1)) if m else 1


CITY_NAMES = {"mumbai": "Mumbai", "delhi": "Delhi", "bengaluru": "Bengaluru", "chennai": "Chennai"}


def crawl_city(site, slug):
    first = site.fetch(PAGE_SCHEMES[slug](1))
    if first is None:
        return []
    n = page_count(first)
    site.log(f"{slug}: {n} pages")
    listings = {}
    for i in range(1, n + 1):
        html = first if i == 1 else site.fetch(PAGE_SCHEMES[slug](i))
        if html is None:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for card in soup.select(".product-item"):
            row = parse_card(card, CITY_NAMES[slug])
            if row["listing_id"] is not None:
                listings[row["listing_id"]] = row      # some listings repeat across pages
    return list(listings.values())


UPSERT = """INSERT OR REPLACE INTO competitor_prices
 (listing_id, city, retailer, title, category, pack_value, pack_uom, price_inr, mrp_inr,
  in_stock, last_seen, url, scraped_at, product_id, match_confidence, match_note)
 VALUES (:listing_id, :city, :retailer, :title, :category, :pack_value, :pack_uom, :price_inr,
  :mrp_inr, :in_stock, :last_seen, :url, :scraped_at, :product_id, :match_confidence, :match_note)"""


def scrape_all(products, site=None, cities=None, log=print):
    """Crawl every city listing page, match to Kestrel SKUs, store in cache."""
    site = site or Site(log=log)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = 0
    for slug in (cities or PAGE_SCHEMES):
        rows = crawl_city(site, slug)
        for r in rows:
            pid, conf, note = match_listing(r, products)
            r.update(scraped_at=now, product_id=pid, match_confidence=conf, match_note=note)
        with cache_conn() as c:
            c.executemany(UPSERT, rows)
        matched = sum(1 for r in rows if r["product_id"])
        log(f"{slug}: {len(rows)} listings, {matched} matched to a SKU")
        total += len(rows)
    return total
