# Gordon — Final Reliability + Agent UX Build: Report

Date: 2026-09-17. Branch `claude/online-grocery-automation-b7pq4g`. Baseline commit `99acd89`; final commit = the Phase 12 commit at the head of this branch (`git log -1`). Everything below was measured on this server or in this suite; where something was *not* measured, it says so.

## 1. Final architecture as actually implemented

```
Telegram handler (telegram_bot.py)          — conversation only
   │  parse_message (nlu) → classifier / planner / loop / rules
   ▼
execution.py                                 — blocking halves, thread-run
   refill · run_list_items · mark_shopped · log_removals_* · read_carts
   finish_run · interrupt_open_runs · resume_interrupted
   │
   ▼
orchestrator.py                              — one need at a time
   add_terms_to_cart / run_order_cycle / resume_run
     ├ identity.resolve      (barcode → feed row → retailer code)   Phase 6
     ├ remembered choice     (preferred_products, ranked by source) Phase 8
     ├ resolver / autoresolve (rejections excluded)                 Phase 8
     ├ presence_check        (present | absent | unknown)           Phase 2/3
     ├ breaker.Breaker       (classify → observe → recover ×2)      Phase 4
     └ _outcome_for          (verified | unverified | failed_* | …) Phase 2
   │
   ▼
adapters/shufersal.py · adapters/tivtaam.py  — click and read; never checkout
   cart_summary waits for settle; add verified by tile / two signals
   │
   ▼
storage.py (SQLite, WAL)
   cart_runs (status, outcome) · run_items (identity = household need)
   preferred_products (+source, evidence_count) · product_rejections
```

Durable state per run: `cart_runs` (trigger, lifecycle status, terminal
outcome) and `run_items` (one row per household need, UNIQUE(run_id,
source_kind, source_id), outcome, failure_kind, evidence, product_code).
Every report carries its `run_id`; the household message is built from
run items (`outcome.py`), not from adapter prose.

Model vs. deterministic split, as built: the model handles language,
context, planner strategy and genuine ambiguity (`nlu`, `planner`,
`convo`); everything from product identity down is deterministic and
tested. No per-item model calls were introduced; no workflow engine,
queue, ML confidence or ontology was added.

Safety, deterministic: `planner.FORBIDDEN` (parse-time denylist),
`hybrid.CART_TOOLS` (refused on the loop path), `hybrid.TOOL_TO_INTENT`
(the effective allowlist — an unmapped tool never runs; pinned by
`test_hybrid.ToolAllowlistTests`). The only cart mutations that exist
are add, set-quantity, and Shufersal per-line remove. Tiv Taam
`clear_cart` was deleted.

## 2. Schema changes and migration counts

| Change | Kind | Counts |
|---|---|---|
| `cart_runs`, `run_items` | new tables (Phase 1) | created empty; **no historical runs fabricated** |
| `stock_items.barcode` backfill | data (Phase 0) | 377 updated, 13 unfillable, re-run 0 |
| `preferred_products.source / evidence_count / last_confirmed_at` | columns (Phase 8) | 782 rows → **628 purchase / 57 inferred / 92 search**; **5 empty-code rows dropped** (777 remain); re-run 0 changes |
| `product_rejections` | new table (Phase 8) | 0 rows — only explicit corrections write it |
| `cart_runs.outcome` | column (Phase 11) | set on every close since deploy |

Every data migration ran as preview → counts reported → backup → apply →
re-run (0 changes). Backups: `data/backups/grocery_bot.pre-reliability-build.20260917-163229.sqlite3`
and `…pre-phase8.20260917-180650.sqlite3`, both `PRAGMA integrity_check` ok,
directory git-ignored. The 629 self-name rows were not deleted (620 of them
carry product codes; the "delete" recommendation was withdrawn on evidence).
`evidence_count` is 1 everywhere: `stock_items.picked_count` is 0 across the
board, so evidence accrues from now, not retroactively.

