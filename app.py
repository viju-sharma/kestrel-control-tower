"""Kestrel control tower. Run with: streamlit run app.py"""
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from kestrel import ask, config
from kestrel import metrics as m
from kestrel.dates import last_complete_quarter, quarter_bounds, quarters_in_range
from kestrel.db import connect

st.set_page_config(page_title="Kestrel control tower", page_icon="🗼", layout="wide")


@st.cache_resource
def get_conn():
    return connect()


try:
    conn = get_conn()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

# ----------------------------------------------------------------- sidebar
data_start, data_end = m.data_range(conn)
data_start, data_end = date.fromisoformat(data_start), date.fromisoformat(data_end)
regions = m.regions(conn)

with st.sidebar:
    st.title("Kestrel control tower")
    st.caption(f"Data {data_start} to {data_end}")

    region_label = st.selectbox("View", ["All regions"] + [f"{r.region_name} ({r.regional_manager})"
                                                          for r in regions.itertuples()])
    region_id = region_name = None
    if region_label != "All regions":
        r = regions.iloc[[f"{x.region_name} ({x.regional_manager})" for x in regions.itertuples()].index(region_label)]
        region_id, region_name = int(r.region_id), r.region_name

    quarters = quarters_in_range(data_start, data_end)
    complete = [q for q in quarters if q[4] <= data_end]
    period_opts = [q[2] for q in complete] + ["Last month", "Last 3 months", "Custom"]
    ly, lq = last_complete_quarter(data_end)
    default = f"FY{ly%100:02d}-{(ly+1)%100:02d} Q{lq}"
    period = st.selectbox("Period", period_opts, index=period_opts.index(default))
    if period == "Last month":
        start = data_end.replace(day=1); end = data_end
    elif period == "Last 3 months":
        start = (data_end.replace(day=1) - timedelta(days=62)).replace(day=1); end = data_end
    elif period == "Custom":
        start, end = st.date_input("Range", (data_end - timedelta(days=90), data_end),
                                   min_value=data_start, max_value=data_end)
    else:
        start, end = next((q[3], q[4]) for q in complete if q[2] == period)
    start, end = max(start, data_start), min(end, data_end)

    unit = st.radio("Fill rate unit", ["cases", "eaches"], horizontal=True,
                    help="Ops commits in cases; modern trade penalises in eaches. Both are computed, pick the lens.")
    st.caption(f"On time = delay ≤ {config.ON_TIME_TOLERANCE_MIN} min. In full = case fill ≥ {config.IN_FULL_PCT:g}%. "
               "Both are knobs (see README).")

fill_col = "fill_cases" if unit == "cases" else "fill_eaches"
scope = f"{region_name or 'All regions'} · {period} ({start} to {end})"


def pct(x, d=1):
    return "–" if x is None or pd.isna(x) else f"{100*x:.{d}f}%"


def inr(x):
    if x is None or pd.isna(x):
        return "–"
    if abs(x) >= 1e7:
        return f"₹{x/1e7:,.2f} Cr"
    if abs(x) >= 1e5:
        return f"₹{x/1e5:,.1f} L"
    return f"₹{x:,.0f}"


def fmt_rate_cols(df, cols):
    df = df.copy()
    for c in cols:
        if c in df:
            df[c] = df[c].map(lambda v: pct(v))
    return df


# ------------------------------------------------------------------- tabs
tab_over, tab_service, tab_cold, tab_money, tab_price, tab_ask, tab_notes = st.tabs(
    ["Overview", "Service", "Cold chain", "Money", "Price position", "Ask", "Data notes"])

