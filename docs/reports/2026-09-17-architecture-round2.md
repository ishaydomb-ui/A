# Architecture challenge, round 2 — nine questions, then A–F

2026-09-17. **Nothing implemented.** Every number comes from a command
run today against the repo, the live database, or the live carts. Two
of my round-1 recommendations are **retracted** below with the evidence
that overturned them.

---

# A. The nine questions

## 1. Execution item identity

**Finding: `adhoc_requests.id` is not sufficient. Three of five fill
sources drop their identity before the fill starts.**

| Source | Identity at origin | What reaches `add_terms_to_cart` |
|---|---|---|
| ad-hoc request | `adhoc_requests.id` | bare `(term, qty)` — id dropped (`telegram_bot.py:1402`) |
| recipe ingredients | creates `adhoc_requests` rows (`:1803`) | same |
| standing cart / stock | `stock_items (store, product_code)` | `(product_name, qty)` — code dropped (`standingcart.py:102`) |
| base list | `base_list_items.id` | `(search_term_for(store), qty)` — id dropped (`:99`) |
| stock-up / deals | display only → routes to `add_to_cart` | bare terms |

`RefillPlan.terms` is typed `list[tuple[str, int]]`. The fill loop
receives strings. This is why consumption falls back to comparing
request text against product name — the id was there and was thrown
away one function earlier.

**Answer:** no existing entity spans all sources, and none should be
bent to. **A minimal `run_item` is needed — but as the attempt record
itself, not a Basket Plan.** One row per (run, source, store) carrying a
polymorphic source reference:

```
source_kind ∈ {adhoc, base, stock, deal, freeform}
source_id   = adhoc_requests.id | base_list_items.id | product_code | promotion_id | NULL
```

`RefillPlan.terms` becomes `list[PlanTerm]` with `(term, qty, source_kind,
source_id)`. Every source already has the id at the point the tuple is
built; this stops discarding it. Nothing else changes shape.

## 2. Run identity / resume

**Finding: no run identity exists anywhere.** `run_order_cycle` and
`add_terms_to_cart` return a `dict[store, OrderCycleReport]` with no id
and no timestamps. `record_manifest` stamps `at`; `record_cart_failures`
stamps a whole run with one `failed_at` (why 14 failures read as one
instant). `deferred_cycles` is whole-cycle granularity and is only
written when the exit node was down at request time. `last_cycle_details`
in `app_state` is currently invalid JSON.

The same item attempted across cycles today leaves: a `preferred_products`
row (overwritten), possibly a `cart_failures` row, and a manifest entry
if it landed. Nothing says which attempt belongs to which run.

**Smallest missing primitive:** `cart_runs(id, trigger, started_at,
finished_at, status)`, and `run_items.run_id`. That supports:

- **resume** — first `run_item` in the run without a terminal outcome;
- **idempotency** — unique `(run_id, store, source_kind, source_id)`;
  a second attempt in the same run is a *presence check*, not a re-add;
- **deploy recovery** — a `running` run with no `finished_at` after
  restart is `interrupted`, and resumable (§8);
- **terminal completion** — `status ∈ {running, interrupted, completed,
  completed_with_unverified, aborted}` (§7).

## 3. Verification contracts — tested live

**Tiv Taam, read-only, three reads over 100s:** header count, DOM line
count (`.product-in-cart`), panel names, and `cart_summary` **all agreed
(0 / 0 / 0 / ok, ₪0.00)** — the cart is empty after last night's order.
Agreement across four independent signals is what `CART_EMPTY_VERIFIED`
should mean. Add-lag could not be tested: Tiv Taam has no per-item
remove, so no mutation is safely reversible.

**Shufersal: the probe could not run — and that is the finding.** Every
`page.goto` (account, login, cart) timed out at 30s, three times. Meanwhile
the badge selector returned **`"0"` on a page that never loaded**, for a
cart that held ~137 items / ₪1,831 this morning. `cart_summary` returned
`ok=False`. My probe's guard refused the add ("absence cannot be
confirmed") — correctly.

Contracts, given what was observed:

