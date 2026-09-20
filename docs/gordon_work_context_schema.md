# Gordon → Work SAFE context: schema

Field-by-field contract for `python -m grocery_bot.cli work-context-safe
tivtaam` (`grocery_bot/work_safe_export.py`, schema
`gordon-work-safe-context/v1`). Every field carries one label:

- **OBSERVED** — a stored fact (a real order, price, or date).
- **USER_DECLARED** — typed or tapped by a household member.
- **DERIVED_FROM_OBSERVED_HISTORY** — computed from real observed rows
  (a median, a gap between real dates) — a summary of evidence, not a
  planner conclusion.
- **HINT** — Gordon's own guess at a product mapping. Useful context,
  never ground truth; Work should verify against the live catalogue
  before acting on it.

Nothing in this export carries Gordon's own due/buy-don't-buy
conclusions, department classification, or promotions — see "What's
deliberately excluded" at the end.

## `schema`

Literal version string, `"gordon-work-safe-context/v1"`. Bump this if
the shape ever changes in a way Work needs to know about.

## `metadata`

| Field | Label | Meaning |
|---|---|---|
| `generated_at` | OBSERVED | When this export was built |
| `store` | — | Which chain (currently only `tivtaam` has real content) |
| `schema_doc` | — | Points back to this file |
| `source_freshness.order_log_latest` | OBSERVED | Newest `order_log` date for this store |
| `source_freshness.order_log_count` | OBSERVED | How many real orders `order_log` holds for this store |
| `source_freshness.tivtaam_order_lines_latest` | OBSERVED | Newest order with real per-line detail recorded |
| `source_freshness.tivtaam_order_lines_count` | OBSERVED | Total real purchase-line rows recorded, all time |
| `excluded_by_design` | — | Documentation only — names what this export leaves out and why. Its presence here is not a leak of those fields; nothing it lists appears anywhere else in the export as a key |

## `pending_needs[]`

Verbatim `adhoc_requests` rows — what a household member typed and Gordon
hasn't put in a cart yet. `text`/`requested_by`/`quantity`/`amount`/
`unit`/`brand` are all **USER_DECLARED**; `id`/`requested_at` are
**OBSERVED**. Not store-scoped — the table itself has no `store` column,
since a need isn't tied to a chain until Gordon resolves it.

## `recent_purchase_history`

`{"orders": [...], "window_days": 84, "min_orders_target": 10}` — the
last `window_days` days of real Tiv Taam orders, extended to the last
`min_orders_target` orders if the time window alone would return fewer
than that (a quiet stretch should not mean an empty export).

Each order:

| Field | Label | Source |
|---|---|---|
| `order_id` | OBSERVED | `order_log.order_code` |
| `order_date` | OBSERVED | `order_log.placed_at` |
| `total`, `item_count` | OBSERVED | `order_log` |
| `line_detail_available` | — | `false` means the order is known but its per-line detail hasn't been fetched yet (see "Freshness" below) — `lines` will be `[]`, which is not the same as "nothing was bought" |
| `lines[].product_code` | OBSERVED | Tiv Taam's own `productId` |
| `lines[].barcode` | OBSERVED | Manufacturer EAN, where the order carried one |
| `lines[].raw_name` | OBSERVED | Exactly as the chain's API returned it |
| `lines[].ordered_quantity` | OBSERVED | What was ordered (kg for a weighed line) |
| `lines[].actual_quantity` | OBSERVED | What was actually delivered/picked — differs from `ordered_quantity` on almost every weighed line |
| `lines[].weightable` | OBSERVED | Whether this is a by-weight product |
| `lines[].unit` | OBSERVED | `'ק"ג'` when weightable, else `null` — Gordon has no other unit signal at the line level |
| `lines[].price`, `lines[].line_total` | OBSERVED | As charged on that line |
| `lines[].substituted` | OBSERVED | Whether this line replaced an originally-ordered, undelivered item |

The delivery fee and the undelivered half of a substitution are already
excluded upstream (`tivtaamhistory.order_lines()`), not filtered here.

## `observed_purchase_summary[]`

One entry per Tiv Taam product with at least one real recorded line.
Every aggregate states its own `sample_size` so Work can judge
confidence rather than trust a label blindly.

