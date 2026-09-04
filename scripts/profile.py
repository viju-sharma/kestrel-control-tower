#!/usr/bin/env python
"""Re-run the data checks behind docs/DATA_NOTES.md and print the figures."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kestrel.db import connect, query  # noqa: E402

CHECKS = [
    ("lines by unit", "SELECT qty_uom, COUNT(*) n FROM order_lines GROUP BY 1"),
    ("lines delivered in full", "SELECT SUM(delivered_qty >= ordered_qty) full_lines, COUNT(*) lines FROM order_lines"),
    ("orders in full at 90% case fill", "SELECT SUM(in_full) in_full, COUNT(*) orders FROM v_order_service"),
    ("delay_minutes vs timestamps", """SELECT telematics_vendor, COUNT(*) n,
        SUM(abs(delay_minutes - round((julianday(parse_ts(actual_arrival)) - julianday(planned_arrival))*1440)) > 1) disagree
        FROM deliveries GROUP BY 1"""),
    ("deliveries over 2h late", "SELECT ROUND(AVG(late_2h),3) share, ROUND(AVG(delay_minutes)) avg_delay FROM v_order_service"),
    ("test outlets and their orders", """SELECT t.outlet_code, t.outlet_name, COUNT(o.order_id) orders
        FROM v_outlets t LEFT JOIN orders o ON o.outlet_id = t.outlet_id WHERE t.is_test = 1 GROUP BY 1,2"""),
    ("orders on deleted / closed-after-close outlets", """SELECT
        SUM(t.is_deleted = 1) deleted, SUM(t.status='CLOSED' AND o.order_date > t.closed_date) after_close
        FROM orders o JOIN outlets t ON t.outlet_id = o.outlet_id"""),
    ("phone shared by two outlet codes", """SELECT contact_phone, GROUP_CONCAT(outlet_code) codes FROM outlets
        WHERE outlet_code NOT LIKE 'TST%' GROUP BY 1 HAVING COUNT(*) > 1"""),
    ("city spellings", "SELECT city, COUNT(*) n FROM outlets WHERE city IN ('Bangalore','Bengaluru','Delhi','New Delhi') GROUP BY 1"),
    ("header net vs gross-discount+tax, by source", """SELECT source_system,
        ROUND(AVG(order_value_net_inr / (order_value_gross_inr - discount_amount_inr + tax_amount_inr)), 4) ratio
        FROM orders GROUP BY 1"""),
    ("negative return quantities", "SELECT SUM(return_qty < 0) negative, COUNT(*) total FROM returns_credit_notes"),
    ("returns larger than delivered", """SELECT COUNT(*) n FROM returns_credit_notes r
        JOIN order_lines l ON l.order_line_id = r.order_line_id WHERE abs(r.return_qty) > l.delivered_qty"""),
    ("created_at formats", "SELECT source_system, MIN(created_at) example FROM orders GROUP BY 1"),
    ("lines inside a closed price window", """SELECT COUNT(*) n FROM order_lines l JOIN orders o ON o.order_id = l.order_id
        JOIN product_price_history h ON h.product_id = l.product_id AND h.effective_to IS NOT NULL
        AND o.order_date BETWEEN h.effective_from AND h.effective_to"""),
    ("chilled lines to outlets without a chiller", """SELECT COUNT(*) lines, COUNT(DISTINCT o.outlet_id) outlets
        FROM order_lines l JOIN products p ON p.product_id = l.product_id JOIN orders o ON o.order_id = l.order_id
        JOIN outlets t ON t.outlet_id = o.outlet_id WHERE p.is_chilled = 1 AND t.chiller_available = 0"""),
    ("RT06 returns vs excursion flag on the delivery", """SELECT d.temperature_excursion_flag flag, COUNT(*) n
        FROM returns_credit_notes r LEFT JOIN deliveries d ON d.order_id = r.order_id
        WHERE r.return_reason_code LIKE 'RT06%' GROUP BY 1"""),
    ("discontinued SKUs ordered after discontinuation", """SELECT COUNT(DISTINCT p.product_id) skus, COUNT(*) lines
        FROM order_lines l JOIN orders o ON o.order_id = l.order_id JOIN products p ON p.product_id = l.product_id
        WHERE p.status = 'DISCONTINUED' AND o.order_date > p.discontinued_date"""),
    ("outlets assigned to exited salespeople", """SELECT COUNT(*) n FROM outlets t
        JOIN salespeople s ON s.salesperson_id = t.salesperson_id WHERE s.date_of_exit IS NOT NULL"""),
    ("open orders with delivered quantity", """SELECT COUNT(DISTINCT l.order_id) n FROM order_lines l
        JOIN orders o ON o.order_id = l.order_id WHERE o.order_status = 'OPEN' AND l.delivered_qty > 0"""),
    ("credit note approval_date populated", "SELECT SUM(approval_date IS NOT NULL) n FROM returns_credit_notes"),
    ("invoice route code belongs to invoice warehouse", """SELECT COUNT(*) n, SUM(w.warehouse_code = f.warehouse_code) agree
        FROM cache.freight_invoices f JOIN routes r ON r.route_code = f.route_code
        JOIN warehouses w ON w.warehouse_id = r.warehouse_id"""),
    ("competitor listings matched", "SELECT COUNT(*) listings, SUM(product_id IS NOT NULL) matched, SUM(match_confidence < 1) low_conf FROM cache.competitor_prices"),
]


def main():
    conn = connect()
    for label, sql in CHECKS:
        print(f"## {label}")
        try:
            print(query(conn, sql).to_string(index=False))
        except Exception as e:
            print(f"(failed: {e})")
        print()


if __name__ == "__main__":
    main()
