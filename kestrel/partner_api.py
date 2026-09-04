"""Client for the Kestrel Logistics Partner API (carrier freight invoices).

The service rate-limits (429 + Retry-After), falls over (503), is slow on the
first page of every cursor walk, reports timestamps in UTC and amounts in
paise. All of that is handled here so nothing else has to know."""
import random
import time
from datetime import datetime

import requests

from . import config
from .dates import parse_ts
from .db import cache_conn


class PartnerAPIError(RuntimeError):
    pass


class PartnerAPI:
    def __init__(self, base_url=None, api_key=None, max_retries=10, timeout=30, log=None):
        self.base = (base_url or config.PARTNER_API_URL).rstrip("/")
        self.session = requests.Session()
        self.session.headers["X-API-Key"] = api_key or config.PARTNER_API_KEY
        self.max_retries = max_retries
        self.timeout = timeout
        self.log = log or (lambda *_: None)

    def get(self, path, **params):
        url = f"{self.base}{path}"
        backoff = 0.5
        for attempt in range(self.max_retries):
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as e:
                self.log(f"  network error ({e.__class__.__name__}), retrying in {backoff:.1f}s")
                time.sleep(backoff); backoff = min(backoff * 2, 10)
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", backoff))
                self.log(f"  429 rate limited, waiting {wait:.0f}s")
                time.sleep(wait)
                continue
            if r.status_code in (500, 502, 503, 504):
                self.log(f"  {r.status_code} from upstream, retrying in {backoff:.1f}s")
                time.sleep(backoff + random.uniform(0, 0.3)); backoff = min(backoff * 2, 10)
                continue
            if r.status_code == 401:
                raise PartnerAPIError("Rejected API key (401). Check KESTREL_PARTNER_API_KEY.")
            raise PartnerAPIError(f"{r.status_code} from {url}: {r.text[:200]}")
        raise PartnerAPIError(f"gave up on {url} after {self.max_retries} attempts")

    def health(self):
        return self.get("/v1/health")

    def carriers(self):
        return self.get("/v1/carriers")["data"]

    def fuel_surcharge(self, month):
        return self.get("/v1/fuel_surcharge", month=month)

    def iter_invoice_pages(self, date_from=None, date_to=None, cursor=None):
        """Yield (rows, next_cursor) page by page. Pass a cursor to resume."""
        params = {"limit": 200}
        if date_from:
            params["from"] = str(date_from)
        if date_to:
            params["to"] = str(date_to)
        while True:
            if cursor:
                params["cursor"] = cursor
            page = self.get("/v1/freight_invoices", **params)
            cursor = page.get("next_cursor")
            yield page.get("data", []), cursor
            if not cursor:
                return


def normalise(inv):
    """Invoice as the API sends it -> row for cache.freight_invoices."""
    service_date = inv.get("service_date") or inv.get("invoice_date")
    return (
        inv["invoice_id"], inv.get("carrier_id"), inv.get("carrier_name"),
        inv.get("warehouse_code"), inv.get("route_code"),
        inv.get("invoice_date"), service_date, service_date[:7] if service_date else None,
        (inv.get("amount") or 0) / 100.0,                 # paise -> rupees
        inv.get("fuel_surcharge_pct"),
        (inv.get("detention_charge") or 0) / 100.0,       # also paise
        inv.get("distance_km"), inv.get("weight_kg"),
        1 if inv.get("temperature_controlled") else 0,
        inv.get("status"),
        parse_ts(inv.get("created_at_utc")),              # UTC -> IST
    )


UPSERT = """INSERT OR REPLACE INTO freight_invoices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""


def sync_invoices(date_from, date_to, api=None, log=print):
    """Pull invoices for a window into the cache. Safe to re-run: resumes
    from the last cursor if a previous run was interrupted."""
    api = api or PartnerAPI(log=log)
    key = f"cursor:{date_from}:{date_to}"
    with cache_conn() as c:
        row = c.execute("SELECT value FROM freight_sync WHERE key = ?", (key,)).fetchone()
        cursor = row[0] if row and row[0] not in (None, "done") else None
        if row and row[0] == "done":
            log(f"{date_from}..{date_to} already synced; delete data/cache.db to refetch")
            return 0
    if cursor:
        log(f"resuming from {cursor}")
    total, pages = 0, 0
    for rows, nxt in api.iter_invoice_pages(date_from, date_to, cursor):
        pages += 1
        with cache_conn() as c:
            c.executemany(UPSERT, [normalise(r) for r in rows])
            c.execute("INSERT OR REPLACE INTO freight_sync VALUES (?, ?)", (key, nxt or "done"))
        total += len(rows)
        log(f"page {pages}: {len(rows)} invoices (total {total}), next={nxt}")
    with cache_conn() as c:
        c.execute("INSERT OR REPLACE INTO freight_sync VALUES (?, ?)",
                  ("last_sync", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    return total
