"""Database access.

The operational db is opened read-only. A second file (cache.db) holds what
we fetch ourselves and is attached as `cache`. The clean views are TEMP, so
they exist only for the life of the connection."""
import sqlite3
from pathlib import Path

import pandas as pd

from . import config
from .dates import parse_ts

VIEWS_SQL = Path(__file__).parent / "sql" / "views.sql"

CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS freight_invoices (
    invoice_id TEXT PRIMARY KEY,
    carrier_id TEXT, carrier_name TEXT,
    warehouse_code TEXT, route_code TEXT,
    invoice_date TEXT, service_date TEXT, service_month TEXT,
    amount_inr REAL, fuel_surcharge_pct REAL, detention_inr REAL,
    distance_km REAL, weight_kg REAL, temperature_controlled INTEGER,
    status TEXT, created_at_ist TEXT
);
CREATE INDEX IF NOT EXISTS ix_fi_month ON freight_invoices(service_month);
CREATE TABLE IF NOT EXISTS freight_sync (
    key TEXT PRIMARY KEY, value TEXT
);
CREATE TABLE IF NOT EXISTS competitor_prices (
    listing_id INTEGER PRIMARY KEY,
    city TEXT, retailer TEXT, title TEXT, category TEXT,
    pack_value REAL, pack_uom TEXT,
    price_inr REAL, mrp_inr REAL, in_stock INTEGER,
    last_seen TEXT, url TEXT, scraped_at TEXT,
    product_id INTEGER, match_confidence REAL, match_note TEXT
);
"""


def ensure_cache():
    config.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(config.CACHE_PATH) as c:
        c.executescript(CACHE_SCHEMA)


def connect():
    if not config.DB_PATH.exists():
        raise FileNotFoundError(
            f"Database not found at {config.DB_PATH}. Copy kestrel_ops.db there "
            "or set KESTREL_DB=/path/to/kestrel_ops.db")
    ensure_cache()
    conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True, check_same_thread=False)
    conn.create_function("parse_ts", 1, parse_ts, deterministic=True)
    conn.execute("ATTACH DATABASE ? AS cache", (str(config.CACHE_PATH),))
    sql = VIEWS_SQL.read_text().replace("{on_time_min}", str(config.ON_TIME_TOLERANCE_MIN))
    conn.executescript(sql)
    return conn


def query(conn, sql, params=()):
    return pd.read_sql_query(sql, conn, params=params)


def cache_conn():
    """Writable connection to the cache only (used by the fetch scripts)."""
    ensure_cache()
    return sqlite3.connect(config.CACHE_PATH)
