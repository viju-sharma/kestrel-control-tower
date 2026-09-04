-- Clean layer. Every view is TEMP so the client's database is never modified.
-- {on_time_min} and {in_full_pct} are substituted at connect time from config.

-- Outlets: flag test rows and normalise city spellings. `reportable` is what
-- every ranking uses: active, not soft-deleted, not a test/migration record.
CREATE TEMP VIEW v_outlets AS
SELECT
    o.*,
    r.region_name,
    CASE WHEN o.outlet_code LIKE 'TST%'
           OR upper(o.outlet_name) LIKE '%TEST%'
           OR upper(o.outlet_name) LIKE '%DUMMY%'
           OR upper(o.outlet_name) LIKE '%MIGRATION%'
           OR upper(o.outlet_name) LIKE '%DO NOT USE%'
         THEN 1 ELSE 0 END AS is_test,
    CASE o.city
        WHEN 'Bangalore' THEN 'Bengaluru'
        WHEN 'New Delhi' THEN 'Delhi'
        ELSE o.city END AS city_clean,
    CASE WHEN o.status = 'ACTIVE' AND o.is_deleted = 0
          AND NOT (o.outlet_code LIKE 'TST%'
                   OR upper(o.outlet_name) LIKE '%TEST%'
                   OR upper(o.outlet_name) LIKE '%DUMMY%'
                   OR upper(o.outlet_name) LIKE '%MIGRATION%'
                   OR upper(o.outlet_name) LIKE '%DO NOT USE%')
         THEN 1 ELSE 0 END AS reportable
FROM outlets o
LEFT JOIN regions r ON r.region_id = o.region_id;

-- Order lines with both unit systems side by side. qty_uom is per line, so
-- summing raw quantities mixes cases and eaches (ticket KP-2340).
CREATE TEMP VIEW v_order_lines AS
SELECT
    ol.*,
    p.sku_code, p.product_name, p.category, p.subcategory, p.brand, p.is_chilled,
    CASE ol.qty_uom WHEN 'EACH' THEN ol.ordered_qty   / NULLIF(ol.case_pack_at_order, 0) ELSE ol.ordered_qty   END AS ordered_cases,
    CASE ol.qty_uom WHEN 'EACH' THEN ol.delivered_qty / NULLIF(ol.case_pack_at_order, 0) ELSE ol.delivered_qty END AS delivered_cases,
    CASE ol.qty_uom WHEN 'EACH' THEN ol.ordered_qty   ELSE ol.ordered_qty   * ol.case_pack_at_order END AS ordered_eaches,
    CASE ol.qty_uom WHEN 'EACH' THEN ol.delivered_qty ELSE ol.delivered_qty * ol.case_pack_at_order END AS delivered_eaches,
    -- value actually shipped, pro-rated from the line value
    CASE WHEN ol.ordered_qty > 0 THEN ol.line_value_inr * ol.delivered_qty / ol.ordered_qty ELSE 0 END AS delivered_value_inr
FROM order_lines ol
JOIN products p ON p.product_id = ol.product_id;

-- Orders with fiscal calendar, normalised created_at (IST) and outlet quality.
CREATE TEMP VIEW v_orders AS
SELECT
    o.*,
    parse_ts(o.created_at) AS created_at_ist,
    substr(o.order_date, 1, 7) AS order_month,
    CASE WHEN CAST(substr(o.order_date, 6, 2) AS INT) >= 4
         THEN CAST(substr(o.order_date, 1, 4) AS INT)
         ELSE CAST(substr(o.order_date, 1, 4) AS INT) - 1 END AS fy_start_year,
    ((CAST(substr(o.order_date, 6, 2) AS INT) - 4 + 12) % 12) / 3 + 1 AS fiscal_quarter,
    rg.region_name,
    w.warehouse_code, w.warehouse_name,
    rt.route_code,
    t.outlet_code, t.outlet_name, t.city_clean AS outlet_city,
    t.is_test AS outlet_is_test, t.status AS outlet_status, t.is_deleted AS outlet_deleted,
    t.reportable AS outlet_reportable
FROM orders o
JOIN v_outlets t   ON t.outlet_id = o.outlet_id
LEFT JOIN regions rg    ON rg.region_id = o.region_id
LEFT JOIN warehouses w  ON w.warehouse_id = o.warehouse_id
LEFT JOIN routes rt     ON rt.route_id = o.route_id;

-- Deliveries with a parsed arrival and an on-time flag. delay_minutes is the
-- vendor's own figure; it does not reconcile to the timestamps (see DATA_NOTES.md)
-- so we use it as reported.
CREATE TEMP VIEW v_deliveries AS
SELECT
    d.*,
    parse_ts(d.actual_arrival) AS actual_arrival_ist,
    CASE WHEN d.delay_minutes <= {on_time_min} THEN 1 ELSE 0 END AS on_time,
    CASE WHEN d.delay_minutes > 120 THEN 1 ELSE 0 END AS late_2h,
    rt.route_code, rt.is_reefer,
    w.warehouse_code
FROM deliveries d
LEFT JOIN routes rt    ON rt.route_id = d.route_id
LEFT JOIN warehouses w ON w.warehouse_id = d.warehouse_id;

