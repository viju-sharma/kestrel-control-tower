"""Metric queries. Each function returns a DataFrame (or dict) and takes a
date window plus an optional region so the regional view is the same code
with one more filter."""
import pandas as pd

from .db import query

DIMS = {
    "region": ("region_name", "region_name"),
    "warehouse": ("warehouse_code", "warehouse_code || ' ' || warehouse_name"),
    "route": ("route_code", "route_code"),
    "outlet": ("outlet_code", "outlet_code || ' ' || outlet_name"),
    "channel": ("channel", "channel"),
    "month": ("order_month", "order_month"),
    "source": ("source_system", "source_system"),
}


def _window(start, end, region_id=None, date_col="order_date", region_col="region_id"):
    sql = f" {date_col} BETWEEN ? AND ? "
    params = [str(start), str(end)]
    if region_id:
        sql += f" AND {region_col} = ? "
        params.append(int(region_id))
    return sql, params


# ---------------------------------------------------------------- service

def service_summary(conn, start, end, region_id=None):
    w, p = _window(start, end, region_id)
    row = query(conn, f"""
        SELECT COUNT(*) AS orders,
               SUM(ordered_cases) AS ordered_cases, SUM(delivered_cases) AS delivered_cases,
               SUM(ordered_eaches) AS ordered_eaches, SUM(delivered_eaches) AS delivered_eaches,
               AVG(on_time) AS on_time, AVG(in_full) AS in_full, AVG(otif) AS otif,
               SUM(delivered_value_inr) AS delivered_value_inr,
               SUM(temperature_excursion_flag) AS excursions
        FROM v_order_service WHERE outlet_reportable = 1 AND {w}""", p).iloc[0].to_dict()
    row["fill_cases"] = row["delivered_cases"] / row["ordered_cases"] if row["ordered_cases"] else None
    row["fill_eaches"] = row["delivered_eaches"] / row["ordered_eaches"] if row["ordered_eaches"] else None
    return row


def service_by(conn, dim, start, end, region_id=None, min_orders=1):
    key, label = DIMS[dim]
    w, p = _window(start, end, region_id)
    return query(conn, f"""
        SELECT {key} AS key, {label} AS label,
               COUNT(*) AS orders,
               SUM(ordered_cases) AS ordered_cases, SUM(delivered_cases) AS delivered_cases,
               SUM(delivered_cases) / SUM(ordered_cases) AS fill_cases,
               SUM(delivered_eaches) / SUM(ordered_eaches) AS fill_eaches,
               AVG(on_time) AS on_time, AVG(in_full) AS in_full, AVG(otif) AS otif,
               AVG(delay_minutes) AS avg_delay_min,
               SUM(late_2h) * 1.0 / COUNT(*) AS late_2h_share
        FROM v_order_service
        WHERE outlet_reportable = 1 AND {w}
        GROUP BY 1, 2 HAVING COUNT(*) >= ?
        ORDER BY 1""", p + [min_orders])


def worst(conn, dim, metric, start, end, region_id=None, n=5, min_orders=5):
    df = service_by(conn, dim, start, end, region_id, min_orders)
    return df.sort_values(metric).head(n)


def late_routes(conn, start, end, region_id=None, share=0.10, hours=2):
    w, p = _window(start, end, region_id)
    return query(conn, f"""
        SELECT route_code, warehouse_code, region_name, COUNT(*) AS deliveries,
               SUM(CASE WHEN delay_minutes > ? THEN 1 ELSE 0 END) AS late,
               SUM(CASE WHEN delay_minutes > ? THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS late_share,
               AVG(delay_minutes) AS avg_delay_min
        FROM v_order_service WHERE outlet_reportable = 1 AND {w}
        GROUP BY 1, 2, 3 HAVING late_share > ?
        ORDER BY late_share DESC""", [hours * 60, hours * 60] + p + [share])


def short_reasons(conn, start, end, region_id=None):
    w, p = _window(start, end, region_id, date_col="o.order_date", region_col="o.region_id")
    return query(conn, f"""
        SELECT l.short_reason_code AS reason,
               SUM(l.ordered_cases - l.delivered_cases) AS short_cases,
               SUM(l.line_value_inr - l.delivered_value_inr) AS short_value_inr
        FROM v_order_lines l JOIN v_orders o ON o.order_id = l.order_id
        WHERE o.outlet_reportable = 1 AND o.order_status IN ('DELIVERED','PARTIAL') AND {w}
        GROUP BY 1 ORDER BY 2 DESC""", p)