| Field | Label | Meaning |
|---|---|---|
| `product_code`, `barcode`, `raw_name` | OBSERVED | Identity |
| `last_purchase_date` | OBSERVED | From `last_purchase`, falling back to the newest recorded line if `last_purchase` has no row |
| `observed_purchase_dates` | OBSERVED | Every real date this product appears in `tivtaam_order_lines` |
| `observed_purchase_count` | OBSERVED | `len(observed_purchase_dates)` |
| `observed_typical_quantity.median` | DERIVED_FROM_OBSERVED_HISTORY | Median of real `actual_quantity` values — **not** `stock_items.default_quantity** |
| `observed_typical_quantity.sample_size` | — | How many real lines the median rests on — `1` means exactly that, not a pattern |
| `observed_purchase_interval_days.median` | DERIVED_FROM_OBSERVED_HISTORY | Median gap between real observed dates — **not** `shelflife.py`'s model, and absent (`null`) rather than guessed when fewer than 2 dates exist |

## `product_hints[]`

Gordon's `preferred_products` rows, **as hints only**.

| Field | Label | Meaning |
|---|---|---|
| `household_term` | USER_DECLARED | What the household calls it |
| `product_display_name` | HINT | Gordon's stored name for the chosen product |
| `barcode` | OBSERVED (when present) | Joined from `stock_items` by product code, where that code is also a stock item |
| `stored_product_code` | HINT | Gordon's `preferred_products.product_code` |
| `provenance` | — | `human` \| `purchase` \| `inferred` \| `search` — confidence in the *choice*, independent of whether the code is real (see next field) |
| `evidence_count` | OBSERVED | How many times this exact choice was reconfirmed |
| `machine_resolvable` | — | **The one computed field in this export.** `false` whenever `stored_product_code` is empty or textually identical to `product_display_name` — the audit's found defect, a description written into the code column instead of a real id. This is independent of `provenance`: a `source='purchase'` hint can still be `machine_resolvable: false`. |

**Work should never treat a hint as a confirmed product identity** —
verify against the live catalogue, especially where
`machine_resolvable` is `false`.

## `explicit_rejections[]`

`product_rejections` rows, presented as strong constraints (`"constraint":
"DO_NOT_CHOOSE"`). **USER_DECLARED** — written only on an explicit human
correction, never inferred from a cart edit.

## `repeat_cart_failures[]`

`cart_failures` rows that failed on 2+ distinct runs within 180 days.
**OBSERVED**, but explicitly tagged `"kind":
"OPERATIONAL_EVIDENCE_NOT_A_PREFERENCE"` — a term Gordon's automation
struggled to add (a UI change, an out-of-stock item) is not the same
signal as a household not wanting the product; conflating the two would
be a new mistake this export exists to avoid repeating.

## `retailer_context`

Static configuration (`chains.py`) plus a timestamp — not learned data.

## What's deliberately excluded, and why

| Excluded | Why |
|---|---|
| `gordon_due` / `due_signal` / `due_reason` | Planner conclusions (`shelflife.py`), gated on a Tiv Taam department classification the audit found is 100% `"שונות"` (uncategorised) — the signal would be manufactured, not real |
| Department | Same defect — every Tiv Taam `stock_items` row currently reads `"שונות"` |
| `stock_items.default_quantity` as if observed | For Tiv Taam specifically, no code path in this repo reproduces how that value was computed (unlike Shufersal, where it's a real historical median) — see the audit |
| Standing-list / buy-don't-buy planner output | That's `work_projection.py`'s job, a different export for a different purpose (benchmarking Gordon's own plan, not feeding Work's) |
| `hotdeals`/`store_promotions` (promotions, in general) | The audit found `hotdeals.find(chains=["tivtaam"])` leaks Shufersal promotions (9 of 10 results in one real check) — not fixed yet. Work is expected to check live Tiv Taam promotions itself for this first pilot |

## Freshness note

`recent_purchase_history` can legitimately show real orders with
`line_detail_available: false` and empty `lines` — that means the order
is known (`order_log`) but its per-line detail hasn't been fetched yet.
As of 2026-09-20, `tivtaamhistory.sync()` (the nightly job) backfills up
to `MAX_LINE_DETAIL_FETCHES_PER_SYNC` (20) such orders per run, oldest
gap first among the newest orders, so a large historical backlog closes
over a few nights rather than in one pass. Check
`metadata.source_freshness` to see exactly how much detail exists as of
any given export.
