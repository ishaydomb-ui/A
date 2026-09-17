# grocery-automation — architecture, for review

Written 2026-09-17 for an external audit. The goal is that someone who
has never seen this repo can judge the design and propose improvements,
so this states what the system does, how it is built, **where it is
weak**, and which weaknesses are known-and-accepted versus known-and-not
-yet-fixed.

Scale: 22,005 lines across 47 modules, 72 test files, 1,119 tests, 263
commits. One SQLite database, 20 tables, 784k rows (dominated by
`store_prices` at 600k and `price_history` at 111k).

---

## 1. What it is for

A household of four (two adults, two children aged 3 and 5) orders
groceries online, roughly every 7 days, split between two Israeli chains.
The bot exists to remove the two costs they actually named: rebuilding the
same list from memory every week, and losing money to promotions and
club benefits nobody has time to track.

**Measured, not assumed:** across 2026 they placed 23 Tiv Taam orders and
6 Shufersal ones. Tiv Taam is the primary chain by a factor of four. This
matters to the audit because much of the code was written when only
Shufersal's history was readable, and several defaults still reflect that.

### The non-negotiable constraint

> The bot may search, build carts, fill forms and prepare orders. **It
> must never complete a purchase or submit a payment.** Every order stops
> at a human review step.

This is enforced in three independent places, deliberately redundant:

| Layer | Mechanism |
|---|---|
| Tool catalogue | `planner.FORBIDDEN` — checkout/pay/account tools refused during plan validation, whatever the model proposed |
| Guessing path | `hybrid.CART_TOOLS` — any cart-touching tool reached from an unclassified message is refused outright |
| Adapters | No method exists that can reach a checkout step; `tivtaam_api.strip_payment` removes card fields on ingest |

An auditor should try to find a fourth path. That is the highest-value
thing to attack in this codebase.

---

## 2. Runtime topology

Single Contabo VPS in France, user-scope systemd (no root services):

```
grocery-bot.service     long-running python-telegram-bot application
grocery-prices.timer    price feed refresh, several times daily
grocery-nudge.timer     hourly "is a shop overdue" check
grocery-backup.timer    git push + DB backup to remote storage
grocery-doctor.timer    hourly verification that the backup actually worked
```

**Geo-blocking is the defining infrastructure constraint.** Both chains
block non-Israeli IPs; Shufersal returns HTTP 200 with a placeholder, so
a block looks exactly like broken selectors. Egress goes through
Tailscale in **userspace mode** offering SOCKS5 on `localhost:1055` —
deliberately not the system default route, because two other projects
share this host. `ShufersalAdapter` refuses to start without a proxy.

The price feeds deliberately bypass the proxy: they work direct and would
otherwise consume the household's home bandwidth.

**Current weakness:** the documented exit node (a Xiaomi TV box at the
household) is offline; traffic fails over to other tailnet nodes
intermittently. On 2026-09-17 a run lost the route mid-flight and 14 cart
adds failed in one burst. `exitnode.ensure_israeli_exit` probes and
re-selects **once before a cycle**, not when a running cycle starts
failing. There is no circuit breaker. *This is unfixed and is the largest
operational fragility.*

---

## 3. Data model

Twenty tables in one SQLite file. The ones that carry the design:

| Table | Rows | Role |
|---|---|---|
| `store_prices` | 600,224 | Per-chain price feed, 6 chains, refreshed daily |
| `store_promotions` | 47,934 | Promotions per chain |
| `price_history` | 110,933 | Daily per-item price, the only irreplaceable data here |
| `catalog_products` / `catalog_promotions` | 5,868 / 18,712 | Shufersal's own feed (separate namespace from the portal feeds) |
| `stock_items` | 681 | **What the household actually buys**, with `share` = fraction of orders containing it. Derived from order history |
| `last_purchase` | 703 | product → date last bought, per chain |
| `preferred_products` | 782 | term → chosen product. The memory that stops re-asking |
| `order_log` | 62 | Placed orders, per chain |
| `adhoc_requests` | 130 | The shopping list. Lifecycle: pending → in_cart → shopped → confirmed |
| `pending_ambiguities` | 172 (3 open) | Search terms with several candidate products |
| `cart_failures` | 16 | Per-item add failures, for strategy not just counting |