# ------------------------------------------------------------- cold chain

def excursions_by_month(conn, start, end, region_id=None):
    w, p = _window(start, end, region_id)
    return query(conn, f"""
        SELECT order_month AS month,
               SUM(has_chilled) AS chilled_deliveries,
               SUM(CASE WHEN has_chilled = 1 THEN temperature_excursion_flag ELSE 0 END) AS excursions,
               100.0 * SUM(CASE WHEN has_chilled = 1 THEN temperature_excursion_flag ELSE 0 END)
                     / NULLIF(SUM(has_chilled), 0) AS per_100_chilled,
               SUM(temperature_excursion_flag) AS excursions_all
        FROM v_order_service WHERE outlet_reportable = 1 AND {w}
        GROUP BY 1 ORDER BY 1""", p)


def excursions_by(conn, dim, start, end, region_id=None):
    key, label = DIMS[dim]
    w, p = _window(start, end, region_id)
    return query(conn, f"""
        SELECT {key} AS key, {label} AS label,
               SUM(has_chilled) AS chilled_deliveries,
               SUM(CASE WHEN has_chilled = 1 THEN temperature_excursion_flag ELSE 0 END) AS excursions,
               100.0 * SUM(CASE WHEN has_chilled = 1 THEN temperature_excursion_flag ELSE 0 END)
                     / NULLIF(SUM(has_chilled), 0) AS per_100_chilled,
               MAX(max_temp_celsius) AS peak_temp_c
        FROM v_order_service WHERE outlet_reportable = 1 AND has_chilled = 1 AND {w}
        GROUP BY 1, 2 ORDER BY per_100_chilled DESC""", p)


def latest_snapshot_date(conn):
    return conn.execute("SELECT MAX(snapshot_date) FROM inventory_snapshots").fetchone()[0]


def near_expiry(conn, days=30, region_id=None, snapshot_date=None):
    snapshot_date = snapshot_date or latest_snapshot_date(conn)
    sql = """
        SELECT warehouse_code, warehouse_name, category, is_chilled,
               SUM(on_hand_cases) AS cases, SUM(on_hand_value_inr) AS value_inr,
               COUNT(DISTINCT sku_code) AS skus, MIN(days_to_expiry) AS soonest_days
        FROM v_inventory
        WHERE snapshot_date = ? AND days_to_expiry <= ? AND on_hand_cases > 0"""
    p = [snapshot_date, days]
    if region_id:
        sql += " AND region_id = ?"
        p.append(int(region_id))
    return query(conn, sql + " GROUP BY 1,2,3,4 ORDER BY value_inr DESC", p)


def near_expiry_total(conn, days=30, region_id=None):
    df = near_expiry(conn, days, region_id)
    return {"cases": float(df["cases"].sum()), "value_inr": float(df["value_inr"].sum()),
            "snapshot_date": latest_snapshot_date(conn)}


def cold_chain_returns(conn, start, end, region_id=None):
    w, p = _window(start, end, region_id, date_col="return_date")
    return query(conn, f"""
        SELECT category, COUNT(*) AS credit_notes, SUM(return_cases) AS cases,
               SUM(credit_note_value_inr) AS value_inr
        FROM v_returns WHERE reason_code = 'RT06' AND outlet_reportable = 1 AND {w}
        GROUP BY 1 ORDER BY value_inr DESC""", p)


# ------------------------------------------------------------------ money

def returns_summary(conn, start, end, region_id=None):
    w, p = _window(start, end, region_id, date_col="return_date")
    r = query(conn, f"""
        SELECT COUNT(*) AS credit_notes, SUM(credit_note_value_inr) AS value_inr,
               SUM(CASE WHEN reason_code='RT06' THEN credit_note_value_inr ELSE 0 END) AS cold_chain_value_inr
        FROM v_returns WHERE outlet_reportable = 1 AND {w}""", p).iloc[0].to_dict()
    w2, p2 = _window(start, end, region_id)
    d = query(conn, f"SELECT SUM(delivered_value_inr) AS v FROM v_order_service WHERE outlet_reportable = 1 AND {w2}", p2).iloc[0]["v"]
    r["dispatch_value_inr"] = d
    r["returns_pct"] = (r["value_inr"] / d) if d else None
    return r