## 3. Code deleted / replaced / simplified

- **Deleted:** `TivTaamAdapter.clear_cart` and its three private constants
  (no caller; not a cart-preparation operation). The old
  `_remove_from_cart` in the handler (called a method no adapter had).
  The two duplicate live-view runners (folded into `_run_with_live_view`).
- **Replaced:** name-compare consumption of ad-hoc requests → run-item
  outcomes; "did not throw" → verification evidence; `INSERT OR REPLACE`
  preferences → ranked writes; replace-by-name → `replace.py`.
- **Moved out of the Telegram class:** refill, removals logging,
  mark-shopped, watcher run half, cart reads, run close, resume
  (`execution.py`). `telegram_bot.py` 3,283 → 3,121 lines with more
  behaviour, not less.
- **Flags added for rollback:** `GORDON_BREAKER=off`, `GORDON_IDENTITY=name`.

## 4. Before / after reliability metrics

| Property | Before (measured 2026-09-17 AM) | After |
|---|---|---|
| Ad-hoc requests left pending after they landed | 17 of 21 (name compare) | consumed by run outcome; only `verified` consumes |
| Shufersal cart read | 0 lines beside a mid-computation total, `ok=True` | settle wait; 100 lines, ₪1,867.73 stable ×5; `complete=False` at the 100-line cap |
| "added" without evidence | reported as success | `unverified`, named in the message, presence-checked before any replay |
| Tiv Taam count read failure | `0` (from an exception) used as *before* | `None` → unverified, never a false success |
| Route flap mid-run | one failure per remaining item | breaker: classify → probe out of band → recover (≤2) → resume same run; no cascade (test A) |
| Crash mid-fill | run lost; watcher cooldown blocked retry | `resume_runs` 30 s after start, same id, cooldown bypassed |
| "X במקום Y" | added X, silently left Y, claimed an attempt | reject Y, add X, human-prefer X, remove Y where supported, else say so |
| Preference authority | every write equal | human > purchase > inferred > search; no downgrade; rejections final |
| Terminal message | bucket prose per store | one of four states from run items; gaps named; no infra narration |

Unit/regression suite: 1,196 passing at Phase 0 → **1,263 passing** at the end (Phase 12 tree, run alone after the OOM kill); every phase committed only after a full green run.

## 5. Live benchmark results

**What was run live today (through the Israeli exit, real accounts):**

- Route: Bezeq/Jerusalem exit available; `ensure_israeli_exit` failover
  observed once at 6.2 s (to Uset-PC) during the morning's flapping.
- Shufersal: session 18.2 s; cart read 47.4 s then 37.2 s (settle wait
  included), 100 lines, `complete=False`, ₪1,867.73 both times.
- Tiv Taam: four signals agreed on an empty cart three times (0/0/0/₪0.00)
  — the `CART_EMPTY_VERIFIED` definition; one read during a route drop
  returned `ok=false`, not "empty".
- Identity resolution, no browser: Tiv Taam plan 276 needs, 267 stock →
  **228 by barcode (85%)**, 39 by retailer code, 0 unresolved. Shufersal
  plan 164, 158 stock → 158 by retailer code (Shufersal stock rows carry no
  barcodes; the `P_` code is stable there).
- Presence checks (cap rule live): **not completed.** The canary was
  killed by the host for low memory during the Shufersal presence loop
  (20 checks × a ~40 s cart read each), together with the full test
  suite running alongside it and the three live bots. Nothing was
  mutated; the live bot and its siblings survived. Operational rule
  from this: one Playwright probe *or* one full suite at a time on this
  box, never both.
- Stale-request check on the Shufersal cart (100 of ~130 lines read):
  3 of the 14 word-match a cart line (שמן זית, מלח, גזר); 11 are
  inconclusive on a partial read.

