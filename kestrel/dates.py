"""Timestamp and fiscal-calendar helpers.

Three source systems and two telematics vendors write timestamps four
different ways. Rather than fix the data we parse on read and expose the
normalised value through the views (see sql/views.sql)."""
from datetime import datetime, timedelta

from .config import FY_START_MONTH

IST_OFFSET = timedelta(hours=5, minutes=30)

# order matters: try the unambiguous ones first
_FORMATS = (
    "%Y-%m-%d %H:%M:%S",      # SFA_MOBILE, TELEMATICS_A, planned_arrival
    "%Y-%m-%dT%H:%M:%SZ",     # PARTNER_API, UTC
    "%d/%m/%Y %H:%M",         # ERP_WEB
    "%d-%b-%Y %I:%M %p",      # TELEMATICS_B
    "%Y-%m-%d",
)


def parse_ts(value):
    """Return an ISO 'YYYY-MM-DD HH:MM:SS' string in Asia/Kolkata, or None.

    Registered as a SQLite function so the views can call it."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    for fmt in _FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        if fmt.endswith("Z"):
            dt = dt + IST_OFFSET
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return None


def fiscal_year_start(d):
    """Calendar year in which the fiscal year containing `d` began."""
    return d.year if d.month >= FY_START_MONTH else d.year - 1


def fiscal_quarter(d):
    return ((d.month - FY_START_MONTH) % 12) // 3 + 1


def fiscal_label(d):
    y = fiscal_year_start(d)
    return f"FY{y % 100:02d}-{(y + 1) % 100:02d} Q{fiscal_quarter(d)}"


def quarter_bounds(fy_start_year, q):
    """(first_day, last_day) of fiscal quarter q in the FY starting fy_start_year."""
    m = FY_START_MONTH + (q - 1) * 3
    y = fy_start_year
    if m > 12:
        m -= 12
        y += 1
    start = datetime(y, m, 1).date()
    em, ey = m + 3, y
    if em > 12:
        em -= 12
        ey += 1
    end = datetime(ey, em, 1).date() - timedelta(days=1)
    return start, end


def last_complete_quarter(as_of):
    """The most recent fiscal quarter that ended on or before `as_of`."""
    y, q = fiscal_year_start(as_of), fiscal_quarter(as_of)
    start, end = quarter_bounds(y, q)
    if end > as_of:
        q -= 1
        if q == 0:
            q, y = 4, y - 1
    return y, q


def quarters_in_range(first_day, last_day):
    """All (fy_start_year, q, label, start, end) quarters overlapping the range, newest first."""
    out = []
    y, q = fiscal_year_start(last_day), fiscal_quarter(last_day)
    while True:
        s, e = quarter_bounds(y, q)
        if e < first_day:
            break
        out.append((y, q, f"FY{y % 100:02d}-{(y + 1) % 100:02d} Q{q}", s, e))
        q -= 1
        if q == 0:
            q, y = 4, y - 1
    return out
