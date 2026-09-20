# Gordon vNext — Clean Migration Phase 1 (2026-09-20)

Shadow-mode decision layer built *above* the existing execution layer.
Nothing in production changed behaviour: no Telegram handler, planner,
cart writer, adapter, schedule, session or preference was touched. The
new layer reads Gordon's tables and produces a `ShoppingPlan` and a
readiness call; it cannot reach a cart.

Branch `claude/gordon-work-mvp-cartpause`, commits `2daa6e4`, `039b75e`,
`96e1c3e`, `5ae2cb4` (+ this report's commit). Base: `e5ff122`.

---

## 1. Exact new files

| File | Role |
|---|---|
| `grocery_bot/vnext_config.py` | `VNextConfig` dataclass — every band/multiplier/cutoff, `GORDON_VNEXT_<FIELD>` env overrides, no inline magic numbers |
| `grocery_bot/household_evidence.py` | 13 evidence types, `Origin` = FACT / HUMAN_DECLARED / INFERENCE, read-only gatherers over `Storage`'s public accessors |
| `grocery_bot/need_engine.py` | per-term `NeedAssessment`: need confidence, quantity (+unit, +confidence), reasons, `auto_include` / `suggest` / `ignore` |
| `grocery_bot/shopping_plan.py` | the one canonical `ShoppingPlan` / `PlanItem`, `build_plan()`, `format_plan()`, `mutates_cart=False` |
| `grocery_bot/shopping_readiness.py` | `assess()` → `Readiness(score, reasons, suggested_action, confidence)`, sends nothing |
| `grocery_bot/basket_optimizer.py` | interface + models only (`ChainEconomics`, `LineQuote`, `BasketQuote`, `OptimizerInput`, `OptimizerResult`); `optimize()` returns `implemented=False` |
| `grocery_bot/telegram_vnext_view.py` | pure text renderers for the three future message shapes; **not wired** |
| `tests/test_vnext_need_engine.py` | 16 tests — the pinned need rules |
| `tests/test_vnext_plan_shadow.py` | 12 tests — zero mutation, plan/readiness shape, view text, optimizer seam, CLI read-only |
| `docs/reports/2026-09-20-vnext-phase1-samples/` | real-data samples (this run) |

## 2. Existing files minimally touched

| File | Diff scope | Why |
|---|---|---|
| `grocery_bot/storage.py` (+127) | three additive `CREATE TABLE IF NOT EXISTS` blocks at the end of `SCHEMA`; six new methods (`add_/list_vnext_planned_meals`, `add_/list_vnext_household_events`, `add_/list_vnext_stockup_rules`) appended at the end of the class | the schema could not carry a planned meal, a household event or a conditional stock-up request without changing an existing column's meaning |
| `grocery_bot/cli.py` (+107) | usage docstring; four `_vnext_*` handlers; four entries in `_DB_ONLY_COMMANDS` | `vnext-plan`, `vnext-readiness` (pure reads), `vnext-add-meal`, `vnext-stockup-rule` (write only to their own additive tables) |

No other existing file changed (`git diff --stat e5ff122`, §9). No existing
column, index, row or accessor was altered.

**One additive schema effect on the production DB, stated explicitly:**
`Storage.__init__` runs `SCHEMA` on open, so the first open with the new
code created the three empty `vnext_*` tables in
`data/grocery_bot.sqlite3` (this happened during the test-suite run, not
from the bot, which has not been restarted). They hold 0 rows. Nothing
else changed — verified by md5 over every pre-existing table before and
after the sample generation (`[]` changed).

## 3. Data model

### Evidence (`household_evidence.Evidence`)

Fields: `type`, `origin`, `term` (normalised via `storage.normalize_term`),
`store`, `product_code`, `product_name`, `value`, `detail`, `observed_at`,
`source_ref` (`table:key`, traceable), `trust` (0–1), `data`.

| Type | Origin | Source today |
|---|---|---|
| `explicit_need` | HUMAN_DECLARED | `adhoc_requests` (`consumed=0`); `consumed` is **not** read as cart presence |
| `explicit_rejection` | HUMAN_DECLARED | `product_rejections` where `source='human'` (`order_removed` is never written) |
| `explicit_preference` | HUMAN_DECLARED (standing list; `preferred_products.source='human'`) / **INFERENCE** (`purchase`/`inferred`/`search` rows) | `base_list_items`, `preferred_products` |
| `purchase_history` | FACT | `stock_items` (tier, share, department) |
| `purchase_recency` | FACT | `last_purchase`, newest `tivtaam_order_lines` date |
| `purchase_cadence` | INFERENCE | median of real order-date gaps (≥3 obs, Tiv Taam lines) → else household gap / share → else household gap; `trust` differs per method |
| `observed_quantity` | FACT | median of `tivtaam_order_lines.actual_quantity` (with unit) → else `stock_items.default_quantity` |
| `planned_meal` | HUMAN_DECLARED (the meal) / INFERENCE (each ingredient, `known_inventory=False`, `likely_have` from `pantry.likely_have`) | `vnext_planned_meals` |
| `household_event` | HUMAN_DECLARED | `vnext_household_events` |
| `waste_report` | HUMAN_DECLARED | `waste_reports` |
| `promotion` | FACT | `dealfill.picks_for` (both chains) + `hotdeals.find` filtered by store (its `chains` arg is ignored upstream — known bug) |
| `price_history` | FACT | `price_stats` (Shufersal `price_history`) — reader exists, not yet consumed by the engine |
| `external_spend_signal` | — | stub, returns `[]`; no source exists |

### NeedAssessment → PlanItem

`term`, `display_name` (household's own words first), `source_evidence`,
`reason`, `confidence`, `quantity`, `unit`, `quantity_confidence`,
`mandatory`, `stock_up`, `meal_or_event`, `candidate_retailers`,
`unresolved_decisions`, `decision`, `human_product` / `inferred_product`
(the inferred one lives only in the plan object), `inventory_known`
(always False in Phase 1), `depletion_inference` (the "probably low"
reasoning, marked `is_inference`), `origin_summary`.

`ShoppingPlan`: `schema`, `generated_at`, `as_of`, `mutates_cart=False`,
`summary`, `caveats`, `items`, `ignored`, `config`. `plan.exceptions` is
the exception view: MEDIUM items only when materially relevant
(stock-up, meal, or within `exception_margin` of HIGH) plus explicit
needs with no known product; the rest are `quiet_suggestions`.

### New tables (additive, household-declared inputs)

`vnext_planned_meals(meal, on_date, servings, ingredients JSON, declared_by, created_at, active)`,
`vnext_household_events(name, on_date, extra_people, note, declared_by, created_at, active)`,
`vnext_stockup_rules(term, min_discount, max_quantity, declared_by, created_at, active)`.

## 4. Sample ShoppingPlan (real household data, as of 2026-09-20)

Full output: `2026-09-20-vnext-phase1-samples/plan.txt` / `plan.json`.

- **73 items**: 20 auto-include, 53 suggested, 647 ignored (tier-D
  history and novel promotion terms that nothing else names).
- Auto-include = **14 explicit requests** (the pending `adhoc_requests`
  backlog from 2026-09-11/16) + **6 routine** Tiv Taam items whose
  measured cadence says overdue (e.g. פלפל צהוב 0.56 ק"ג, תפוח עץ סמיט
  0.54 ק"ג, בצל אדום 0.52 ק"ג — quantities are the median of real
  delivered kilograms).
- 9 stock-up candidates (all `suggest`, never auto), e.g. בצל יבש −38%,
  פלפל אדום −51%, רוטב סויה −31%; two lapsed items with good promotions
  were deliberately *not* stocked up (דבש 455 d, מרק פטריות 477 d).
- Exception view: **41 decisions** (12 more suggestions kept quiet).
  70 of 73 items carry an *inferred* product only; 3 have no known
  product at any chain.
- Depletion states: overdue 20, recently bought 10, approaching due 9,
  likely due 8, lapsed 5, no cadence 21.

## 5. Sample readiness

```
score 0.56 → prepare_soon   (confidence 0.75)
  6 recurring items look due by cadence (inference, inventory unknown)
  14 explicit requests pending — cart presence not verified in shadow mode
  3 days since the last recorded order; household rhythm ~9 days
  9 stock-up opportunities on things the household buys
  estimated basket: 20 auto-include items, 53 to review
  a shop nobody reported yet would not be visible here until the nightly order sync
score parts: essentials=0.262, explicit=0.25, cadence=0.0, meals=0.0, savings=0.05
```

Confidence is capped at 0.75 (`readiness_confidence_cap_cart_unknown`)
because no cart is read.

## 6. Sample Telegram vNext messages (rendered, not sent)

```
בקרוב כדאי להכין קנייה
6 דברים כנראה עומדים להיגמר
14 בקשות פתוחות
מצאתי 9 הזדמנויות חיסכון טובות
```
```
הקנייה מוכנה
20 פריטים
6 צריכה שוטפת
14 בקשות
```
```
צריך ממך 41 החלטות בלבד

• נקניק פפרוני — ניחוש של מוצר, לא אישור שלך
• רצועות / קוביות עוף — ניחוש של מוצר, לא אישור שלך
• דנונה דל לקטוז — ניחוש של מוצר, לא אישור שלך
…
ועוד 33.
```
(Plain text, no parse mode — a `*` in a product name cannot break it.
With a planned meal the first message adds "טאקו מוסיפה N פריטים" and
the second "N לטאקו" — exercised in the tests.)

## 7. Test results

- New: **28 passed** (`tests/test_vnext_need_engine.py` 16,
  `tests/test_vnext_plan_shadow.py` 12).
- Full suite: **1467 passed, 4 failed, 52 subtests passed** (11:43).
  The 4 failures pre-exist on `e5ff122` and are the paused Markdown→HTML
  migration: `test_cli_contract.py::CartCommandContractTest::test_add_to_cart_is_advertised_in_the_help`,
  `test_mdtext.py::RenderingTests::test_a_cart_row_escapes_a_hazardous_product_name`,
  `test_mdtext.py::RenderingTests::test_markdown_balance_is_preserved`,
  `test_multibuy.py::OffersFromCatalogueTest::test_message_escapes_asterisks_in_product_names`.
  No new failures.

Coverage of the required cases: explicit need outranks depletion ✔;
recent purchase lowers confidence ✔; recurring item due after cadence ✔
(a 3-gap cadence only *suggests*; ≥6 observations auto-includes);
waste lowers quantity and quantity confidence ✔; meal creates ingredient
demand with `inventory_known=False` and a pantry staple demoted, not
assumed ✔; good promotion → stock-up candidate ✔; mediocre promotion →
no demand, and no change on a recurring item ✔; inferred product choice
never reaches `preferred_products` ✔; plan + readiness + all three
views + both CLI commands leave every table byte-identical ✔; cart code
(`orchestrator.add_terms_to_cart`, `execution.run_list_items`) mocked to
raise and never called ✔.

## 8. Data deficiencies that limit trustworthy prediction (quantified on the live DB)

1. **No human-confirmed product for any term.** `preferred_products`:
   632 `purchase`, 92 `search`, 54 `inferred`, **0 `human`**. Every
   product choice in the plan is INFERENCE; 70/73 items say so.
2. **Cadence is measured for a minority.** Only **70 of 390** Tiv Taam
   products have ≥3 real order dates (`tivtaam_order_lines`, 20 orders
   back to 2026-02-17); the rest use household-gap/share (trust 0.45).
   Shufersal has **0** measured per-item cadences (`interval_days` is
   NULL everywhere, no per-line table) and its `last_purchase` is stale
   at **2026-09-01** (19 days) because the Shufersal order sync needs a
   logged-in browser page the nightly pass does not hold.
3. **Department is unusable for Tiv Taam:** 390/390 rows are `שונות`, so
   pantryable/perishable reasoning for the primary chain rests on
   `hotdeals.is_stockable` name patterns only.
4. **Cross-chain identity is not resolvable:** Shufersal `stock_items`
   carry no barcode (`NULL`), so the same product at two chains is two
   terms unless the names normalise identically. 44 plan items list
   both chains purely by name coincidence.
5. **Waste: 0 reports. Rejections: 0. Planned meals/events: 0.** The
   waste, rejection and meal rules are tested but inactive on real data.
6. **Cart contents unknown.** No live read in shadow mode, and the
   audit found cart reads unreliable anyway — 14 explicit needs may
   already be in a cart (3 word-matched the Shufersal cart on 09-17).
   `adhoc_requests.consumed` is deliberately not used as a proxy.
7. **Two consumption rules** (orchestrator: `added`; watch_list:
   `verified`) mean "pending" itself is not uniformly meaningful; the
   plan treats every pending row as a need and says so.
8. **External spend signal: no source.** Type defined, reader returns `[]`.
9. **Promotion coverage is narrow:** 28 promotion evidence rows from
   `dealfill`/`hotdeals`; `price_history` exists (128k rows / 6,059
   Shufersal items) but is not yet consumed by the engine.
10. **Session fragility is inherited, not solved:** any Phase 2 execution
    would run through the same Tiv Taam captured session (~16-day life)
    and exit-node route the audit flagged.

## 9. Confirmation: current Telegram/cart behaviour is unchanged

- `git diff --stat e5ff122` touches only the files in §1/§2; `telegram_bot.py`,
  `orchestrator.py`, `execution.py`, `listwatch.py`, `cartpause.py`,
  `autoresolve.py`, `planner.py`, `hybrid.py`, `nlu.py`, `main.py`, the
  adapters, `.env` and the systemd units are untouched.
- `grep -rn "vnext|household_evidence|need_engine|shopping_plan|shopping_readiness|basket_optimizer"`
  across those runtime files returns **0** lines — nothing imports the
  new layer except `cli.py`.
- No scheduled job was added or changed; the live process (PID 1066268,
  started 21:24:47) has not been restarted and does not load this code.
- `cart_paused:*` flags unchanged (both chains live, as before).
- No new Telegram message path exists; `telegram_vnext_view` is called
  only from tests and the sample script.
- Every pre-existing table md5-identical before/after generating the
  samples; the only schema effect is the three empty additive tables.

**Phase 2 (wiring, live execution, proactive messages) is not started
and needs approval.**
