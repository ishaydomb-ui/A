# Architecture challenge — inspection, disagreement, direction

Response to Ishay's 12-section challenge of 2026-09-17. **Nothing here
is implemented.** Every claim is backed by a command run against the
current code or the live database on 2026-09-17; where I was wrong in
`docs/ARCHITECTURE.md` this morning, I say so.

## The headline assumption — agreed, with one precision

> "The first priority may be making the existing intelligence execute
> reliably and naturally, not adding more grocery intelligence."

**Agreed, and the evidence is stronger than the assessment assumed.** The
20-item fill path makes **zero model calls** — `autoresolve`,
`disambiguate`, `orchestrator` and `standingcart` contain no model
invocation at all. The model touches only the *message*. Every failure
this week was in execution and state, not in grocery reasoning.

The one precision: section 6 (product identity) *is* a domain gap, but the
evidence shows it is a **data-plumbing** gap, not an intelligence gap.
The identity bridge already exists in the data and is simply not used.
So even the strongest "domain" item on the list is an execution item.

---

## 1. Control plane

**Verdict: model-first for *understanding*, not for *execution*. The two
are conflated in the question, and separating them is what makes the
migration small.**

Evidence:

- The taxonomy is 15 intents; 13 have handlers; `parse_message` has
  exactly **one** caller (`telegram_bot.py:781`). The taxonomy is not
  woven through the system — it is a single seam.
- The planner has 16 tools, 6 touching the cart. `hybrid.TOOL_TO_INTENT`
  maps only 7 of them to handlers; the 6 cart tools are refused by design
  and `remember_preference` is unmapped. So the planner is already the
  richer surface, and it is deliberately throttled.
- Measured latency (`docs/EXPERIMENT_DIRECT_PLANNER.md`): classifier
  7–10s on the common case, harness median 17s, planner median 18s but
  worst single message **74s**; chained worst case **210s**. There is no
  production instrumentation — the journal contains zero latency lines.
  *That absence is itself a finding.*

**Wrong assumption in the question:** "the planner appears to take over
mainly when the classifier does not know what to do." True, but the
important consequence is the reverse: **the classifier is a fast-path
cache in front of the planner**, and the measurements say it wins on
terse follow-ups ("בעצם שניים"). Removing it would cost 7–10s messages
their speed and lose nothing else. Keeping it as a cache and making the
planner the *default* front door is the smaller change.

**Would a persistent Agent SDK session materially simplify?** It would
collapse the 210s worst case (three subprocess spawns) to one call with
context already loaded, and it would let containment be *declared*
(`tools: []` + explicit verbs) instead of enforced after parsing. It
would **not** touch execution reliability, which is where the week's
defects were. So: yes for the conversation layer, irrelevant for
sections 3–5, and it should not be sequenced ahead of them.

**Smaller migration path (not a rewrite):**
1. Make the planner the default; keep the classifier as a short-circuit
   for its measured wins (quantity/correction follow-ups).
2. Map the 6 cart tools to the *same* deterministic handlers the
   classifier already uses — the refusal exists because the guessing
   path was untrusted, not because cart tools are unsafe.
3. Move to a persistent session only after 3–5 below, with the
   comparison harness pointed at it.

---

## 2. Canonical basket state

**Verdict: about 70% exists; the missing 30% is exactly the part that
would have saved this week. Do not add a "basket plan" table.**

What exists, and is already the shape you describe:

| Concept | Where it lives today |
|---|---|
| desired items | `adhoc_requests` + `base_list_items` + `plan_refill` |
| lifecycle | `ADHOC_STATES = in_cart → awaiting → shopped → confirmed → delivered`, chain-scoped |
| what went in | `standing_cart_manifest` per store |
| what the household removed | `CartGuard.removed` (live) + `shopped_snapshot` vs order (post-checkout) |
| what failed | `cart_failures` |

