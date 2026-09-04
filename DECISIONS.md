# Decisions

A running log, kept as the work went. Newest at the bottom.

## Going in
- Six hours, five asks. Depth on service, cold chain and money; price position and ask-anything if time allows.
- Streamlit, one command. Divya's only hard constraint: "if it does not open, I will not use it".
- Database opened read-only. Clean on read, never write to the client's file.
- Front page = fiscal Q1 (Apr–Jun 2026). The year runs April–March; the board asks for Q1 first.

## After profiling the data
- **Cases vs eaches: both.** From `case_pack_at_order` on every line. Cases default (Divya's screen); eaches a toggle (Rakesh's customers penalise in units).
- **In full cannot be strict.** All 511,516 lines are short by a little; strict OTIF is 0% everywhere. In full = case fill ≥ 90%, printed on screen, env var.
- **On time = delay ≤ 30 min.** No tolerance in the brief. `delay_minutes` disagrees with the timestamps on 87% of rows for both vendors; I use the vendor's figure and say so.
- **Orders that count:** DELIVERED and PARTIAL. Cancelled were never served; OPEN are not finished.
- **Outlets that count:** active, not soft-deleted, not test. Three test outlets carry 260 orders.
- **Value from lines, never headers.** Gross ties; the partner feed's net is inflated by exactly 8.5%. The open ticket blames the wrong column.

## External sources
- **Freight = billed amount from the partner API.** Driver-entered `fuel_cost_inr` is not shown. One quarter by default, cached, resumable.
- **Freight joins on warehouse + month, not route.** 87% of invoices name a route from another warehouse; a third of spend landed on route-level cells with deliveries. No route-level freight.
- **Listing cards only.** They carry price, MRP, stock, last-seen. Detail pages are 19 more minutes at the 1s crawl-delay for a chart nobody asked for. `/internal/` is disallowed; the client refuses to fetch it.
- **Price gap = today's MRP vs lowest price seen in 14 days, per city.** Shelf price is a floor question.
- **SKU matching is rules:** brand alias + type words + pack size. Unit slips (400kg vs 400g) accepted at lower confidence; the product master has the same slips.
- **Weather and holidays: not used.** No metric they would explain in the time.

## The screen
- Overview leads with six numbers and the worst outlets, routes, warehouses. No clicks.
- Region selector is the regional manager's view. Same code, one filter. No logins.
- Ask-anything: an LLM writes one read-only SELECT over the clean layer. SQL and rows shown with every answer. Without a key the tab offers the eight sample questions so it still opens.
