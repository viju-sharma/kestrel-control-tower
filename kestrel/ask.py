"""Plain-English questions over the clean views.

An LLM writes one read-only SQLite query against the views described below.
We run it, show the SQL and the rows, then ask the model for a two-sentence
reading of the numbers. Anthropic is used if ANTHROPIC_API_KEY is set;
otherwise any OpenAI-compatible endpoint (Gemini, Groq, Ollama...) via
LLM_API_KEY / LLM_BASE_URL. Without either the app falls back to a small
library of prepared questions so the feature still opens."""
import os
import re
import threading
import time

import requests

from . import config
from .db import query

SCHEMA = """
You are answering questions for the Head of Supply Chain at Kestrel Provisions, an Indian
grocery distributor. Write ONE SQLite SELECT statement against these views (do not use the
raw tables unless a column only exists there). Dates are 'YYYY-MM-DD' text.

v_order_service  -- one row per fulfilled order (status DELIVERED or PARTIAL). THE grain for service KPIs.
  order_id, order_date, order_month ('YYYY-MM'), fy_start_year, fiscal_quarter (1=Apr-Jun),
  region_id, region_name, warehouse_id, warehouse_code, warehouse_name, route_id, route_code,
  outlet_id, outlet_code, outlet_name, outlet_city, channel (GT/MT/HORECA/ECOM_DARKSTORE), source_system,
  outlet_reportable (1 = active, not deleted, not a test outlet: ALWAYS filter =1 unless asked otherwise),
  ordered_cases, delivered_cases, ordered_eaches, delivered_eaches,
  order_value_inr, delivered_value_inr, short_lines, line_count,
  in_full (1/0, case fill >= {in_full}%), on_time (1/0, delay <= {on_time} min), otif (1/0),
  delay_minutes (negative = early), late_2h (1/0), temperature_excursion_flag, max_temp_celsius, has_chilled (1/0)
  Fill rate = SUM(delivered_cases)/SUM(ordered_cases)  (or eaches). OTIF = AVG(otif).

v_order_lines    -- order lines with units normalised
  order_id, order_line_id, product_id, sku_code, product_name, brand, category, subcategory, is_chilled,
  ordered_qty, delivered_qty, qty_uom (CASE/EACH, per line!), case_pack_at_order,
  ordered_cases, delivered_cases, ordered_eaches, delivered_eaches, line_value_inr, delivered_value_inr,
  short_reason_code, unit_price_inr

v_orders         -- all order headers incl. CANCELLED/OPEN, joined to outlet
  order_id, order_date, order_month, fy_start_year, fiscal_quarter, order_status, region_name, warehouse_code,
  route_code, outlet_code, outlet_name, outlet_city, channel, source_system, outlet_reportable, created_at_ist,
  order_value_gross_inr, order_value_net_inr, promo_code

v_deliveries     -- delivery_id, order_id, route_code, warehouse_code, delay_minutes, on_time, late_2h,
  temperature_excursion_flag, max_temp_celsius, is_reefer, telematics_vendor, actual_arrival_ist, dispatch_datetime

v_returns        -- credit notes, quantities made positive
  return_id, return_date, return_month, fy_start_year, fiscal_quarter, return_qty, qty_uom, return_cases,
  return_reason_code (RT01_NEAR_EXPIRY, RT02_DAMAGE_TRANSIT, RT03_WRONG_SKU, RT04_QUALITY, RT05_OVERSUPPLY,
  RT06_COLD_CHAIN_BREACH), reason_code ('RT01'..'RT06'), credit_note_value_inr, disposition, status,
  sku_code, product_name, category, is_chilled, region_name, warehouse_code, route_code, channel,
  outlet_code, outlet_name, outlet_reportable

v_inventory      -- weekly Monday snapshots; use snapshot_date = (SELECT MAX(snapshot_date) FROM v_inventory) for "current"
  snapshot_date, warehouse_code, warehouse_name, region_id, sku_code, product_name, category, is_chilled,
  batch_id, on_hand_cases, on_hand_eaches, available_cases, days_of_cover, expiry_date, days_to_expiry,
  ageing_bucket, damaged_cases, blocked_cases, on_hand_value_inr

v_outlets        -- outlet_id, outlet_code, outlet_name, channel, city_clean, region_name, status, is_deleted,
  is_test, reportable, chiller_available, credit_limit_inr

products         -- product_id, sku_code, product_name, brand, category, subcategory, pack_size_value, pack_size_uom,
  case_pack, mrp_inr (today's), list_price_inr, is_chilled, status (ACTIVE/DISCONTINUED), discontinued_date
v_price_windows  -- product_id, sku_code, effective_from, effective_to, mrp_inr, list_price_inr (historic MRP)

cache.freight_invoices -- carrier bills (the only true freight cost). invoice_id, carrier_name, warehouse_code,
  route_code, service_date, service_month, amount_inr, distance_km, weight_kg, temperature_controlled, status.
  Join to v_order_service on warehouse_code + month only (no delivery id; route_code on invoices is unreliable).
cache.competitor_prices -- scraped shelf prices. listing_id, city (Mumbai/Delhi/Bengaluru/Chennai), retailer,
  title, category, price_inr, mrp_inr (retailer's), last_seen, product_id (matched Kestrel SKU or NULL),
  match_confidence

Conventions:
- Fiscal year runs April-March. fy_start_year=2026, fiscal_quarter=1 is Apr-Jun 2026 ("Q1 FY26-27").
- The data ends on {data_end}; "last month" = {last_month}, "last quarter" = {last_quarter_label}
  ({last_quarter_start} to {last_quarter_end}). "Last week" = the 7 days ending {data_end}.
- Regions: West, South, North, East, Central.{region_hint}
- Return ONLY the SQL, no prose, no code fences, no explanation. Keep it short. Round rates to 3 decimals. Add LIMIT 200 unless aggregating to few rows.
- Do not join v_order_service to v_outlets: v_order_service already carries outlet columns and outlet_reportable.
- Excursions per hundred chilled deliveries = 100.0*SUM(CASE WHEN has_chilled=1 THEN temperature_excursion_flag END)/SUM(has_chilled).
"""

FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|replace|vacuum)\b", re.I)


def _anthropic():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        return None
    return anthropic.Anthropic()


def _openai_compatible(system, user, max_tokens, attempts=4):
    """One chat call. Free tiers rate-limit per minute, so 429 is retried
    after the wait the server asks for."""
    for i in range(attempts):
        r = requests.post(
            f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {config.LLM_API_KEY}", "Content-Type": "application/json"},
            json={"model": config.LLM_MODEL, "max_tokens": max_tokens, "temperature": 0,
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
            timeout=90)
        if r.status_code == 429 and i < attempts - 1:
            m = re.search(r"try again in ([\d.]+)s", r.text)
            wait = float(m.group(1)) + 1 if m else float(r.headers.get("Retry-After", 10))
            time.sleep(min(wait, 60))
            continue
        if r.status_code != 200:
            raise RuntimeError(f"LLM endpoint returned {r.status_code}: {r.text[:300]}")
        choice = r.json()["choices"][0]
        if choice.get("finish_reason") == "length":
            raise RuntimeError("the model ran out of tokens before finishing; ask a narrower question")
        return choice["message"]["content"]


def complete(system, user, max_tokens=800):
    """One chat turn, whichever provider is configured."""
    client = _anthropic()
    if client is not None:
        msg = client.messages.create(model=config.ANTHROPIC_MODEL, max_tokens=max_tokens, system=system,
                                     messages=[{"role": "user", "content": user}])
        return msg.content[0].text
    if config.LLM_API_KEY and config.LLM_BASE_URL and config.LLM_MODEL:
        return _openai_compatible(system, user, max_tokens)
    return None


def llm_available():
    return _anthropic() is not None or bool(config.LLM_API_KEY and config.LLM_BASE_URL and config.LLM_MODEL)


def llm_name():
    if _anthropic() is not None:
        return f"Anthropic {config.ANTHROPIC_MODEL}"
    if llm_available():
        return config.LLM_MODEL
    return None


def _context(conn, region_name=None):
    from datetime import date
    from .dates import last_complete_quarter, quarter_bounds
    from .metrics import data_range
    _, end = data_range(conn)
    d = date.fromisoformat(end)
    y, q = last_complete_quarter(d)
    s, e = quarter_bounds(y, q)
    lm = (d.replace(day=1)).strftime("%Y-%m")
    return dict(
        in_full=config.IN_FULL_PCT, on_time=config.ON_TIME_TOLERANCE_MIN, data_end=end,
        last_month=lm, last_quarter_label=f"FY{y%100:02d}-{(y+1)%100:02d} Q{q}",
        last_quarter_start=s, last_quarter_end=e,
        region_hint=(f" The user is looking at the {region_name} region; filter region_name='{region_name}' "
                     "unless the question names another region." if region_name else ""),
    )


def extract_sql(text):
    """Models sometimes wrap the query in prose or a code fence despite being
    told not to. Take the fenced block if there is one, else everything from
    the first SELECT/WITH."""
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, flags=re.S | re.I)
    body = m.group(1) if m else text
    m = re.search(r"\b(WITH|SELECT)\b", body, flags=re.I)
    if not m:
        raise ValueError("the model did not return a SQL query")
    return body[m.start():].strip().rstrip(";").strip()