**What is missing — the minimum state to resume after partial failure:**
a per-item **attempt record**: `(request_id, store, attempted_at,
outcome ∈ {verified, unverified, failed, infra}, evidence)`. Today an
item is either `consumed` or not, and consumption is decided by comparing
the request text against the added *product* name (`telegram_bot.py:
2704`) — which is why 17 of 21 items stayed "pending" after landing in
the cart. There is no record that says "attempted at 04:07, add sent,
not verified". That single missing record is the cause of: the 6/20 →
14 confusion, the double-add of `עגבניות שרי במלח`, the watcher
re-announcing a backlog, and the inability to resume at item 7.

**Direction:** normalise, don't duplicate. Add the attempt record keyed
to `adhoc_requests.id` and manifest entries; derive `consumed` from
"has a verified attempt" instead of a string compare. Fill-and-delete
is unaffected: the household still deletes in the retailer cart, and
`CartGuard` + the shopped snapshot already turn those deletions into
`removed` evidence.

---

## 3. UNKNOWN must not look like FALSE — the audit

This is the highest-value section, and you were right to suspect it.
Every row below was read from the code today; "direction" says which way
the conflation errs.

| Where | UNKNOWN is returned as | Direction of harm |
|---|---|---|
| `shufersal._add` (585–628) | **"added"** after `click()` + 1s sleep, **no cart check at all** | *claims success* |
| `tivtaam._cart_line_count` (467–480) | `0` on exception; used as `before` in add verification | before=0 makes any read look like success; after=0 makes success look like failure |
| `tivtaam._cart_count` | `0` when the header text lacks "מוצרים" | same |
| `tivtaam._rows_once` (300–323) | `[]` for "no row matched" **and** for timeouts | not-found ≡ failed |
| `tivtaam.is_session_valid` (231–243) | `"היי" in body` — any page containing "היי" | *claims logged in*; `False` on exception (safe direction) |
| `cart_summary` both chains | `ok=True, items=[]` | observed live: ₪1,831 total, 0 lines, `ok=True` |
| `CartGuard.empty()` | `readable=False` → **allows everything** | documented, deliberate; still "unknown → unprotected" |
| `standingcart.removals` | `[]` for an empty cart | deliberate; now backed by the order-based path |
| `record_cart_failures` | one timestamp for a whole run | 14 sequential failures read as one instant |
| `_do_replace_item._remove_from_cart` | returns `False` when the adapter has no remover — **which is every adapter** | see §12 |
| `_remember_failures` | infra recorded as product (fixed 2026-09-17) | — |

Two of these are new findings from this audit, not in this morning's
document: **Shufersal never verifies an add at all**, and **`replace_item`
can never remove anything from any cart** because no adapter defines
`remove_from_cart` (Shufersal's is named `remove_item`; Tiv Taam has
none). The household is told "🔄 Y במקום X" and X stays in the cart.

**Direction:** a three-valued result type at the adapter boundary
(`VERIFIED / UNVERIFIED / FAILED` with an `evidence` field) replacing
`bool` and `[]` at the rows marked *claims success*. Not everywhere —
the deliberate ones (`CartGuard.empty`, `removals`) are correct and
documented. The rule to apply: **an exception handler may return "I
don't know"; it may never return "no".**

---

## 4. Cart mutations should be verified

**What "added" means today, per chain — this is worse than the document said:**

- **Shufersal:** the click did not throw, then `wait_for_timeout(1000)`.
  The in-cart tile state is checked *only on the exception path*. There
  is no before/after, no badge read, nothing. **Every Shufersal "added"
  this project has ever reported is UNVERIFIED.** The cart may well have
  received them — but the code does not know.
- **Tiv Taam:** line-count delta with polling (`after > before`). This
  is the right shape, and it has a known weakness the code documents
  itself: the header count "lags the click and was seen reading 0 on a
  full cart", and `_cart_line_count` returns 0 on exception.

**What can realistically be verified:**

| Chain | Cheapest reliable evidence |
|---|---|
| Shufersal | the tile flips to its in-cart form (`_tile_in_cart`, already exists, used only in the except path) — per-item, no page load; and `.topCartTotalItems` badge delta as a second signal |
| Tiv Taam | `.product-in-cart` DOM count delta (exists) **plus** presence of the added name in `_cart_line_names` for the disputed cases; the Self-Point API has no cart read, so DOM is the ceiling |