def returns_by(conn, dim, start, end, region_id=None):
    col = {"category": "category", "reason": "return_reason_code", "channel": "channel",
           "warehouse": "warehouse_code", "region": "region_name", "disposition": "disposition",
           "month": "return_month"}[dim]
    w, p = _window(start, end, region_id, date_col="return_date")
    return query(conn, f"""
        SELECT {col} AS key, COUNT(*) AS credit_notes, SUM(return_cases) AS cases,
               SUM(credit_note_value_inr) AS value_inr
        FROM v_returns WHERE outlet_reportable = 1 AND {w}
        GROUP BY 1 ORDER BY value_inr DESC""", p)


def returns_category_reason(conn, start, end, region_id=None):
    """Value by category with the reason that contributes most to it."""
    w, p = _window(start, end, region_id, date_col="return_date")
    df = query(conn, f"""
        SELECT category, return_reason_code AS reason, COUNT(*) AS credit_notes,
               SUM(credit_note_value_inr) AS value_inr
        FROM v_returns WHERE outlet_reportable = 1 AND {w}
        GROUP BY 1, 2""", p)
    if df.empty:
        return df
    tot = df.groupby("category", as_index=False)["value_inr"].sum().rename(columns={"value_inr": "category_value_inr"})
    lead = df.sort_values("value_inr", ascending=False).drop_duplicates("category")[["category", "reason", "value_inr"]]
    lead = lead.rename(columns={"reason": "leading_reason", "value_inr": "leading_reason_value_inr"})
    out = tot.merge(lead, on="category").sort_values("category_value_inr", ascending=False)
    out["leading_reason_share"] = out["leading_reason_value_inr"] / out["category_value_inr"]
    return out


def returns_share_by_category(conn, start, end, region_id=None):
    """Credit value as % of delivered value, per category (leakage)."""
    w, p = _window(start, end, region_id, date_col="return_date")
    ret = query(conn, f"""SELECT category, SUM(credit_note_value_inr) AS returns_inr
                          FROM v_returns WHERE outlet_reportable=1 AND {w} GROUP BY 1""", p)
    w2, p2 = _window(start, end, region_id, date_col="o.order_date", region_col="o.region_id")
    disp = query(conn, f"""SELECT l.category, SUM(l.delivered_value_inr) AS dispatch_inr
                           FROM v_order_lines l JOIN v_orders o ON o.order_id=l.order_id
                           WHERE o.outlet_reportable=1 AND o.order_status IN ('DELIVERED','PARTIAL') AND {w2}
                           GROUP BY 1""", p2)
    df = disp.merge(ret, on="category", how="left").fillna({"returns_inr": 0})
    df["returns_pct"] = df["returns_inr"] / df["dispatch_inr"]
    return df.sort_values("returns_pct", ascending=False)


def freight_coverage(conn):
    return query(conn, """SELECT service_month, COUNT(*) AS invoices, SUM(amount_inr) AS amount_inr
                          FROM cache.freight_invoices GROUP BY 1 ORDER BY 1""")


def freight_per_case(conn, dim, start, end, region_id=None):
    """Freight cost per delivered case.

    Invoices carry a warehouse code, a route code and a service date but no
    delivery id. The route code cannot be trusted: on 87% of invoices it names
    a route that belongs to a different warehouse, and only a third of the
    spend lands on a warehouse+route+month cell that has deliveries. So we
    join on warehouse + month, which keeps every rupee, and do not offer a
    route-level cut. Carrier figures share each cell's cases in proportion
    to what each carrier billed in it."""
    # region here is the warehouse's region, not the ordering outlet's, so a
    # warehouse-month cell is counted once
    w, p = _window(start, end, region_id, date_col="s.order_date", region_col="wh.region_id")
    cases = query(conn, f"""
        SELECT s.warehouse_code, s.order_month AS month, rg.region_name, SUM(s.delivered_cases) AS cases
        FROM v_order_service s
        JOIN warehouses wh ON wh.warehouse_code = s.warehouse_code
        JOIN regions rg ON rg.region_id = wh.region_id
        WHERE s.outlet_reportable = 1 AND {w}
        GROUP BY 1, 2, 3""", p)
    inv = query(conn, """
        SELECT warehouse_code, service_month AS month, carrier_name,
               SUM(amount_inr) AS freight_inr, COUNT(*) AS invoices
        FROM cache.freight_invoices WHERE service_date BETWEEN ? AND ?
        GROUP BY 1, 2, 3""", [str(start), str(end)])
    if inv.empty or cases.empty:
        return pd.DataFrame()
    cell = inv.groupby(["warehouse_code", "month"], as_index=False)["freight_inr"].sum() \
              .rename(columns={"freight_inr": "cell_freight"})
    m = inv.merge(cell, on=["warehouse_code", "month"]) \
           .merge(cases, on=["warehouse_code", "month"], how="inner")
    m["cases_alloc"] = m["cases"] * m["freight_inr"] / m["cell_freight"]
    key = {"warehouse": "warehouse_code", "carrier": "carrier_name", "month": "month",
           "region": "region_name"}[dim]
    g = m.groupby(key, as_index=False).agg(freight_inr=("freight_inr", "sum"),
                                           cases=("cases_alloc", "sum"),
                                           invoices=("invoices", "sum"))
    g["freight_per_case"] = g["freight_inr"] / g["cases"]
    return g.sort_values("freight_per_case", ascending=False)