def generate_sql(conn, question, region_name=None):
    text = complete(SCHEMA.format(**_context(conn, region_name)), question, max_tokens=2500)
    if text is None:
        return None
    return extract_sql(text)


QUERY_TIMEOUT_S = int(os.environ.get("KESTREL_ASK_TIMEOUT_S", "45"))


def safe_run(conn, sql, limit=500, timeout=QUERY_TIMEOUT_S):
    """Run one read-only query with a wall-clock limit. A generated query can
    be pathological (correlated subqueries over 500k lines); interrupting it
    keeps the app responsive and tells the user to rephrase."""
    head = sql.lstrip().lower()
    if not (head.startswith("select") or head.startswith("with")):
        raise ValueError("only SELECT queries are allowed")
    if ";" in sql or FORBIDDEN.search(sql):
        raise ValueError("query contains a statement that is not allowed")
    timer = threading.Timer(timeout, conn.interrupt)
    timer.start()
    try:
        df = query(conn, sql)
    except Exception as e:
        if "interrupted" in str(e).lower():
            raise TimeoutError(f"query took longer than {timeout}s and was stopped; try a narrower question")
        raise
    finally:
        timer.cancel()
    return df.head(limit)


def narrate(question, sql, df):
    if df.empty:
        return None
    sample = df.head(40).to_csv(index=False)
    text = complete(
        "You explain query results to a supply chain director. Two to four plain sentences, "
        "lead with the answer, quote the actual numbers, mention the biggest item by name. "
        "No preamble, no bullet points, no restating the question.",
        f"Question: {question}\n\nSQL used:\n{sql}\n\nResult rows (CSV):\n{sample}", max_tokens=400)
    return text.strip() if text else None


# --------------------------------------------------------------- fallback

