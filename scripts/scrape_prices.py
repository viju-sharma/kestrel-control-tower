#!/usr/bin/env python
"""Scrape BazaarPulse listing pages into data/cache.db and match them to SKUs.

    cd bazaarpulse_site && python3 -m http.server 8080   # in another shell
    python scripts/scrape_prices.py [--city mumbai]

Respects robots.txt including the 1s crawl delay, so a full run of ~66
listing pages takes about a minute. Detail pages are not fetched: the listing
card already carries the current price and last-seen date."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kestrel.db import connect  # noqa: E402
from kestrel.matching import load_products  # noqa: E402
from kestrel.scraper import PAGE_SCHEMES, Site, scrape_all  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", action="append", choices=list(PAGE_SCHEMES))
    a = ap.parse_args()
    site = Site(log=print)
    try:
        site.session.get(f"{site.base}/index.html", timeout=5).raise_for_status()
    except Exception as e:
        sys.exit(f"BazaarPulse not reachable at {site.base}: {e}\n"
                 "serve it with: cd bazaarpulse_site && python3 -m http.server 8080")
    products = load_products(connect())
    n = scrape_all(products, site=site, cities=a.city)
    print(f"done, {n} listings stored")


if __name__ == "__main__":
    main()