with tab_over:
    st.subheader(scope)
    s = m.service_summary(conn, start, end, region_id)
    r = m.returns_summary(conn, start, end, region_id)
    ex = m.excursions_by_month(conn, start, end, region_id)
    ne = m.near_expiry_total(conn, 30, region_id)
    fr = m.freight_per_case(conn, "warehouse", start, end, region_id)

    c = st.columns(6)
    c[0].metric(f"Fill rate ({unit})", pct(s[fill_col]),
                help=f"Other lens: {pct(s['fill_eaches' if unit == 'cases' else 'fill_cases'])}")
    c[1].metric("OTIF", pct(s["otif"]), help=f"On time {pct(s['on_time'])} · in full {pct(s['in_full'])}")
    per100 = (100 * ex["excursions"].sum() / ex["chilled_deliveries"].sum()) if ex["chilled_deliveries"].sum() else None
    c[2].metric("Excursions / 100 chilled", "–" if per100 is None else f"{per100:.2f}")
    c[3].metric("Returns % of dispatch", pct(r["returns_pct"], 2), help=f"{inr(r['value_inr'])} of {inr(r['dispatch_value_inr'])}")
    if fr.empty:
        c[4].metric("Freight / case", "not synced", help="Run scripts/sync_freight.py")
    else:
        c[4].metric("Freight / case", f"₹{fr['freight_inr'].sum()/fr['cases'].sum():,.0f}")
    c[5].metric("Near-expiry stock (30d)", inr(ne["value_inr"]), help=f"{ne['cases']:,.0f} cases at {ne['snapshot_date']}")

    st.markdown("#### Worst performers")
    w1, w2, w3 = st.columns(3)
    with w1:
        st.caption("Outlets by fill rate (min 5 orders)")
        df = m.worst(conn, "outlet", fill_col, start, end, region_id, n=5)
        st.dataframe(fmt_rate_cols(df[["label", "orders", fill_col, "otif"]], [fill_col, "otif"]),
                     hide_index=True, use_container_width=True)
    with w2:
        st.caption("Routes by OTIF")
        df = m.worst(conn, "route", "otif", start, end, region_id, n=5)
        st.dataframe(fmt_rate_cols(df[["label", "orders", "otif", "avg_delay_min"]], ["otif"]).round(0),
                     hide_index=True, use_container_width=True)
    with w3:
        st.caption("Warehouses by fill rate")
        df = m.worst(conn, "warehouse", fill_col, start, end, region_id, n=8)
        st.dataframe(fmt_rate_cols(df[["label", "orders", fill_col, "otif"]], [fill_col, "otif"]),
                     hide_index=True, use_container_width=True)

    st.markdown("#### Routes late by more than 2 hours on more than 1 delivery in 10")
    lr = m.late_routes(conn, start, end, region_id)
    st.dataframe(fmt_rate_cols(lr, ["late_share"]).round(0), hide_index=True, use_container_width=True, height=220)

with tab_service:
    st.subheader(f"Service · {scope}")
    dim = st.radio("Break down by", ["region", "warehouse", "route", "outlet", "channel", "source"], horizontal=True)
    df = m.service_by(conn, dim, start, end, region_id, min_orders=3)
    st.bar_chart(df.set_index("label")[[fill_col, "otif"]].sort_values(fill_col), horizontal=True,
                 height=min(700, 60 + 22 * len(df)))
    st.dataframe(fmt_rate_cols(df.drop(columns=["key"]), ["fill_cases", "fill_eaches", "on_time", "in_full", "otif", "late_2h_share"]).round(0),
                 hide_index=True, use_container_width=True)
    st.markdown("#### Trend by month")
    tr = m.service_by(conn, "month", start, end, region_id)
    st.line_chart(tr.set_index("label")[["fill_cases", "fill_eaches", "otif", "on_time"]])
    st.markdown("#### Why we shipped short")
    st.dataframe(m.short_reasons(conn, start, end, region_id).round(0), hide_index=True, use_container_width=True)

with tab_cold:
    st.subheader(f"Cold chain · {scope}")
    ex = m.excursions_by_month(conn, start, end, region_id)
    a, b = st.columns([2, 1])
    a.line_chart(ex.set_index("month")["per_100_chilled"])
    b.dataframe(ex.round(2), hide_index=True, use_container_width=True)
    dim = st.radio("Excursions by", ["warehouse", "route", "region", "channel"], horizontal=True, key="cc_dim")
    st.dataframe(m.excursions_by(conn, dim, start, end, region_id).round(2).drop(columns=["key"]),
                 hide_index=True, use_container_width=True, height=260)
    st.markdown("#### Near-expiry stock")
    days = st.slider("Expiring within (days)", 7, 90, 30)
    ne = m.near_expiry(conn, days, region_id)
    st.caption(f"Latest snapshot {m.latest_snapshot_date(conn)} · {ne['cases'].sum():,.0f} cases · {inr(ne['value_inr'].sum())} at list price")
    st.dataframe(ne.round(0), hide_index=True, use_container_width=True, height=260)
    st.markdown("#### Returns coded as cold chain breach (RT06)")
    st.dataframe(m.cold_chain_returns(conn, start, end, region_id).round(0), hide_index=True, use_container_width=True)

