# Data notes: what we found, what we did about it, and what we chose not to do

Every item here was checked against the database, not taken from the data
dictionary. Numbers are for the full 18 months unless stated. The SQL is in
`scripts/profile.py` if you want to re-run it.

## 1. Things that change the numbers

### Quantities are in two units on the same table (KP-2340)
78% of order lines are booked in cases, 22% in eaches. `SUM(delivered_qty)`
mixes them. `case_pack_at_order` is populated on every line and equals the
product's `case_pack` on all 511k rows, so conversion is safe.

**What we did:** `v_order_lines` carries `ordered_cases / delivered_cases /
ordered_eaches / delivered_eaches`. Every metric picks one. The UI has a
cases/eaches toggle because Divya wants cases and Rakesh wants eaches and
both are right for their audience.
**Not done:** standardising the source table. We do not write to the client's db.

### No order in the whole dataset is delivered in full
All 511,516 lines have `delivered_qty < ordered_qty`. Not one exception. So a
strict "in full" test gives 0% and OTIF is 0% for every region, route and
outlet. That is not a supply chain result, it is how the data was generated
(or a downstream rounding rule nobody has mentioned).

**What we did:** in-full means case fill rate at or above 90% for the order
(`KESTREL_IN_FULL_PCT`). At 90%, 18% of orders are in full and OTIF has a
spread worth looking at. The threshold is printed in the sidebar so nobody
mistakes it for the customer's definition.
**Alternative rejected:** reporting OTIF as 0% everywhere. Technically true,
useless to the person the screen is for. Second alternative, 95%: only 640
orders clear it and the per-outlet view becomes noise.