# ------------------------------------------------------------------ price

def price_gap(conn, city=None, category=None, freshness_days=14, as_of=None):
    """Kestrel MRP vs lowest matched competitor shelf price per SKU and city."""
    as_of = as_of or conn.execute("SELECT MAX(last_seen) FROM cache.competitor_prices").fetchone()[0]
    if not as_of:
        return pd.DataFrame()
    sql = """
        SELECT c.city, p.category, p.sku_code, p.product_name, p.mrp_inr,
               MIN(c.price_inr) AS lowest_price_inr,
               COUNT(*) AS listings, MAX(c.last_seen) AS last_seen,
               MIN(c.match_confidence) AS match_confidence
        FROM cache.competitor_prices c JOIN products p ON p.product_id = c.product_id
        WHERE c.product_id IS NOT NULL AND c.price_inr > 0
          AND julianday(?) - julianday(c.last_seen) <= ?"""
    p = [as_of, freshness_days]
    if city:
        sql += " AND c.city = ?"; p.append(city)
    if category:
        sql += " AND p.category = ?"; p.append(category)
    df = query(conn, sql + " GROUP BY 1,2,3,4,5", p)
    if df.empty:
        return df
    df["gap_inr"] = df["mrp_inr"] - df["lowest_price_inr"]
    df["gap_pct"] = df["gap_inr"] / df["mrp_inr"]
    return df


def price_gap_summary(conn, freshness_days=14):
    df = price_gap(conn, freshness_days=freshness_days)
    if df.empty:
        return df
    return df.groupby(["city", "category"], as_index=False).agg(
        skus=("sku_code", "nunique"), median_gap_pct=("gap_pct", "median"),
        undercut_skus=("gap_pct", lambda s: int((s > 0).sum())))


def top_skus_by_value(conn, start, end, n=20, region_id=None):
    w, p = _window(start, end, region_id, date_col="o.order_date", region_col="o.region_id")
    return query(conn, f"""
        SELECT l.product_id, l.sku_code, l.product_name, l.category,
               SUM(l.delivered_value_inr) AS value_inr
        FROM v_order_lines l JOIN v_orders o ON o.order_id = l.order_id
        WHERE o.outlet_reportable = 1 AND o.order_status IN ('DELIVERED','PARTIAL') AND {w}
        GROUP BY 1,2,3,4 ORDER BY value_inr DESC LIMIT ?""", p + [n])


# ------------------------------------------------------------- data quality

def discontinued_orders(conn, region_id=None):
    sql = """
        SELECT o.outlet_code, o.outlet_name, o.region_name, l.sku_code, l.product_name,
               p.discontinued_date, COUNT(*) AS lines, MIN(o.order_date) AS first_order, MAX(o.order_date) AS last_order,
               SUM(l.ordered_cases) AS ordered_cases
        FROM v_order_lines l JOIN v_orders o ON o.order_id = l.order_id
        JOIN products p ON p.product_id = l.product_id
        WHERE p.status = 'DISCONTINUED' AND o.order_date > p.discontinued_date AND o.outlet_reportable = 1"""
    p = []
    if region_id:
        sql += " AND o.region_id = ?"; p.append(int(region_id))
    return query(conn, sql + " GROUP BY 1,2,3,4,5,6 ORDER BY lines DESC", p)


def regions(conn):
    return query(conn, "SELECT region_id, region_name, regional_manager FROM regions ORDER BY region_id")


def data_range(conn):
    r = conn.execute("SELECT MIN(order_date), MAX(order_date) FROM orders").fetchone()
    return r[0], r[1]
