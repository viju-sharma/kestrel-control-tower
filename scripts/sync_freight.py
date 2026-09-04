#!/usr/bin/env python
"""Fetch carrier invoices from the partner API into data/cache.db.

    python scripts/sync_freight.py --from 2026-04-01 --to 2026-06-30

Defaults to the last complete fiscal quarter in the database. Re-running
resumes an interrupted walk and skips windows already done."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kestrel.dates import last_complete_quarter, quarter_bounds  # noqa: E402
from kestrel.partner_api import PartnerAPI, PartnerAPIError, sync_invoices  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from")
    ap.add_argument("--to", dest="date_to")
    ap.add_argument("--all", action="store_true", help="walk every invoice (takes a few minutes)")
    a = ap.parse_args()

    if a.all:
        date_from, date_to = "2025-01-01", "2026-06-30"
    elif a.date_from and a.date_to:
        date_from, date_to = a.date_from, a.date_to
    else:
        from datetime import date
        from kestrel.db import connect
        from kestrel.metrics import data_range
        _, last = data_range(connect())
        y, q = last_complete_quarter(date.fromisoformat(last))
        date_from, date_to = quarter_bounds(y, q)
        print(f"no window given, using last complete quarter {date_from}..{date_to}")

    api = PartnerAPI(log=print)
    try:
        api.health()
    except Exception as e:
        sys.exit(f"partner API not reachable at {api.base}: {e}\n"
                 "start it with: python partner_api/server.py")
    try:
        n = sync_invoices(date_from, date_to, api)
    except PartnerAPIError as e:
        sys.exit(str(e))
    print(f"done, {n} invoices stored")


if __name__ == "__main__":
    main()