| | ADD_VERIFIED requires | REMOVE_VERIFIED requires | Must return UNVERIFIED when |
|---|---|---|---|
| **Shufersal** | tile flips to in-cart form (`_tile_in_cart`, exists, currently only on the except path) **and** badge delta ≥ 1 within 5s | `article[data-product-code=X]` absent on cart page **and** badge decreased | either read throws; badge reads `"0"` while last known total > 0; cart page yields 0 articles with a non-null total |
| **Tiv Taam** | `.product-in-cart` delta ≥ 1 **and** the name appears in `_cart_line_names` | n/a — no per-item remove exists | either read throws; `_cart_line_count` returns its exception-path `0`; header and DOM count disagree |

Two hard rules from the evidence: **the badge alone is never
sufficient** (it lied live today), and **a count read of `0` produced by
an exception handler is not a count** (`_cart_line_count`, `_cart_count`
both do this).

## 4. Circuit breaker — with confidence, from real signatures

Playwright exceptions are caught as bare `Exception` at 33 sites and
serialised with `str(exc)[:200]`. So classification is by substring, and
these are the classes that actually occur:

| Signature (as it appears in `detail`) | Class | Confidence |
|---|---|---|
| `net::ERR_SOCKS_CONNECTION_FAILED`, `ERR_PROXY_CONNECTION_FAILED`, `ERR_NAME_NOT_RESOLVED`, `ERR_INTERNET_DISCONNECTED`, `ERR_CONNECTION_*` | infrastructure | **high** — trip on 1 |
| httpx `ProxyError`, `ConnectTimeout` (seen in yesterday's retries) | infrastructure | **high** — trip on 1 |
| `Page.goto: Timeout 30000ms exceeded` | **ambiguous** | see below |
| `Target page, context or browser has been closed` | session/browser | recover session, no trip |
| `Session expired and could not be renewed` | session | recover session, no trip |
| `no add control on the row`, `the click did not change the cart`, `אזל מהמלאי` | product (or product-ambiguous) | never trips |

**Why Timeout is ambiguous — demonstrated today, not inferred.** During
the Shufersal timeouts, `curl` through the *same* SOCKS proxy fetched
the Shufersal login page (HTTP 200, 230,946 bytes, 6.8s) and Tiv Taam
(0.7s). The route was healthy; the browser stalled on the site. Yesterday
the identical exception text came from a dead route. One `TimeoutError`
therefore carries no information about the network.

**Recommendation:** high-confidence → trip immediately. Ambiguous →
**two consecutive, then an out-of-band probe** (an httpx GET through the
proxy to the chain's login URL, ~1s): probe fails → trip and recover
route; probe succeeds → it is the site, **back off** (sleep, one retry),
do not trip. No fixed "two failures" rule across classes.

## 5. Barcode identity — measured before designing

**Coverage, weighted by purchase frequency: 89.0%** (unweighted 90%).

**Normalisation:** 293/378 are 13-digit EAN; **63 are 7-digit** — these
are retailer-internal PLUs for produce (`פלפל אדום` = 9913002, `בננות` =
4412616) and **must never be used cross-chain**; 3 start with `2`
(variable-weight prefix); 3 non-numeric; 0 leading-zero. Fold rule: keep
the string as-is; treat length ≤ 7 as chain-local.

**Variable weight:** 69 weighable products; 54 are PLU-coded; 49/69
resolve in the feed. Weight is a quantity problem, not an identity one —
the barcode still identifies the product.

**Collisions:** 251 of 25,291 feed barcodes (1%) carry >1 name, and the
samples are naming variants of one product (three spellings of one
Tabasco). Not a false-equivalence risk. **15 purchased base-names carry
>1 barcode** — pack variants — which is the argument *for* keying on
barcode, not against.

**Cross-chain false equivalence:** of barcodes present at ≥2 chains, **493
name-pairs agree, 29 disagree** on any content word (5.6%). Inspected:
most disagreements are naming ("מי עדן שישייה" vs "מים מינרליים" — the
same water); one or two are suspicious (`חומוס אבי` vs `החומוס סדרה
מובחרת`). Rule: cross-chain equivalence by EAN-13 only, with a name-overlap
sanity check; PLUs never cross.

**How much of normal shopping bypasses name matching entirely:** of the
276 standing-plan terms for Tiv Taam, **228 (83%) resolve by a feed
barcode with no search**; share-weighted **84% of a typical basket**. 27
of those are PLUs (fine within the chain). 20 terms have no barcode at
all and stay on the name path.

**Defect found while measuring:** `stock_items.barcode` is **empty for
all 390 Tiv Taam rows** — my rebuild yesterday from the order lines
dropped the field. The 84% figure was computed from
`data/tivtaam/order_lines.json`, which has them. This is backfill item
D-1, and the barcode-first path cannot ship without it.

## 6. Memory and corrections — round-1 recommendation retracted

**Retraction:** I said "delete the 629 rows where term == product name."
Classified today:

| Of the 629 | Count |
|---|---|
| carry a real product code | **620** |
| code is a product the household bought (`stock_items`) | 290 |
| name matches a bought product | 544 |
| **no code and no purchase evidence — genuinely empty** | **7** |

They are mostly the history import seeding memory with the product's own
name as the term. They are the *best* rows. Only 7 are droppable.

**`remember_choice` is `INSERT OR REPLACE`** (`storage.py:1683`) — every
one of its ten call sites silently overwrites. There is **no negative-
evidence structure** of any kind.

**Is `source + evidence_count + last_confirmed_at` sufficient?** For
*promotion*, yes. For *suppression*, **no** — nothing stops a rejected
mapping from being re-inferred and re-written the next cycle. The
missing half is a rejection record:

```
product_rejections(store, term, product_code, rejected_at, source ∈ {human, order_removed})
UNIQUE (store, term, product_code)
```

Rules: the resolver excludes rejected candidates before ranking;
`remember_choice` refuses to write a rejected pair; a human "X במקום Y"
writes a rejection for Y **and** a `source=human` preference for X; an
item deleted from the cart and absent from the order (`removals_from_order`
already produces this) writes an `order_removed` rejection. Not ML: two
tables and three rules.

## 7. Terminal outcome

**Exists:** `OrderCycleReport` with `added / ambiguous / not_found /
errors / skipped` per store; `format_report_headline`; `_store_cycle_summary`.

**Missing:** the *requested* denominator (the terms list is not
retained), an *unverified* bucket, cross-store consolidation into one
result, and any run status. "17 of 20" cannot be produced today because
20 is not stored.

**Definition:** a run is `completed` when every `run_item` has a
terminal outcome — `verified`, `failed_product`, `unresolved_ambiguity`,
`removed_by_household` — and none is `unverified` or infra-pending. If
`unverified` remains after the verification pass, `completed_with_unverified`.
The user-facing result is one message: counts per outcome, then the
exception list only (ambiguities and failed products). It is computed
from `run_items`, not from the retailer cart.

## 8. Restart / deploy — traced end to end

**Deploy:** `scripts/refresh_bot.sh` → `pgrep -P` on the bot's children →
**exit 3 and refuse** while a cycle drives a browser. A deploy cannot
interrupt a fill. Good, already true.

**Crash / OOM / manual kill:** systemd restarts the service → startup
registers four jobs: `drain_deferred_cycle` (first 120s; only acts on
`deferred_cycles` rows, which exist only if the exit was down at request
time), `watch_list` (first 60s, then 180s; blocked by the 6-hour cooldown
that the interrupted run itself set via `note_ran`), `cadence_check`,
`nightly_learn`. **Nothing looks for an interrupted fill. No durable
"fill in progress" marker exists** (grep for one: none).

**Smallest resume, no engine:** a fifth job on the existing queue,
`resume_runs`, first=30s, once: `SELECT id FROM cart_runs WHERE status =
'running'` → set `interrupted` → re-run `add_terms_to_cart` for the
run's `run_items` lacking a terminal outcome, in the same run id. The
verification contract (§3) turns "attempted, unverified" into a presence
check rather than a re-add, which is what makes resume safe.

## 9. Conversation architecture — preserving optionality

**Seams today:** `nlu.parse_message` has one caller; inside it,
`_ask_model` / `_plan_pass` / `_loop_pass` are three functions. The
harness `scripts/compare_understanding.py` exists and produced the
17s/18s/74s numbers. **No deterministic shortcut exists** (zero regex
rules); "תוסיף חלב" goes to a model today.

**To keep all four options open:** (1) keep `parse_message`'s signature
as the only entry; (2) name the backend — `classifier | planner | agent |
rules` — selectable per run of the harness and by config in production;
(3) add per-message latency and backend to the journal *now*, because
there is currently **zero** production instrumentation and the comparison
cannot be made without it; (4) build `rules` as a harness candidate only
— it is the thing that makes "7–10s is not a fast path" testable. Decide
after the reliability work, on the same 25+ message set.

---

# B. Changes to the build order

| Was | Now | Why |
|---|---|---|
| 1. attempt record | **0. instrumentation + barcode backfill** (prerequisites) | §9 needs latency data; §5 needs `stock_items.barcode` |
| — | **1. `cart_runs` + `run_items`** as one step | they are one schema change; the record is meaningless without the run |
| 2. three-valued results | 2. same, **with the per-chain contracts of §3** | contracts are now specified, not assumed |
| 3. breaker | 3. same, **with confidence classes** | §4: no fixed count across classes |
| 4. barcode identity | 4. same | now measured at 84% of a basket |
| 5. provenance | **5. provenance + rejections** | rejections were the missing half |
| 6. fix `replace_item` | 6. same, **writing a rejection** | it becomes the main human-correction path |
| — | **7. `resume_runs` startup job** | §8: nothing resumes a crashed fill |
| 7. execution service | 8. | — |
| 8–9. planner/session | 9. **measured four-way comparison**, then decide | §9 |

**Dropped from the plan:** deleting the 629 rows.

---

# C. Minimal schema changes

```sql
CREATE TABLE cart_runs (
  id          INTEGER PRIMARY KEY,
  trigger     TEXT NOT NULL,          -- done | watch_list | start_order | resume | manual
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  status      TEXT NOT NULL           -- running | interrupted | completed | completed_with_unverified | aborted
);

CREATE TABLE run_items (
  id           INTEGER PRIMARY KEY,
  run_id       INTEGER NOT NULL REFERENCES cart_runs(id),
  store        TEXT NOT NULL,
  source_kind  TEXT NOT NULL,         -- adhoc | base | stock | deal | freeform
  source_id    TEXT,
  term         TEXT NOT NULL,
  quantity     REAL NOT NULL DEFAULT 1,
  outcome      TEXT NOT NULL,         -- pending | verified | unverified | failed_product | failed_infra | unresolved_ambiguity | removed_by_household
  failure_kind TEXT,                  -- product | session | infrastructure | ambiguous
  evidence     TEXT,                  -- JSON: which signals said what
  product_code TEXT,
  attempted_at TEXT,
  verified_at  TEXT,
  UNIQUE (run_id, store, source_kind, source_id, term)
);

CREATE TABLE product_rejections (
  store         TEXT NOT NULL,
  term          TEXT NOT NULL,
  product_code  TEXT NOT NULL,
  source        TEXT NOT NULL,        -- human | order_removed
  rejected_at   TEXT NOT NULL,
  UNIQUE (store, term, product_code)
);

ALTER TABLE preferred_products ADD COLUMN source TEXT NOT NULL DEFAULT 'unknown';  -- purchase | human | search | inferred | unknown
ALTER TABLE preferred_products ADD COLUMN evidence_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE preferred_products ADD COLUMN last_confirmed_at TEXT;
-- stock_items.barcode already exists; it is empty for Tiv Taam (D-1).
```

In code, not schema: `CartAddResult` gains `verification ∈ {verified,
unverified, n/a}` and `failure_kind`; `RefillPlan.terms` becomes
`list[PlanTerm]`. `status` keeps its current values so every existing
reader is untouched.

---

# D. Migrations and backfills

- **D-1 `stock_items.barcode` for Tiv Taam** — from
  `data/tivtaam/order_lines.json` (378 products, 377 with a barcode).
  Idempotent `UPDATE … WHERE barcode IS NULL OR barcode = ''`.
- **D-2 `preferred_products.source`** — `purchase` where
  `(store, product_code)` ∈ `stock_items` or the folded name ∈
  `stock_items` (≈604 rows); `inferred` where `chosen_at ≥ 2026-09-17`
  and not purchase-backed (the resolver's day); `search` otherwise.
  `evidence_count = 1`, `last_confirmed_at = chosen_at`.
- **D-3 drop the 7** rows with no code and no purchase evidence. Only
  those.
- **D-4 `cart_runs` / `run_items`** — no backfill; historical runs are
  not reconstructable and should not be faked.
- **D-5 `product_rejections`** — no backfill. Optionally seed from the
  eleven repairs made on 2026-09-17 (the old wrong names as `human`
  rejections), since those were explicit corrections.

Every backfill is a `SELECT` first, printed, then applied; each is
re-runnable.

---

# E. Rollback

All changes are additive. Per step:

- **Schema:** new tables can be dropped; new columns are nullable or
  defaulted and ignored by old code. `grocery-backup.timer` already
  keeps a DB copy; take an explicit `cp` before D-1..D-3.
- **Result types:** `CartAddResult.status` is unchanged; new fields are
  additive. Old callers keep working with the new adapters.
- **Breaker:** behind `GORDON_BREAKER=off`; off restores the current
  run-through behaviour exactly.
- **Barcode-first:** behind `GORDON_IDENTITY=barcode|name`; the name
  path is the current path and stays the fallback either way.
- **Rejections:** an absent or empty table means no suppression — the
  current behaviour.
- **Resume job:** its absence means the current behaviour (nothing
  resumes).
- **Code:** one commit per step, each revertible alone; steps 1–6 do not
  depend on each other's code except 6→5 and 7→1.

---

# F. Acceptance tests, steps 1–6

Each is a real test in `tests/`, and each names the defect it prevents.

**Step 1 — runs and items**
- A fill of 20 terms from three sources creates one `cart_runs` row and
  20 `run_items`, each carrying `source_kind` and the originating id.
- After a fill, `adhoc_requests.consumed` is derived from a `verified`
  `run_item`, not from a name compare: the request "חלב עמיד" whose
  product landed as "חלב עמיד 3% 1 ליטר" is consumed. *(Prevents: 17 of
  21 stuck pending.)*
- A second attempt of the same item in the same run is rejected by the
  unique constraint and routed to a presence check. *(Prevents: the
  duplicate `עגבניות שרי במלח`.)*

**Step 2 — verification contracts**
- Shufersal: a click whose tile does not flip and whose badge does not
  move yields `unverified`, never `added`. *(Prevents: "added" meaning
  "did not throw".)*
- Shufersal: badge `"0"` while last known total > 0 yields
  `unverified`. *(Prevents: today's live probe result.)*
- Tiv Taam: `_cart_line_count` raising yields `unverified`, not a
  `before=0`. *(Prevents: exception-path zero as a count.)*
- Tiv Taam: header and DOM count disagree → `unverified`.
- `cart_summary` returning `ok=True, items=[], total>0` is reported as a
  failed read. *(Prevents: ₪1,831 with 0 lines.)*

**Step 3 — breaker**
- One `ERR_SOCKS_CONNECTION_FAILED` stops further mutations in the run;
  remaining items are `pending`, not `failed`. *(Prevents: 14 in a row.)*
- One `Timeout 30000ms` does **not** trip; two consecutive with a
  successful out-of-band probe → back-off, no trip; two consecutive with
  a failed probe → trip.
- After a trip and a successful recovery, the run resumes at the first
  `pending` item and finishes with the same `run_id`.
- No infrastructure or session failure is ever written to
  `cart_failures`. *(Prevents: 15 bogus rows.)*

**Step 4 — barcode identity**
- A Tiv Taam standing term whose barcode is in the feed resolves with
  zero browser searches (assert the search method is not called).
- A 7-digit PLU resolves within its chain and is never matched at
  another chain.
- Two purchased pack variants with different barcodes resolve to their
  own products, not to each other.
- A term with no barcode falls through to the name path unchanged.

**Step 5 — provenance and rejections**
- A `human` correction overwrites an `inferred` mapping; an `inferred`
  mapping never overwrites `purchase` or `human`.
- A rejected `(store, term, product_code)` is excluded by the resolver
  and refused by `remember_choice`. *(Prevents: `בייקון → מצלמה`
  returning.)*
- D-2 classifies the live table with ≥ 600 rows as `purchase` and
  exactly 7 as droppable — asserted against a copy of the real DB.

**Step 6 — `replace_item`**
- "X במקום Y" writes a rejection for Y, a `human` preference for X, and
  — on Shufersal — calls `remove_item` with Y's code; on Tiv Taam, where
  no remover exists, the reply says so instead of implying removal.
  *(Prevents: the silent no-op found in round 1.)*