with tab_money:
    st.subheader(f"Money · {scope}")
    r = m.returns_summary(conn, start, end, region_id)
    c = st.columns(4)
    c[0].metric("Credit notes", f"{r['credit_notes']:,.0f}")
    c[1].metric("Credit value", inr(r["value_inr"]))
    c[2].metric("% of dispatch value", pct(r["returns_pct"], 2))
    c[3].metric("Of which cold chain", inr(r["cold_chain_value_inr"]))
    a, b = st.columns(2)
    with a:
        st.caption("Returns by category, with the reason that drives it")
        st.dataframe(fmt_rate_cols(m.returns_category_reason(conn, start, end, region_id), ["leading_reason_share"]).round(0),
                     hide_index=True, use_container_width=True)
    with b:
        st.caption("Leakage: credit value as % of delivered value")
        st.dataframe(fmt_rate_cols(m.returns_share_by_category(conn, start, end, region_id), ["returns_pct"]).round(0),
                     hide_index=True, use_container_width=True)
    st.markdown("#### Freight cost per delivered case")
    cov = m.freight_coverage(conn)
    if cov.empty:
        st.info("No carrier invoices synced yet. Run `python scripts/sync_freight.py` with the partner API up. "
                "The driver-entered fuel_cost_inr in deliveries is not the billed amount, so we do not show it.")
    else:
        st.caption("Invoices synced for: " + ", ".join(cov["service_month"]) +
                   ". Joined to deliveries on warehouse + month: invoices carry no delivery id and their route codes are unreliable (see Data notes).")
        dim = st.radio("Freight by", ["warehouse", "carrier", "region", "month"], horizontal=True, key="fr_dim")
        fr = m.freight_per_case(conn, dim, start, end, region_id)
        if fr.empty:
            st.warning("No invoices in this window. Sync it first.")
        else:
            st.bar_chart(fr.set_index(fr.columns[0])["freight_per_case"], horizontal=True, height=min(600, 60 + 22 * len(fr)))
            st.dataframe(fr.round(2), hide_index=True, use_container_width=True)

with tab_price:
    st.subheader("Price position · Kestrel MRP vs lowest competitor shelf price")
    summ = m.price_gap_summary(conn, config.PRICE_FRESHNESS_DAYS)
    if summ.empty:
        st.info("No competitor prices yet. Serve bazaarpulse_site on :8080 and run `python scripts/scrape_prices.py`.")
    else:
        st.caption(f"Listings seen in the last {config.PRICE_FRESHNESS_DAYS} days only. "
                   "Gap = (our MRP − lowest observed price) / MRP; positive means the shelf is under our MRP.")
        pv = summ.pivot(index="category", columns="city", values="median_gap_pct")
        st.dataframe(pv.map(lambda v: pct(v)), use_container_width=True)
        cities = sorted(summ["city"].unique())
        a, b = st.columns(2)
        city = a.selectbox("City", cities, index=cities.index("Mumbai") if "Mumbai" in cities else 0)
        n = b.slider("Top SKUs by value", 5, 50, 20)
        top = m.top_skus_by_value(conn, start, end, n, region_id)
        gap = m.price_gap(conn, city=city, freshness_days=config.PRICE_FRESHNESS_DAYS)
        df = top.merge(gap[["sku_code", "lowest_price_inr", "listings", "gap_inr", "gap_pct", "match_confidence"]],
                       on="sku_code", how="left")
        df = df.merge(pd.read_sql_query("SELECT sku_code, mrp_inr FROM products", conn), on="sku_code")
        df = df[["sku_code", "product_name", "category", "value_inr", "mrp_inr", "lowest_price_inr", "gap_pct", "listings", "match_confidence"]]
        st.caption(f"Top {n} SKUs by delivered value in the selected period, against {city}. Blank = no listing matched.")
        st.dataframe(fmt_rate_cols(df, ["gap_pct"]).round(0), hide_index=True, use_container_width=True)
        with st.expander("All matched listings"):
            st.dataframe(m.price_gap(conn, freshness_days=365).round(2), hide_index=True, use_container_width=True)

with tab_ask:
    st.subheader("Ask")
    mode = "Claude writes the SQL" if ask.llm_available() else "No ANTHROPIC_API_KEY: prepared questions only"
    st.caption(f"{mode}. Every answer shows the SQL and the rows it came from. "
               + (f"Scoped to {region_name}." if region_name else ""))
    qs = ask.prepared_questions()
    pick = st.selectbox("Try one", ["…"] + qs)
    q = st.text_input("Or type your own", value="" if pick == "…" else pick,
                      placeholder="why did fill rate drop in the West last week")
    if q:
        with st.spinner("Working…"):
            res = ask.answer(conn, q, region_name)
        if res["error"]:
            st.error(res["error"])
        if res["narrative"]:
            st.markdown(res["narrative"])
        if res["df"] is not None:
            st.dataframe(res["df"], hide_index=True, use_container_width=True)
            if len(res["df"]) > 1 and res["df"].select_dtypes("number").shape[1] >= 1:
                num = res["df"].select_dtypes("number").columns[-1]
                lab = res["df"].columns[0]
                if res["df"][lab].dtype == object and len(res["df"]) <= 60:
                    st.bar_chart(res["df"].set_index(lab)[num], horizontal=True, height=min(500, 60 + 20 * len(res["df"])))
        if res["sql"]:
            with st.expander("SQL"):
                st.code(res["sql"], language="sql")

with tab_notes:
    notes = Path(__file__).parent / "docs" / "DATA_NOTES.md"
    st.markdown(notes.read_text() if notes.exists() else "docs/DATA_NOTES.md not found")