**Namespace trap worth knowing:** the web store's product ids and the
price feed's item codes are different namespaces with no overlap.
Anything joining them must go through product *names*, with folding
(`storage._fold_apostrophes` collapses the apostrophe and gershayim
families — 48,776 feed names carry an ASCII quote and 48 carry the Hebrew
gershayim; without folding, a query from an iOS Hebrew keyboard reaches
neither population).

---

## 4. Module map

### Store access
- `adapters/shufersal.py` (668) — Playwright. Headless username/password
  login works, no OTP. Tile parsing via `data-product-*` attributes.
- `adapters/tivtaam.py` (676) — Playwright over the Self-Point AngularJS
  front end. Login is behind a checkbox reCAPTCHA, so the session is
  captured once by a human through noVNC and reused; measured to last
  16+ days.
- `adapters/tivtaam_api.py` (224) — Self-Point REST client. Orders,
  order lines, profile, coupons, smart-list. Strips payment fields.
- `exitnode.py`, `connectivity.py` — Israeli egress probing and failover.

### Catalogue and pricing
- `publishedprices.py`, `prices.py`, `catalog.py` — the legally mandated
  price-transparency feeds, 6 chains.
- `compare.py`, `basketview.py`, `unitprice.py`, `threshold.py` —
  cross-chain basket pricing, per-unit value, spend thresholds.
- `multibuy.py`, `dealfill.py` (844), `hotdeals.py`, `radar.py` —
  promotion parsing and deal selection.

### Household model
- `stock.py`, `history.py`, `tivtaamhistory.py`, `learn.py` — turn order
  history into a buying pattern.
- `shelflife.py`, `pantry.py`, `waste.py` — replenishment intervals.
- `standingcart.py` (562) — the current operating model: after each shop,
  both carts are refilled so the household only reviews and deletes.

### Understanding (natural language)
- `nlu.py` (644) — classifier over a fixed intent taxonomy, backed by
  `claude -p` as a subprocess.
- `planner.py` (397) — tool-catalogue planner for messages the taxonomy
  has no slot for. 16 tools, `validate()` applied to parsed JSON
  regardless of what the prompt asked.
- `hybrid.py` — the seam: the planner takes over exactly where the
  classifier says `unclear`, and nowhere else.
- `convo.py` — rolling 6-turn transcript so corrections resolve.
- `autoresolve.py` (436) — **decide which product a term means from
  purchase history**, rather than asking.
- `readback.py` — redacts any figure in model-authored text that the code
  did not compute.
- `untrusted.py` — flattens fetched text before it enters a prompt.

### Interface
- `telegram_bot.py` (3,187) — 19 commands, 4 scheduled jobs, all
  conversational handling. **This is the largest module and the most
  obvious refactor target.**
- `safesend.py`, `mdtext.py`, `htmltext.py` — Telegram formatting
  survival: Markdown fallback, 4096-char splitting on line boundaries.

### Benefits (adjacent scope)
- `benefits_catalog.py`, `benefits.py`, `malls.py`, `mallfinder.py`,
  `areas.py`, `whereto.py` — credit-card and club benefits, mall→chain
  mapping, geocoding.

---

## 5. Control flows worth auditing

### 5.1 A message from the household

```
text → convo.remember_turn → nlu.parse_message
         ├─ classifier (claude -p, 120s cap)
         ├─ if unclear → planner.plan_message (60s) → hybrid.reconsider
         └─ if still unclear → loop pass (30s)
       → readback.verify (redact uncomputed figures)
       → intent handler → adapter
```

**Verified worst case for one message: 210 seconds.** Three chained
subprocess calls. An auditor should question whether a persistent session
(the Claude Agent SDK, now available for Python) would be better than
spawning a CLI per message — this is an open decision, not an oversight.

### 5.2 Filling a cart

```
plan_refill(store) → terms
  → bulk_match (one call, ~40s, replaces ~15min of per-item search)
  → per term: preferred_products? → add by code
              else search → single match? → add
                          → several matches? → autoresolve.decide
                                             → still nothing? → ask
  → CartGuard: never re-add what a person deleted since the last fill
  → record_manifest (what went in, for later removal detection)
```

