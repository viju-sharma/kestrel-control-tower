"""Sanity checks against the real database. Skipped when it is not present."""
import pytest

from kestrel import config

pytestmark = pytest.mark.skipif(not config.DB_PATH.exists(), reason="kestrel_ops.db not available")


@pytest.fixture(scope="module")
def conn():
    from kestrel.db import connect
    return connect()


def test_unit_conversion_is_consistent(conn):
    # eaches / case_pack must equal cases on every line, in both directions
    n = conn.execute("""
        SELECT COUNT(*) FROM v_order_lines
        WHERE abs(ordered_eaches / NULLIF(case_pack_at_order,0) - ordered_cases) > 1e-6
    """).fetchone()[0]
    assert n == 0


def test_test_outlets_are_flagged(conn):
    rows = conn.execute("SELECT outlet_code FROM v_outlets WHERE is_test = 1").fetchall()
    assert {r[0] for r in rows} >= {"TST00001", "TST00002", "TST00003"}


def test_city_spellings_collapse(conn):
    cities = {r[0] for r in conn.execute("SELECT DISTINCT city_clean FROM v_outlets")}
    assert "Bangalore" not in cities and "New Delhi" not in cities


def test_service_excludes_cancelled_and_open(conn):
    st = {r[0] for r in conn.execute("SELECT DISTINCT order_status FROM v_order_service")}
    assert st == {"DELIVERED", "PARTIAL"}


def test_returns_are_positive(conn):
    assert conn.execute("SELECT MIN(return_qty) FROM v_returns").fetchone()[0] >= 0