PREPARED = [
    ("lowest fill rate outlets last month",
     ["fill", "outlet"],
     """SELECT outlet_code, outlet_name, region_name, COUNT(*) AS orders,
               ROUND(SUM(delivered_cases)/SUM(ordered_cases), 3) AS fill_rate_cases
        FROM v_order_service WHERE outlet_reportable = 1 AND order_month = '{last_month}'
        GROUP BY 1,2,3 HAVING orders >= 3 ORDER BY fill_rate_cases LIMIT 5"""),
    ("OTIF by region, last complete quarter",
     ["otif", "region"],
     """SELECT region_name, COUNT(*) AS orders, ROUND(AVG(otif),3) AS otif,
               ROUND(AVG(on_time),3) AS on_time, ROUND(AVG(in_full),3) AS in_full
        FROM v_order_service WHERE outlet_reportable = 1
          AND order_date BETWEEN '{last_quarter_start}' AND '{last_quarter_end}'
        GROUP BY 1 ORDER BY otif"""),
    ("returns value by category with leading reason",
     ["return", "categor"],
     """SELECT category, ROUND(SUM(credit_note_value_inr)) AS value_inr, COUNT(*) AS credit_notes,
               (SELECT return_reason_code FROM v_returns r2 WHERE r2.category = r.category AND r2.outlet_reportable = 1
                GROUP BY 1 ORDER BY SUM(credit_note_value_inr) DESC LIMIT 1) AS leading_reason
        FROM v_returns r WHERE outlet_reportable = 1 GROUP BY 1 ORDER BY value_inr DESC"""),
    ("temperature excursions per 100 chilled deliveries by month",
     ["excursion", "month"],
     """SELECT order_month, SUM(has_chilled) AS chilled_deliveries,
               SUM(CASE WHEN has_chilled = 1 THEN temperature_excursion_flag ELSE 0 END) AS excursions,
               ROUND(100.0*SUM(CASE WHEN has_chilled = 1 THEN temperature_excursion_flag ELSE 0 END)/SUM(has_chilled), 2) AS per_100
        FROM v_order_service WHERE outlet_reportable = 1 GROUP BY 1 ORDER BY 1"""),
    ("routes more than two hours late on more than one delivery in ten",
     ["route", "late"],
     """SELECT route_code, warehouse_code, COUNT(*) AS deliveries, SUM(late_2h) AS late_over_2h,
               ROUND(SUM(late_2h)*1.0/COUNT(*), 3) AS share
        FROM v_order_service WHERE outlet_reportable = 1
          AND order_date BETWEEN '{last_quarter_start}' AND '{last_quarter_end}'
        GROUP BY 1,2 HAVING share > 0.1 ORDER BY share DESC"""),
    ("top 20 SKUs by value: our MRP vs lowest competitor price in Mumbai",
     ["mrp", "mumbai"],
     """WITH top AS (
            SELECT l.product_id, SUM(l.delivered_value_inr) AS value_inr
            FROM v_order_lines l JOIN v_orders o ON o.order_id = l.order_id
            WHERE o.outlet_reportable = 1 AND o.order_status IN ('DELIVERED','PARTIAL')
              AND o.order_date BETWEEN '{last_quarter_start}' AND '{last_quarter_end}'
            GROUP BY 1 ORDER BY 2 DESC LIMIT 20)
        SELECT p.sku_code, p.product_name, ROUND(top.value_inr) AS value_inr, p.mrp_inr,
               MIN(c.price_inr) AS lowest_mumbai_price, ROUND((p.mrp_inr - MIN(c.price_inr))/p.mrp_inr, 3) AS gap_pct
        FROM top JOIN products p ON p.product_id = top.product_id
        LEFT JOIN cache.competitor_prices c ON c.product_id = p.product_id AND c.city = 'Mumbai'
        GROUP BY 1,2,3,4 ORDER BY value_inr DESC"""),
    ("freight cost per delivered case by warehouse, last quarter",
     ["freight", "warehouse"],
     """WITH cases AS (
            SELECT warehouse_code, order_month AS month, SUM(delivered_cases) AS cases
            FROM v_order_service WHERE outlet_reportable = 1
              AND order_date BETWEEN '{last_quarter_start}' AND '{last_quarter_end}' GROUP BY 1,2),
        freight AS (
            SELECT warehouse_code, service_month AS month, SUM(amount_inr) AS freight_inr
            FROM cache.freight_invoices
            WHERE service_date BETWEEN '{last_quarter_start}' AND '{last_quarter_end}' GROUP BY 1,2)
        SELECT c.warehouse_code, ROUND(SUM(f.freight_inr)) AS freight_inr, ROUND(SUM(c.cases)) AS cases,
               ROUND(SUM(f.freight_inr)/SUM(c.cases), 2) AS freight_per_case
        FROM cases c JOIN freight f USING (warehouse_code, month)
        GROUP BY 1 ORDER BY freight_per_case DESC"""),
    ("outlets that ordered a discontinued SKU after its discontinuation date",
     ["discontinu"],
     """SELECT o.outlet_code, o.outlet_name, l.sku_code, p.discontinued_date,
               COUNT(*) AS lines, MAX(o.order_date) AS last_order
        FROM v_order_lines l JOIN v_orders o ON o.order_id = l.order_id JOIN products p ON p.product_id = l.product_id
        WHERE p.status = 'DISCONTINUED' AND o.order_date > p.discontinued_date AND o.outlet_reportable = 1
        GROUP BY 1,2,3,4 ORDER BY lines DESC LIMIT 50"""),
]


def prepared_questions():
    return [p[0] for p in PREPARED]


def prepared_sql(conn, question, region_name=None):
    q = question.lower()
    best, score = None, 0
    for label, keys, sql in PREPARED:
        s = sum(1 for k in keys if k in q)
        if s > score:
            best, score = sql, s
    if best is None:
        return None
    ctx = _context(conn, region_name)
    sql = best.format(**ctx)
    if region_name:
        sql = sql.replace("outlet_reportable = 1", f"outlet_reportable = 1 AND region_name = '{region_name}'")
    return sql


def answer(conn, question, region_name=None):
    out = {"question": question, "sql": None, "df": None, "narrative": None, "error": None, "mode": None}
    try:
        if llm_available():
            out["mode"] = "llm"
            out["sql"] = generate_sql(conn, question, region_name)
        else:
            out["mode"] = "prepared"
            out["sql"] = prepared_sql(conn, question, region_name)
            if out["sql"] is None:
                out["error"] = ("No ANTHROPIC_API_KEY set, so only the prepared questions work. "
                                "Pick one below or set the key and restart.")
                return out
        out["df"] = safe_run(conn, out["sql"])
        if out["mode"] == "llm":
            out["narrative"] = narrate(question, out["sql"], out["df"])
    except Exception as e:  # surfaced in the UI, never swallowed
        out["error"] = f"{e.__class__.__name__}: {e}"
    return out
