from datetime import date

from kestrel.dates import parse_ts, fiscal_label, last_complete_quarter, quarter_bounds


def test_parses_every_source_format():
    assert parse_ts("01/01/2025 18:51") == "2025-01-01 18:51:00"        # ERP_WEB
    assert parse_ts("2025-01-01 16:46:00") == "2025-01-01 16:46:00"     # SFA_MOBILE
    assert parse_ts("03-Jan-2025 12:43 PM") == "2025-01-03 12:43:00"    # TELEMATICS_B
    assert parse_ts("03-Jan-2025 12:43 AM") == "2025-01-03 00:43:00"


def test_partner_api_is_utc_and_shifted_to_ist():
    assert parse_ts("2025-01-01T03:29:00Z") == "2025-01-01 08:59:00"
    # crossing midnight moves the local date
    assert parse_ts("2025-01-01T20:00:00Z") == "2025-01-02 01:30:00"


def test_garbage_is_none():
    assert parse_ts(None) is None
    assert parse_ts("") is None
    assert parse_ts("yesterday") is None


def test_fiscal_year_runs_april_to_march():
    assert fiscal_label(date(2026, 4, 1)) == "FY26-27 Q1"
    assert fiscal_label(date(2026, 6, 30)) == "FY26-27 Q1"
    assert fiscal_label(date(2026, 3, 31)) == "FY25-26 Q4"
    assert fiscal_label(date(2025, 1, 15)) == "FY24-25 Q4"


def test_quarter_bounds():
    assert quarter_bounds(2026, 1) == (date(2026, 4, 1), date(2026, 6, 30))
    assert quarter_bounds(2025, 4) == (date(2026, 1, 1), date(2026, 3, 31))


def test_last_complete_quarter_as_of_end_of_data():
    assert last_complete_quarter(date(2026, 6, 30)) == (2026, 1)
    assert last_complete_quarter(date(2026, 6, 29)) == (2025, 4)