**Direction:** `ADD_VERIFIED` requires positive evidence from at least
one signal; `ADD_SENT_UNVERIFIED` is a first-class outcome that is
retried on the *next* pass by checking presence, not by re-adding —
which is what caused the duplicate `עגבניות שרי במלח` today.

---

## 5. Infrastructure failure must not become product failure

**Exactly how the 6/20 → 14 run happened, from the code:**

`ensure_israeli_exit` is called from six places in `telegram_bot.py`
(429, 1033, 1384, 1876, 2659, 2729) — **all before a run starts**. Inside
the add loop (`orchestrator.py`, `for index, (term, quantity) in
enumerate(terms)`) there are **zero** references to exit, health, proxy
or abort. When the route dropped, each of the 14 remaining items did a
full `page.goto`, hit `ERR_SOCKS_CONNECTION_FAILED` after its own
timeout, was recorded as an error, and the loop advanced. Nothing
distinguished "the 8th item failed" from "the network died at item 7".

**The smallest mechanism that prevents a recurrence — three pieces, no
framework:**

1. **Classify the failure at the adapter boundary.** A `CartAddResult`
   gets `kind ∈ {product, session, infrastructure}` — the
   `INFRASTRUCTURE_MARKERS` list added today already does the
   classification; it just does it *after* the run, in the recorder.
2. **Two consecutive infrastructure failures → stop issuing mutations.**
   Not a counter of total failures; consecutive. Re-probe the exit
   (`ensure_israeli_exit` already fails over across tailnet nodes) and
   re-check the session. If recovered, resume **from the failed index**;
   if not, stop and leave every remaining item `UNVERIFIED`, not
   `FAILED`.
3. **Resume needs the attempt record from §2.** Without it, "resume at
   7" is impossible because nothing knows 1–6 succeeded.

That is the entire design. It is perhaps forty lines in `_add_one`'s
loop plus the result field. No queue, no scheduler, no retry library.

---

## 6. Product identity — my document was wrong

`ARCHITECTURE.md` says the web-store and feed namespaces have "no
overlap". That was measured for Shufersal on 2026-08-29 and then
generalised. **Measured today against the live data:**

- **Tiv Taam: 340 of 377 purchased products (90%) carry a barcode that
  is present in Tiv Taam's price feed.** The order lines expose
  `barcode`; the feed stores `barcode`. The bridge exists and is unused.
- **Those same barcodes appear at the other chains:** Politzer 174,
  Fresh Market 121, Rami Levy 89, Osher Ad 80, Keshet 58. Cross-chain
  identity for the household's own products is *already in the
  database* and nothing joins on it.
- **Shufersal:** `P_`-stripped product codes match `catalog_products`
  item codes for **119 of 291 (41%)**. Partial, but far from zero. The
  Shufersal web store exposes no barcode to the adapter (the only
  "barcode" in `shufersal.py` is in a comment).

**Where name matching is genuinely unavoidable:** the Shufersal web
store's search results (59% of its products) and Tiv Taam's
autocomplete rows, which return names only. Everywhere else — order
history, feeds, cross-chain comparison — a stable identifier exists.

**Direction: HOUSEHOLD ITEM vs RETAILER PRODUCT is the right split, and
the evidence says it is mostly a schema change.** A household item
("fresh apples") maps to retailer products by barcode where known (Tiv
Taam, all portal chains), by `P_` code for Shufersal's 41%, and by
name-with-evidence for the rest. The processed/unprocessed attribute,
brand and unit belong on the *retailer product*, learned once from the
feed, not re-derived by string matching each time. Historical purchases
are the strongest identity evidence available and today they contribute
only a `share` number.

**Wrong assumption to retire:** "barcode doesn't solve everything" —
correct, but it solves 90% of the primary chain, and that is where the
questions came from.

---

## 7. Durable learning is too easy to poison — confirmed, with numbers

`preferred_products` today, 782 rows:

| Fact | Count |
|---|---|
| written on 2026-09-17 by the auto-resolver | **410 (52%)** |
| chosen product is something the household has actually bought | 604 (77%) |
| `term == product_name` — the row carries no information | **629 (80%)** |
| no real product code — cannot be added by id | 61 |
| distinct writers of `remember_choice` | 10 call sites |

**What promotes a mapping:** ten different call sites, each writing
directly and permanently — a clean search hit, a bulk match, the history
import, the auto-resolver, the CLI, and the Telegram choice button. None
records *why*.

**How correction is handled: it isn't.** `_do_replace_item` adds the new
item to the list and attempts a cart removal that cannot succeed (§3).
It never touches `preferred_products`. The planner's
`remember_preference` tool is unmapped. **A household correction does
not override the memory, so a wrong mapping recurs on the next fill.**
This is the mechanism by which `בייקון → מצלמה` would have come back.

**Minimal reversible model — three fields, no ML:**
`source ∈ {purchase, human, search, inferred}`, `evidence_count`,
`last_confirmed_at`. Promotion rule: a mapping becomes *preferred* when
it has a `purchase` source **or** a `human` source, or when it has been
used in a fill and then appeared in a real order (the `removals_from_order`
path already produces exactly this signal). An `inferred` mapping is
used *tentatively* and is the first thing the household's correction
overwrites. The 629 no-information rows should simply be deleted; they
exist only because `remember_choice` is called on every clean hit.

---

## 8. Bulk operations as the normal path

**Where the 20-item flow stands today:**

| Stage | Shufersal | Tiv Taam |
|---|---|---|
| match all terms | `bulk_match` — **one request, 89 terms in 42s vs ~15 min** per-item | **none** — per-item browser search with polling |
| model round-trips | 0 | 0 |
| add | per-item click (unavoidable; no batch endpoint) | per-item click |
| verify | none (§4) | count delta |

So the model is *not* the per-item cost — it is already out of the
loop. **The per-item cost is Tiv Taam search**, which has no bulk
matcher. The Self-Point API (`tivtaam_api.py`) exposes `smart_list`,
orders and shop-lists but the adapter never uses it for lookup.

**Direction:** for Tiv Taam, resolve by barcode against the local feed
*first* (§6) — that is a SQL query, not a browser search — and fall back
to autocomplete only for the ~10% without one. That removes most of the
per-item browser loop without any new endpoint.

---

## 9. Execution ownership

**Where Gordon gives up too early:** at every single item. `_add_one`
has no retry; a search timeout, a stale button, a lagging cart count all
end the item. `נקטרינה` was recorded "probably out of stock" twice and
the household bought it the same evening.

**Where it retries too much:** `tivtaam._rows` re-types the query up to
`SEARCH_RETRY_MS × attempts` per item — retrying the *search* when the
failure was the *route*.

**Where infrastructure detail reaches the household:** the list watcher's
failure message names `/start_order`; the deferred-cycle message says
"בדקו את הלוגים"; and until today `cart_failures` would have put
`ERR_SOCKS_CONNECTION_FAILED` into `/failures` output.

**Direction:** the retry belongs at the *outcome* level, not the action
level — "get this item into the cart by any allowed route" with an
ordered strategy (`preferred → barcode → bulk → search → shorten →
other chain → ask`). `failstrategy.py` already encodes most of that
ordering as *advice*; it is not wired as *behaviour*.

---

## 10. Wait / resume / events

**What exists:** `deferred_cycles` (whole-cycle granularity, survives
restart), `listwatch_*` and `question_queue` in `app_state`, the shopped
snapshot, `order_log` as the late signal, and `shops_detected_since_refill`
as the backstop. A restart mid-*conversation* is fine.

**What is missing:** a restart mid-*fill* loses position — there is no
per-item progress (grep for progress/resume/checkpoint in the
orchestrator finds only the UI callback). The 6-hour cooldown then
prevents a retry, so a deploy during a fill costs the household six
hours. This is the same missing attempt record as §2 and §5. One
addition fixes three sections.

Telegram bursts: `concurrent_updates` is on; the 4096 split and the
`_on_error` reply landed this week. Adequate.

---