-- One row per order that reached fulfilment: the grain for fill rate and OTIF.
-- in_full uses a tolerance because no order in this data is exactly complete.
-- Cancelled and still-open orders are excluded; they have not been served yet.
CREATE TEMP VIEW v_order_service AS
SELECT
    o.order_id, o.order_number, o.order_date, o.order_month, o.fy_start_year, o.fiscal_quarter,
    o.region_id, o.region_name, o.warehouse_id, o.warehouse_code, o.warehouse_name,
    o.route_id, o.route_code, o.outlet_id, o.outlet_code, o.outlet_name, o.outlet_city,
    o.channel, o.source_system, o.order_status, o.outlet_reportable,
    l.ordered_cases, l.delivered_cases, l.ordered_eaches, l.delivered_eaches,
    l.order_value_inr, l.delivered_value_inr, l.short_lines, l.line_count,
    CASE WHEN l.delivered_cases >= l.ordered_cases * {in_full_pct} / 100.0 THEN 1 ELSE 0 END AS in_full,
    d.delivery_id, d.delay_minutes, d.on_time, d.late_2h,
    d.temperature_excursion_flag, d.max_temp_celsius, d.returned_cases, d.pod_captured,
    CASE WHEN l.delivered_cases >= l.ordered_cases * {in_full_pct} / 100.0 AND d.on_time = 1 THEN 1 ELSE 0 END AS otif,
    l.has_chilled
FROM v_orders o
JOIN (
    SELECT order_id,
           SUM(ordered_cases)   AS ordered_cases,
           SUM(delivered_cases) AS delivered_cases,
           SUM(ordered_eaches)  AS ordered_eaches,
           SUM(delivered_eaches) AS delivered_eaches,
           SUM(line_value_inr)  AS order_value_inr,
           SUM(delivered_value_inr) AS delivered_value_inr,
           SUM(CASE WHEN delivered_qty < ordered_qty THEN 1 ELSE 0 END) AS short_lines,
           COUNT(*) AS line_count,
           MAX(is_chilled) AS has_chilled
    FROM v_order_lines GROUP BY order_id
) l ON l.order_id = o.order_id
LEFT JOIN v_deliveries d ON d.order_id = o.order_id
WHERE o.order_status IN ('DELIVERED', 'PARTIAL');

-- Credit notes with a consistent sign and both units (KP-2402).
CREATE TEMP VIEW v_returns AS
SELECT
    r.return_id, r.credit_note_number, r.order_id, r.order_line_id, r.outlet_id, r.product_id,
    r.return_date, substr(r.return_date, 1, 7) AS return_month,
    CASE WHEN CAST(substr(r.return_date, 6, 2) AS INT) >= 4
         THEN CAST(substr(r.return_date, 1, 4) AS INT)
         ELSE CAST(substr(r.return_date, 1, 4) AS INT) - 1 END AS fy_start_year,
    ((CAST(substr(r.return_date, 6, 2) AS INT) - 4 + 12) % 12) / 3 + 1 AS fiscal_quarter,
    abs(r.return_qty) AS return_qty,
    r.qty_uom,
    CASE r.qty_uom WHEN 'EACH' THEN abs(r.return_qty) / NULLIF(ol.case_pack_at_order, 0) ELSE abs(r.return_qty) END AS return_cases,
    r.return_reason_code,
    substr(r.return_reason_code, 1, 4) AS reason_code,
    r.credit_note_value_inr,
    r.disposition, r.status, r.approved_by,
    p.sku_code, p.product_name, p.category, p.subcategory, p.is_chilled,
    o.region_id, o.region_name, o.warehouse_id, o.warehouse_code, o.route_id, o.route_code,
    o.channel, o.outlet_code, o.outlet_name, o.outlet_reportable
FROM returns_credit_notes r
LEFT JOIN order_lines ol ON ol.order_line_id = r.order_line_id
LEFT JOIN products p     ON p.product_id = r.product_id
LEFT JOIN v_orders o     ON o.order_id = r.order_id;

-- Inventory with time to expiry. Weekly snapshots, so "current" = latest date.
CREATE TEMP VIEW v_inventory AS
SELECT
    i.*,
    w.warehouse_code, w.warehouse_name, w.region_id,
    p.sku_code, p.product_name, p.category, p.is_chilled, p.list_price_inr, p.case_pack,
    CAST(julianday(i.expiry_date) - julianday(i.snapshot_date) AS INT) AS days_to_expiry,
    i.on_hand_eaches * p.list_price_inr AS on_hand_value_inr
FROM inventory_snapshots i
JOIN warehouses w ON w.warehouse_id = i.warehouse_id
JOIN products p   ON p.product_id = i.product_id;

-- Products with the MRP that was valid on a given date via product_price_history.
-- (products.mrp_inr is today's price only.)
CREATE TEMP VIEW v_price_windows AS
SELECT h.product_id, p.sku_code, h.effective_from,
       COALESCE(h.effective_to, '9999-12-31') AS effective_to,
       h.mrp_inr, h.list_price_inr
FROM product_price_history h
JOIN products p ON p.product_id = h.product_id;