### 5.3 Detecting that a shop happened

Three signals, deliberately layered because each is insufficient alone:

1. `/done` or free text — immediate, requires the household to say so.
2. `order_log` gaining a row — Tiv Taam same-day, Shufersal ~36 hours.
3. `shops_detected_since_refill` — the backstop, per chain.

On `mark_shopped`, the chain's manifest is snapshotted so that when the
order later appears, `removals_from_order` can say what was deleted
before paying. This is the only observation that survives checkout,
because after a real shop the cart is always empty.

---

## 6. Where I would point an auditor first

Ordered by my estimate of risk, most serious first. These are stated
plainly because the household's own sense is that recent weeks moved
sideways, and that deserves an honest answer rather than a feature list.

**1. `telegram_bot.py` is 3,187 lines and holds interface, orchestration
and scheduling.** Almost every recent defect has been in the seams it
owns. It should probably be three modules.

**2. There is no circuit breaker on infrastructure failure.** A dropped
exit node produced 14 consecutive failed adds and the run kept going. The
probe happens once, before the cycle. Related and now fixed: those
failures were being written into `cart_failures` as though they were
facts about products.

**3. Matching is name-based and the catalogue fights it.** A bare produce
term returns the processed version first, systematically — `תפוחים`
returns apple juice, `אפרסק` returns peach syrup. This produced five
wrong items in a real order. A filter now prefers unprocessed candidates,
but the underlying approach — string matching against product names — is
the weakest structural part of the system. Barcodes would be better and
are present in the feeds but not in the web stores' search results.

**4. Tiv Taam's standing cart was never filled.** The manifest gave
Shufersal 120 items and Tiv Taam 1, because no Tiv Taam purchase history
was readable until 2026-09-15. The primary chain has been running without
the feature that defines the product. `plan_refill` now offers 260 terms
there; the fill has not been run.

**5. Auto-resolution replaced asking, very recently.** 134 open questions
were closed from purchase history on 2026-09-17 after the household
refused to answer them. The confidence model is crude: full term coverage
plus purchase evidence counts as confident, anything else is "worth a
look". It writes into `preferred_products`, which is durable — so a wrong
decision persists until corrected. Two such falsehoods were caught and
repaired in the live data within hours of shipping (`בייקון` → a camera;
`טימין טרי` → milk). **An auditor should assume more remain.**

**6. Cart reads are not reliable.** Shufersal's cart reader returned zero
lines against a ₪1,831 total; Tiv Taam's returned inconsistent line lists
across consecutive reads. Both report `ok=True`. Any feature resting on
"what is in the cart right now" rests on sand.

**7. Consumption bookkeeping compares the wrong strings.** A request is
marked bought by matching the request text against the added *product*
name — "חלב עמיד" vs "חלב עמיד 3% 1 ליטר" — so items land in the cart
while staying on the list. Known, unfixed.

### The recurring defect shape

Worth naming because it accounts for most of the above: **a state that
reads identically whether or not the thing is true.**

- "no login field" = logged in, *or* the page has not rendered
- "probably out of stock" = out of stock, *or* our search term was wrong
- silence from the bot = broken, *or* deliberately quiet
- `ok=True, items=[]` = empty cart, *or* a failed read
- 90 open questions = genuinely ambiguous, *or* nobody looked at the data

Each was found only after it cost something. A useful audit would look
for the ones not yet found.

---

## 7. What is deliberately not built

- **No automatic checkout.** Not a gap; the point.
- **No approval gate before cart writes.** Explicitly declined by the
  household: the cart *is* the proposal, and their review before paying
  is the approval.
- **No captcha automation.** The one Self-Point login is human, by design.
- **No per-person benefit attribution.** Benefits are household-level by
  explicit decision.

---

## 8. Open questions for the audit

1. Is the three-layer purchase prohibition genuinely non-bypassable?
2. Should product matching move to barcodes, and is the namespace gap
   bridgeable at all?
3. Is a 210-second worst case acceptable for a chat interface, and is a
   persistent agent session the right answer?
4. How should confidence in an auto-resolved product decision be
   modelled, given the decision is durable and silent?
5. Is one SQLite file the right store at 784k rows and growing 23k/day?