## 11. Telegram as a client, not the architecture

**Is the size causing defects, or is it just large?** Evidence from
this week's fixes, by where they landed:

| Fix | Kind of logic | Belongs in a transport module? |
|---|---|---|
| `_cheapest_index` unit-price ranking | domain | no |
| `_log_removals`, `_log_removals_from_orders` | domain | no |
| `watch_list` debounce + cart run | workflow | no |
| `_mark_shop_done` chain scoping | state | no |
| `_ask_ambiguities` → auto-resolve | orchestration | no |
| `_do_replace_item` silent no-op | domain | no |
| `_split_for_telegram`, `_on_error` | transport | yes |

Six of eight were workflow, state or domain logic living in the
transport module, and each was found only because the transport
surfaced it. The 69 `async def`s include the entire order cycle
(`_run_cycle_with_live_view`), the standing-cart refill, removal
logging and the nightly learn.

**The smallest real refactor, justified by those defects:** extract one
module — call it the execution service — holding `_refill_carts`,
`_log_removals*`, `_mark_shop_done`, `watch_list`'s run half, and the
cycle runners. It is the code that touches carts and state. Leave
conversation handling and rendering where they are. That is one seam,
not three, and it is the seam every defect crossed.

---

## 12. Safety model — bypass attempt

I tried to find a fourth path to a purchase. Result of the audit:

- **Adapters:** every `goto` target is `/online/he/`, `/my-account`, or
  `/cart/cartsummary` (Shufersal) or the site root (Tiv Taam). The
  `.checkout` selector appears once, in a docstring stating it is never
  touched; no `click()` targets it. `tivtaam_api.strip_payment` removes
  card fields on ingest. **No adapter method reaches checkout.**
- **Planner:** `hybrid.reconsider` is the *only* executor of plan steps,
  and it refuses `CART_TOOLS` before mapping to an intent (line 12
  precedes line 18). `FORBIDDEN` is checked in `validate()` on the parsed
  JSON regardless of prompt content.
- **CLI:** `add-to-cart` takes terms; no order/checkout subcommand.
- **Classifier path:** `add_to_cart` and `start_order` mutate carts with
  no approval gate — **by design**, the cart is the proposal.

**No bypass found.** Two observations that are not bypasses but are
worth the auditor's eye:

1. `clear_cart` exists on the Tiv Taam adapter with **zero callers**.
   Dead code that is destructive should not stay present; delete it.
2. The new agentic planner is safe *because* the cart tools are refused.
   The moment §1's migration maps them to handlers, safety rests on
   `FORBIDDEN` + the adapters alone. That is still two independent
   layers and still no checkout method — sufficient — but the refusal
   list in `hybrid` should then become an explicit allowlist of the six
   cart tools, not an implicit "everything mapped is allowed".

---

## Proposed direction — one sentence per section, in build order

1. **Attempt record** (`§2, §5, §10`) — per-item `(request, store,
   outcome, evidence, at)`; derive `consumed` from it. *Unblocks the next
   three.*
2. **Three-valued adapter results** (`§3, §4`) — `VERIFIED / UNVERIFIED
   / FAILED` with `kind`; Shufersal gets a real verification using the
   `_tile_in_cart` check it already has.
3. **Consecutive-infra circuit breaker with resume** (`§5, §9`) — forty
   lines in the add loop.
4. **Barcode-first identity for Tiv Taam and portal chains** (`§6, §8`)
   — a join, not a matcher; removes most per-item browser search.
5. **Memory provenance** (`§7`) — three fields; delete the 629 empty
   rows; wire correction to overwrite.
6. **Fix `replace_item`** (`§3, §12`) — it cannot remove from any cart
   today.
7. **Extract the execution service** (`§11`) — one module, the seam the
   defects crossed.
8. **Planner as front door, classifier as cache** (`§1`) — after 1–7.
9. **Persistent Agent session** (`§1`) — last, with the harness.

What I would explicitly *not* do: a basket-plan table (exists in
pieces), generic retry infrastructure, a three-way split of
`telegram_bot.py`, or an ML confidence model.