**What was not run: the 20-item mutation benchmark and the five clean
household runs.** Reasons, stated plainly: (1) the household's Shufersal
cart is the live Friday proposal (~130 lines); a synthetic 20-item add is
not removable beyond line 100 and would sit in a real order; (2) the Tiv
Taam cart was just ordered and refilled by hand — a refill now is off
cadence and would be a fabricated basket, which the mandate forbids; (3)
Tiv Taam has no per-line remove, so a canary there is not reversible by
the bot. The instrumentation for the benchmark is in place (RUN/ATTEMPT/
BREAKER/RECOVERY/PRESENCE/RESUME journal lines, `cart_runs.outcome`) and
the metrics table below fills itself from the next real runs. **Decision
needed from Ishay:** which real runs count (the next Liran list via the
watcher, the next Tiv Taam refill, or a Shufersal add-then-remove window
he names), and whether the 14 stale ad-hoc rows from 2026-09-16 should be
marked consumed (3 word-match the Shufersal cart; 11 are inconclusive on
a 100-line read).

Per-run metrics that will be recorded (from `run_items` + journal):
requested · resolved by identity · wrong product (household correction) ·
verified · unverified · ambiguous · product failures · infra failures ·
session failures · recoveries · duplicate adds · manual intervention ·
elapsed · model calls · browser ops · outcome.

## 6. Known remaining failure modes

1. **Shufersal cart page cap (100 lines):** presence beyond the cap is
   `unknown`, and `remove_item` cannot reach lines past 100. Pagination /
   scroll unverified — the probe that was to check it died on a route drop.
2. **Tiv Taam has no per-line remove:** replace and undo are add-only
   there; the message says so. `CART_LINE_REMOVE_SELECTOR` is the seed.
3. **Route instability:** the exit node changed three times today. The
   breaker covers mid-run drops; a drop during `ensure_session` still
   surfaces as a session failure for that store.
4. **Unverified is still possible:** when the tile / two-signal check
   cannot be read, an add is reported unverified rather than hidden, but
   the household still has to look.
5. **Stale ad-hoc rows** (pre-Phase-1) are ignored by the watcher's
   backlog rule; a manual `/start_order` would try them again, guarded by
   the cart manifest — decision above.
6. `execution.log_removals_from_carts` calls `ensure_session`, which the
   Tiv Taam adapter names `is_session_valid`; the pre-existing handler had
   the same call. Fixed in the Phase 12 commit.
7. **Host memory:** a Playwright probe and the full suite together got
   both killed (the box also runs two other households' bots and an
   Immich stack). A live fill under the bot is one browser and is fine;
   probes and suites must be serialised.

## 7. Feature flags that can now be removed

None yet. `GORDON_BREAKER` and `GORDON_IDENTITY` exist for rollback of
code that has passed unit tests and read-only live checks but has **not**
yet carried five real runs. Remove both after the benchmark passes;
neither has a non-default value set anywhere.

## 8. Conversation-backend comparison

**Not run — by the mandate's own gate** ("Do NOT change the classifier/
planner/Agent SDK architecture until execution reliability passes the live
benchmark"). What exists for it: `parse_message` is the single seam;
`ParsedMessage.backend` and the `MSG backend= understand_s= total_s=`
journal line record every real message. Zero `MSG` lines since the 17:58
deploy — no traffic yet — so no numbers can honestly be quoted.

## 9. Persistent Agent SDK — recommendation

No recommendation, on the same grounds: nothing was measured. The
execution service now exposes exactly the deterministic tools an agent
would be given (`execution.*`, `replace.replace_product`,
`identity.resolve`), all of which sit below verification, run state,
safety and identity rules — so a later agent cannot bypass them by
construction.

## 10. Next three highest-value improvements

1. **Run the live benchmark on real household runs** and let
   `cart_runs.outcome` decide — nothing else should be prioritised above
   five clean runs.
2. **Shufersal cart pagination** (or scroll) so presence and remove work
   past line 100 — the one structural gap in verification.
3. **Tiv Taam per-line remove**, verified live, so replace/undo are
   symmetric across chains.