### `delay_minutes` does not agree with the timestamps, for either vendor
Recomputing delay from `actual_arrival - planned_arrival` disagrees with
`delay_minutes` on 87% of TELEMATICS_A rows and 87% of TELEMATICS_B rows,
usually by whole hours in either direction. It is not a timezone offset (the
error is not constant) and it is not a parsing problem (B's `03-Jan-2025
12:43 PM` parses fine; A is already ISO).

**What we did:** use `delay_minutes` as the vendor reported it and expose
the parsed timestamps alongside it. On-time is `delay_minutes <= 30`
(`KESTREL_ONTIME_MIN`). The brief gives no tolerance; 30 minutes is what we
would propose in the first workshop.
**Not done:** picking the timestamps over the delay field. We have no way to
know which system is wrong and the delay is what the carriers are measured
on today.

### Outlets that should not be in a ranking (KP-2377, KP-2211)
- 3 test/migration outlets (`TST00001..3`, names like "DO NOT USE - migration
  dummy") with 260 real-looking orders between them.
- 42 soft-deleted outlets (`is_deleted = 1`) with 4,812 orders. The dictionary
  says the application filters this flag and raw SQL does not.
- 55 closed outlets, 2,493 orders dated after their `closed_date`.
- One clear ownership-transfer duplicate: `OUT00018` and `OUT90118` share a
  phone number and a name. Beyond that, 100+ outlets share a name and city
  ("Sri Mart, Indore" x4) but have different phones, GST numbers and
  onboarding dates, so they may be genuine multi-branch customers.

**What we did:** `v_outlets.reportable` = active, not deleted, not test.
Every ranking filters on it. Ask-anything is told to as well.
**Not done:** merging the name/city duplicates. The evidence is not strong
enough to merge automatically and a wrong merge is worse than a duplicate.
Flagged for the client, not fixed.

### City names are free text (KP-2288)
`Bangalore` (16) and `Bengaluru` (28); `Delhi` (27) and `New Delhi` (11).
**What we did:** `city_clean` collapses those two. Nothing else looked like
a variant. Competitor prices are keyed on the clean spelling.

### Header value does not reconcile for one source (KP-2301)
Gross ties to the sum of lines on all 83,671 orders. What does not tie is
**net**: for every `PARTNER_API` order, `order_value_net_inr` is exactly
1.085 x (gross - discount + tax). Looks like a platform commission or a
second GST application on the e-commerce feed. The ticket was raised
against the wrong column.
**What we did:** every value metric is built from lines, never the header.

### Return quantities are negative from one feed (KP-2402)
900 of 14,000 credit notes carry a negative `return_qty`. Values are always
positive. `v_returns` uses `abs()`. Also: 1,291 credit notes return more
than the line delivered; we report them as-is and flag them.

### Timestamps in four formats
`ERP_WEB` writes `01/01/2025 18:51`, `SFA_MOBILE` writes ISO, `PARTNER_API`
writes ISO with a `Z` (UTC, so 5h30 behind the rest), `TELEMATICS_B` writes
`03-Jan-2025 12:43 PM`. `parse_ts()` handles all four and shifts UTC to IST.
`order_date`, `return_date` and `planned_arrival` are already clean.

### Product table is current state only
`products.mrp_inr` is today's price. 49,204 order lines (10%) fall inside a
price window that has since closed. `v_price_windows` gives the historic
price. The price-position screen deliberately uses today's MRP because the
question is about today's shelf.

## 2. Things worth telling the client

- **25% of deliveries are more than two hours late** and the average delay
  is 132 minutes. 139 of 140 routes fail the "more than 1 in 10 deliveries
  over 2h late" test. Either the plan times are fiction or the network is.
- **91,484 chilled lines went to 508 outlets flagged as having no chiller.**
- **Cold-chain returns mostly follow deliveries with no excursion flag.**
  Of 806 RT06 credit notes, 22 sit on a delivery flagged for a temperature
  excursion, 725 do not, 59 have no delivery at all. Either the flag misses
  most breaches or the reason code is being used loosely.
- 24 SKUs are discontinued and every one of them kept being ordered
  afterwards: 14,370 lines, from every outlet. Nobody blocked them in the
  ordering systems.
- 51 outlets are still assigned to salespeople who have left.
- 97 SKUs in the product master have implausible pack units ("Milk 200kg",
  "Butter 1000ml"). The competitor site repeats them, which is how we know
  they were copied from the master rather than mistyped there.
- `approval_date` on credit notes is never populated, so approval cycle
  time cannot be measured.
- 1,716 OPEN orders already have delivered quantities on their lines.

## 3. The external sources

### BazaarPulse
- **Four price markups**, one per city: `.price` text (Mumbai),
  `data-price-paise` attribute with useless visible text (Bengaluru),
  `<em>Rs.</em> 88.68` (Delhi), `INR 229.86` in `.sellingPrice` (Chennai).
  `parse_price()` tries them in order; tests cover each.
- **Two pagination schemes**: `/page/N.html` for Mumbai and Delhi,
  `index.html` then `index_p{N}.html` for Bengaluru and Chennai. The
  in-page pager for the second pair links to `index.html?p=N`, which a
  static server answers with page 1 every time. We ignore the pager and
  build URLs from the "page 1 of N" breadcrumb.
- Delhi says 15 pages, three product detail pages are missing (387, 458,
  777), and 8 listings appear on two pages. The crawler tolerates 404s and
  de-duplicates by `data-listing-id`.
- **`/internal/margin-sheet.html`** contains competitor margin data and is
  disallowed by `robots.txt`. We do not fetch it, and `Site.fetch()` raises
  if asked to. Crawl-delay 1s is honoured, so a full run takes about 70s.
- **No SKU key.** Titles are noisy ("Combo AMRITVALLEY RICE 400ML (New)",
  "Kestrel Sel. Rusk 400G", "Hillfare Inst. Noodles 400G"). Matching is
  brand alias + product-type words + pack size, with "Select" treated as a
  sub-brand. 1,137 of 1,137 listings match; unit disagreements (kg vs g)
  are accepted at 0.7 confidence and shown.
- Detail pages carry six weeks of price history. We do not fetch them: the
  listing card already has the current price and last-seen date, and
  1,134 more requests at 1s each buys nothing the brief asks for.

### Partner billing API
- `amount` and `detention_charge` are in paise. Divided by 100 on ingest.
- `created_at_utc` is UTC; converted to IST. `service_date` is a plain date.
- 429 with `Retry-After` on ~1 in 9 calls, 503 on ~1 in 25. The client
  retries the same cursor with the header's wait or exponential backoff and
  stores the cursor after every page, so an interrupted walk resumes.
- **Invoice route codes are unreliable.** 87% of invoices name a route that
  belongs to a different warehouse than the invoice's `warehouse_code`.
  Only a third of billed rupees land on a warehouse+route+month cell that
  has deliveries. Orders, by contrast, always agree with the route master.
  We therefore join freight to deliveries on **warehouse + month** and do
  not offer a route-level freight cut. Carrier splits allocate a cell's
  cases in proportion to what each carrier billed in it.
- Carriers declare regions (`Eastbound Freight: East`) but bill every
  warehouse. Ignored; the warehouse code on the invoice is what we trust.
- We pull the last complete fiscal quarter by default (about 7,100 invoices,
  36 pages, one to two minutes). `--all` walks all 41,500.

## 4. Design decisions and the alternatives

| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Clean layer | TEMP views created on connect, source db read-only | Materialised tables, dbt-style transforms | Zero setup, nothing to keep in sync, the client's file is never touched. Costs query time at 100x volume (see DECISIONS.md). |
| UI | Streamlit, one file | FastAPI + React | Opens with one command, which was the client's only hard constraint. A real front end is two weeks we do not have. |
| Front page period | Last complete fiscal quarter (Apr–Jun 2026) | Calendar quarter, last 30 days | The board asks about Q1 first and Kestrel's Q1 is April–June. |
| Fill rate unit | Both computed, cases default, toggle | Pick one | The two memos disagree for good reasons. The toggle is cheaper than the argument. |
| OTIF definition | delay ≤ 30 min and case fill ≥ 90% | Strict, or channel-specific SLAs | Strict gives 0% everywhere. SLAs are not in the data. Both knobs are env vars. |
| Orders in service metrics | DELIVERED and PARTIAL only | Include CANCELLED as 0% fill | Cancelled orders were never served; counting them as short answers a different question. OPEN orders are excluded because they are not finished. |
| Freight | Partner API, joined on warehouse + month | `deliveries.fuel_cost_inr` | The dictionary says fuel cost is driver-entered and not the billed amount. We do not show it. |
| Freight scope | One quarter by default, cached, resumable | Full walk on every start | Full walk is minutes of flaky API per launch; the front page only needs the quarter. |
| Price gap | MRP vs lowest price seen in last 14 days, per city | Median, per-retailer | "What are competitors actually charging on the shelf" is a floor question. Freshness window is an env var. |
| Scrape depth | Listing cards only | Detail pages with history | Cards have everything the brief asks for; 1,134 extra requests at 1s crawl delay is 19 minutes for a chart nobody requested. |
| Ask-anything | Claude writes SQL against the views, SQL and rows shown | Templated questions only, or free SQL with no guard | Divya's questions change daily. Showing the SQL is what makes the answer defensible. Falls back to prepared questions without a key so the tab still opens. |
| Weather / holidays | Not used | Open-Meteo, Nager.Date | We could not find a metric they would explain in six hours. "It was available" is not a reason. |
| Duplicate outlets | Flag, do not merge | Fuzzy merge | A wrong merge silently moves orders between customers. |
