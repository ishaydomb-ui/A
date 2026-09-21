# Gordon vNext — Phase 2a: confirmations, speed, a read-only Telegram surface (2026-09-21)

The additive, non-executing half of Phase 2. vNext still adds nothing to
a cart: `/start_order`, `watch_list`, `/done` and the refill fill carts
exactly as before, and no vNext module imports an adapter. Branch
`claude/gordon-work-mvp-cartpause`, commits `ce8062d`, `7a0e822`,
`dc35243`, `63f9fd3`, `2aac435` on top of `7c67444`. (`30579ad`, in the
same range, is the parent session's own anchor, not part of this work.)

## 1. What was wired, and where

### Human confirmations accumulate from real acts (`ce8062d`)

| Household act | Where | Row written |
|---|---|---|
| a disambiguation tap chooses product X for term T at store S | `telegram_bot.py:2696` (`resolve_ambiguity`, after the add succeeded) | `explicit_statement`, positive, `confirmed_by` = the tapping user's id |
| "שנה" after a tap (undo) | `telegram_bot.py:2258` (`on_choice_followup`, beside the existing `reject_product`) | `later_correction`, **negative**, the rejected product code |
| a spoken replacement "X במקום Y" | `replace.py:66` (old) and `replace.py:83` (new) | `later_correction` negative for Y; `accepted_substitution` positive for X |

- New additive column `vnext_product_confirmations.polarity`
  (`positive` default / `negative`), added through `_ADDED_COLUMNS` so
  the live DB gets it on the next `Storage` open; `later_correction`
  defaults to negative (`vnext_confirmations.py`).
- `vnext_confirmations.note_interaction` never raises: a storage error
  is logged and the handler's reply is unchanged (tested).
- The resolver (`vnext_resolver.human_confirmations` /
  `human_rejections`) reads the **newest row per (store, product)**:
  negative rows join the rejection set and are never candidates; a
  later positive row re-confirms.
- `autoresolve` writes nothing here (asserted by a test that greps its
  source) and vNext still writes nothing to `preferred_products`.
- `kept_exception_choice` has no source yet: nothing offers an
  exception choice in production until vNext's `/plan` decisions get
  buttons (2b).

### Plan build: 123 s → ~15 s cold, ~2 s warm (`7a0e822`)

Measured on the real DB with `/usr/bin/time`, `vnext-plan --json`:

| | wall time | method |
|---|---|---|
| before (`7c67444`) | **123.4 s** | one `LIKE '%…%'` per term × qualifier word × chain over ~300k `store_prices` rows (0.3 s each, ~500 queries) |
| after, cold process | **16.5 s** (15.3 s in-process) | `vnext_catalogue`: newest name per product read once per chain (Tiv Taam 25k names 0.55 s, Shufersal catalogue 5.8k), searched with one C-speed `str.find` over a joined blob; semantics identical to the SQL helpers (pinned by `test_agrees_with_the_sql_helper`) |
| after, warm (second build in the same process) | **1.9 s** plan, 2.1 s readiness | shortlist cached per DB path (TTL `catalogue_cache_ttl_seconds`=900), promotion pickers cached (`promotions_cache_ttl_seconds`=600), Hebrew normalisers in `vnext_semantics` memoised (7M `translate()` calls per plan before) |

`EXPLAIN QUERY PLAN` confirmed a substring LIKE cannot use an index, so
no index was added; the shortlist replaces the queries instead. The
remaining cold cost is the existing `dealfill.picks_for` /
`radar.find_stockup_deals` (~13 s, 283 `fold()`-UDF searches) — production
code the promotions cache now runs once per ten minutes. Real-DB plan
output before/after: identical items, products and decisions (58 items).

### Read-only Telegram surface (`dc35243`, `63f9fd3`)

- `/plan` — `telegram_bot.py:673` `vnext_plan`: sends "מכין תוכנית… (רק
  תוכנית — לא נוגע בעגלה)", builds the plan in `asyncio.to_thread`,
  edits the message with the prepared-plan view + the true decisions +
  the count of inferred product choices. Plain text, no parse mode.
- `/readiness` — `telegram_bot.py:708` `vnext_readiness`: readiness head
  + Hebrew details from the signals (`telegram_vnext_view.readiness_details`,
  new). The engineering `reasons` list stays in the CLI/JSON.
- `/requests` — `telegram_bot.py:2474` `_vnext_request_estimates`: a
  block "🔎 לפי ההזמנות שהגיעו אחרי הבקשה" with one line per pending
  request: "כנראה כבר נקנה ← <line> (<date>)" / "לא בטוח" / "פתוח".
  Display only; `adhoc_requests` rows are not touched (dump-compared).
- `/start` help: two new lines for `/plan` and `/readiness`.
- Registrations at `telegram_bot.py:3275-3276`; both gated by `_authorized`.
- Both handlers run with `execution.run_list_items` and
  `orchestrator.add_terms_to_cart` patched to raise in the test — a cart
  write from either path fails the suite.

### Command menu (`dc35243`, `2aac435`)

