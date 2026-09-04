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