The audit said no menu existed; in fact `_register_bot_metadata` listed
15 of 21 commands and a Telegram error there would have stopped
startup. Now `COMMAND_MENU` (`telegram_bot.py:3032`) lists every
registered command with a one-line Hebrew description, the call is
wrapped (logged, never fatal), and two tests tie the menu to the
`CommandHandler` registrations (`tests/test_vnext_telegram_surface.py`,
`tests/test_command_menu.py`). The earlier decision to keep
`/price`, `/deals`, `/refresh_prices` unlisted is reversed, deliberately.

Menu, in order: start, list, **plan**, **readiness**, price, deals,
stockup, chaindeals, alldeals, basket, lastdeals, cheaper, failures,
questions, autochoice, requests, list_full, digest, start_order, done,
refresh_prices, pausecart, resumecart (23). `/propose` stays retired
and unlisted.

### Natural-language routes — deliberately not done

"מה חסר" / "צריך קניות" are not routed to the plan/readiness reply.
That needs a change to `nlu.py`'s prompt or intent set, which is a
separate approval (the classifier prompt is measured at 84/84 and any
edit re-opens that). The two slash commands exist and are in the menu.

## 2. Rendered samples (real DB, 2026-09-21 morning)

`/plan` (`2026-09-21-vnext-phase2a-samples/plan_telegram.txt`):

```
📋 תוכנית בלבד — לא נגעתי בעגלה

הקנייה מוכנה
17 פריטים
13 צריכה שוטפת
4 בקשות

צריך ממך החלטה אחת בלבד

• לחם חיטה מלא דגנית עין בר — המותג לא נמצא — לקחת לחם חיטה מלא אמריקן סנדוויץ'?

57 מוצרים בחרתי לפי ההיסטוריה — אפשר לתקן
```

`/readiness` (`…/readiness_telegram.txt`):

```
בקרוב כדאי להכין קנייה
13 דברים כנראה עומדים להיגמר
7 בקשות פתוחות
מצאתי הזדמנות חיסכון טובה אחת

• עברו 3.5 ימים מההזמנה האחרונה; הקצב הרגיל ~9 ימים
• 4 בקשות פתוחות, 3 לא בטוחות, 7 ישנות כנראה כבר נקנו
• סל משוער: 17 פריטים, עוד 41 להצעה
• החלטה אחת שלך
• לא קראתי את העגלה — ייתכן שחלק כבר בפנים
```

DB md5 before and after generating both: `18a57261188a` = `18a57261188a`.

## 3. Tests

- New: `tests/test_vnext_confirmations_wiring.py` (9),
  `tests/test_vnext_catalogue.py` (5), `tests/test_vnext_telegram_surface.py` (6);
  `tests/test_command_menu.py` updated to read `COMMAND_MENU` (5).
- Full suite: **1523 passed**, plus the same 4 pre-existing failures
  (`test_cli_contract`, 2× `test_mdtext`, `test_multibuy` — the paused
  Markdown→HTML migration), re-run separately and still the only ones.
  No new failures.

## 4. Confirmation: cart-writing paths unchanged

- `git diff --stat 7c67444 HEAD -- grocery_bot/orchestrator.py
  grocery_bot/execution.py grocery_bot/adapters grocery_bot/listwatch.py
  grocery_bot/cartpause.py grocery_bot/autoresolve.py grocery_bot/nlu.py`
  → empty.
- `grep -l adapters grocery_bot/vnext_*.py need_engine.py shopping_plan.py
  shopping_readiness.py household_evidence.py telegram_vnext_view.py` → none.
- `telegram_bot.py` changes are additive: two new handlers, one
  display block under `/requests`, two help lines, the menu table, and
  two `note_interaction` calls placed *after* the existing writes they
  mirror; `watch_list` (silent since `555761c`) is untouched.
- `replace.py`: two `note_interaction` calls beside the existing
  `reject_product` / `remember_choice`; the cart run it performs is the
  same call as before.
- Schema: one additive column (`polarity`), two read helpers
  (`store_price_names_latest`, `catalog_names_all`). No row of any
  existing table is written by anything new.
- The live bot (PID 1374430) was not restarted by this work.

## 5. What 2b needs

1. **A Shufersal cart read that can be trusted** — the audit's
   "`ok=True` with zero lines against a ₪1,831 total" is unchanged; the
   Tiv Taam read was fixed this morning (`_cart_lines_from_dom`). Until
   Shufersal's is, vNext cannot know what is already in that cart, so
   `cart_state=unknown` stays a plan-level caveat.
2. **One consumption rule.** `orchestrator` consumes on `added`,
   `watch_list` on `verified`; 2b executing a plan through either path
   inherits whichever it picks. Decide and unify before vNext writes.
3. **Confirmations before auto-choice is trusted.** Today: 0 rows. The
   three sources are live from this commit; a product should need at
   least one positive confirmation (or a purchase count above
   `resolver_evidence_bonus_cap`'s threshold) before a 2b fill adds it
   without listing it in the exception view — the exact bar is a
   `VNextConfig` field to set, not a code change.
4. **Buttons on the exception view** (`/plan`'s decisions) so the
   household's answer can become a `kept_exception_choice` — the fourth
   confirmation kind, still without a source.
5. **The nlu route** for "מה חסר" / "צריך קניות" (separate approval).
