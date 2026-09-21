# Gordon vNext — Phase 2b: the assisted Telegram flow (2026-09-21)

Ishay's mock ("Gordon vNext — your proactive grocery assistant on
Telegram", five screens) built as far as Telegram's inline UI allows,
in **assisted mode**: nothing reaches a cart until the household taps
"אשר והכן עגלה", execution goes through the existing engine with the
cart guard on, and the flow stops at the chain's own cart page. There
is no checkout, payment or order-placing code anywhere in this work —
the hard rule in `CLAUDE.md` and `planner.FORBIDDEN` holds. Commits
`37ccc7a`..`d4ed48b` on `claude/gordon-work-mvp-cartpause` (base
`feaba3d`).

## 1. What is wired, and where

| Piece | Where |
|---|---|
| Draft persistence — one open draft per chat, plan + edits + chain toggles + card answers as JSON | `storage.py`: table `vnext_drafts` (additive), `create_/open_/get_/update_vnext_draft` |
| Draft model + the five screens as pure `(text, keyboard)` renderers, nudge suppression rules, execution term builder | `grocery_bot/vnext_flow.py` (new) |
| Telegram handlers: `/plan`, the `vn:` callback router, the nudge, spoken edits on an open draft, execution | `grocery_bot/vnext_handlers.py` (new, `VNextFlow`), owned by `GroceryBot.vnext` |
| Real basket optimizer: per-chain pricing, split-vs-single decision, gift-threshold note | `grocery_bot/basket_optimizer.py` (`price_items`, `cheaper_alternatives`, `quotes_for`, `optimize`) |
| Tunables: nudge cadence/window/snooze, draft TTL, page size, delivery fees, split minimum, waste window, card count | `vnext_config.py` (`nudge_*`, `draft_ttl_hours`, `review_page_size`, `delivery_fee_*`, `split_min_saving`, `waste_window_days`, `stockup_cards`) |
| Engine hook — the resolver's per-chain product goes straight to `add_specific_product` | `orchestrator.add_terms_to_cart(..., identities={(store, term): Identity})`, one additive parameter; without it the engine is unchanged |
| `telegram_bot.py` touch points | `__init__` creates `self.vnext`; `/plan` → `start_proposal`; `cadence_check` tries the nudge first, falls back to the digest; `_handle_message_inner` routes the "הוסף פריט" reply and, with a draft open, `add_item/remove_item/change_quantity/replace_item/start_order` to the draft (after the classifier, no prompt change); `_do_start_order` (spoken "מלא את העגלה") opens the proposal; the recipe preview gains "➕ הוסף להצעת הקנייה הפתוחה" (`rcpdraft:`); `/start` help and `COMMAND_MENU` text for `/plan`; `CallbackQueryHandler(pattern=r"^vn:")` |

Untouched: `execution.py`, `listwatch.py`, `cartpause.py`, `autoresolve.py`,
`nlu.py` (and its prompt), the adapters, schedules, `.env`, systemd.
`/start_order` (the command) still runs the legacy immediate cycle.

### The screens (callback data uses numeric item ids — Hebrew terms would exceed Telegram's 64-byte limit)

1. **Nudge** — from `cadence_check` when readiness is `prepare_now`/`prepare_soon`;
   suppressed (and logged, each reason) when a draft is open, within
   `nudge_min_days` (3) of the last one, snoozed (`לא עכשיו` = 2 days),
   or outside 09:00–21:00 Israel. Buttons `כן, תכין הצעה` / `רק מה
   שחשוב` / `לא עכשיו` / `הצג פריטים`. On the days it fires it replaces
   the digest; otherwise the digest behaves as before.
2. **Proposal** — counts by kind (routine / meal / requests / stock-up /
   cheaper alternatives), expected saving, `הצג את כל הרשימה` / `ערוך /
   הסר פריטים` / `השווה בין רשתות` / `אשר והכן עגלה` / `בטל`. Followed by
   at most one **waste card** and one **stock-up card**.
3. **Review** — 10 lines a page, "✅ חלב (2) ₪11.90", numbered buttons
   open a per-item sub-keyboard (`הסר` / כמות 1–4 / `קח את ההצעה שלך`
   on a decision / `במקום: …` substitutes / `מצא אלטרנטיבה זולה יותר`),
   `◀ ▶`, `הוסף פריט` (the next text is the item, no NLU call). Spoken
   edits while the draft is open apply to it: "תוסיף קוטג" (also still
   a request on the list), "תוריד את הלחם", "בעצם שניים", "לא זה, השני".
4. **Compare** — per-chain basket + delivery, promos, what each chain
   cannot supply, the recommendation (split only when it beats the best
   single chain by `split_min_saving` after both deliveries; otherwise
   single chain with coverage and an honest "the other chain is cheaper
   on the shared items but is missing N"), ✅ chain toggles (at least one
   stays on), `אשר והכן עגלות`, `הצג פירוט מלא`.
5. **Execution** — "מכין את העגלות…" edited live, then per chain "✅ … —
   עגלה מוכנה (N פריטים, ₪X)", gaps named (לא נמצא / לא אומת / כבר היו),
   then **URL buttons to the chains' cart pages**, `השאר לאישור ידני`,
   `שלח לי קישורים`, `בצע שינויים נוספים` (back to review with only the
   items that did not land).

## 2. Rendered screens (real DB copy, 2026-09-21; `…-phase2b-samples/`)

```
שלום!
נראה שכדאי להזמין קניות 😊

• 13 מוצרים עומדים להיגמר
• 4 בקשות פתוחות
• יש מבצעים טובים השבוע

רוצה שאכין הצעת קנייה?
[כן, תכין הצעה] [רק מה שחשוב] [לא עכשיו] [הצג פריטים]
```
```
הנה הצעת הקנייה שלך 🛒 (18 מוצרים)

✅ 13 פריטים להשלמה שוטפת
📝 4 בקשות שלכם
💰 מבצע אחד ששווה לאגור
🔄 3 אלטרנטיבות זולות יותר

סיכום חיסכון צפוי: ₪14
(בהשוואה למחירים רגילים)
[הצג את כל הרשימה] [ערוך / הסר פריטים] [השווה בין רשתות] [אשר והכן עגלה] [בטל את ההצעה]
```
```
מצאתי מבצע משתלם! 🔥

רוטב סויה מופחת נתרן500מ ב-שופרסל ב-31% הנחה
(₪15.90 במקום ₪22.90)

זו לא קנייה קבועה שלך, אבל משתלם לאגור.
להוסיף 2 לעגלה?
[כן, הוסף] [לא עכשיו]
```
```
הנה הרשימה. מה תרצה לשנות?

1. ✅📝 גרנולה ללא תוספת סוכר (1) ₪27.90
2. ✅📝 מלח (1) ₪7.50
3. ✅📝 סילאן (1) ₪19.90
4. ✅📝 שמן זית (1) ₪34.90
5. ✅ פלפל צהוב (0.55 ק"ג) ₪12.90
6. ✅ מלפפונים (0.65 ק"ג) ₪8.90
7. ✅ קשואים (1 ק"ג) ₪8.90
8. ✅ תפוח עץ סמיט (0.52 ק"ג) ₪12.90
9. ✅ הפטרוזיליה (1) ₪5.90
10. ✅ זיתים ירוקים טבעות (1) ₪15.90

עמוד 1 מתוך 2 · סה"כ 18 פריטים
[1] [2] [3] [4] [5]
[6] [7] [8] [9] [10]
[▶]
[הוסף פריט] [השווה בין רשתות]
[חזרה להצעה] [אשר והכן עגלה]
```
```
השוואת מחירים הושלמה ✅

✅ טיב טעם: 17 פריטים — ₪295.79 + משלוח ₪29.90
   לא נמצא שם: סילאן
✅ שופרסל: 15 פריטים — ₪228.78 + משלוח ₪35.90
   כולל מבצע אחד
   לא נמצא שם: גרנולה ללא תוספת סוכר, קשואים, הפטרוזיליה

💡 הצעה: הכל בטיב טעם (מכסה 17/18) — ₪325.69 כולל משלוח
שופרסל זולה ב-₪38.22 על 14 הפריטים המשותפים, אבל 3 פריטים לא נמצאו שם
פיצול היה חוסך רק 5₪ — לא שווה משלוח כפול
[✅ טיב טעם] [✅ שופרסל]
[אשר והכן עגלות]
[הצג פירוט מלא]
[חזרה להצעה]
```
```
(MOCKED — no cart was touched)
🎉 הכל מוכן!

✅ טיב טעם — עגלה מוכנה (18 פריטים, ₪325.69)

רוצה לעבור לתשלום באתר, או להשאיר את העגלות לאישור ידני?
(אני לא משלם ולא מזמין — זה תמיד שלך.)
[🛒 פתח את העגלה בטיב טעם → https://www.tivtaam.co.il/]
[השאר לאישור ידני]
[שלח לי קישורים] [בצע שינויים נוספים]
```
No waste card today (0 waste reports on record).

## 3. Optimizer with real prices

`optimizer_quotes.json`: Tiv Taam prices 17/18 items (from `store_prices`
by barcode, else the price the household last paid for that product
code in `tivtaam_order_lines`); Shufersal 15/18 (from `catalog_products`,
by EAN, `P_` suffix, or exact name). Delivery ₪29.90 / ₪35.90
(`whereto.DELIVERY_FEES`, overridable). On the 14 items both chains
price, Shufersal is ₪38.22 cheaper after delivery, but cannot supply
3 items; a split would save ~₪5 after two deliveries, below the ₪25
minimum, so the recommendation is a single chain with the honest
caveat. The ₪599 Shufersal gift threshold is a note only.

## 4. Confirmation kinds each button records (`vnext_product_confirmations`)

| Act | Kind | Polarity |
|---|---|---|
| `קח את ההצעה שלך` on a decision | `kept_exception_choice` | positive |
| `כן, הוסף` on the stock-up card | `kept_exception_choice` | positive |
| `במקום: <substitute>` | `accepted_substitution` | positive |
| `כן, החלף` on a cheaper alternative | `accepted_substitution` | positive |
| spoken "X במקום Y" while a draft is open | `later_correction` for Y's product | negative |
| `לא עכשיו` (stock-up), `הסר`, quantity, waste card | nothing — a "not now"/quantity decision is not a product judgement |

`preferred_products` and `product_rejections` are never written by this
flow (pinned by tests).

## 5. Tests

`tests/test_vnext_flow.py` — 23 tests: nudge suppression reasons (all
seven), nudge text/buttons, proposal counts, `רק מה שחשוב`, edit/
quantity/remove/re-add/back, chain toggle keeps one, cancel + stale
button, `הוסף פריט` then text, spoken add/quantity/remove on the draft,
no draft ⇒ not handled, every non-confirm button leaves the engine
untouched, confirm calls `add_terms_to_cart` with `guard_cart=True`,
`trigger="vnext"`, `identities`, the pay button is a URL into
`CHAIN_CART_URL` with no callback, no `checkout(`/`pay(`/`place_order`/
`submit_order`/`confirm_purchase` in the flow's source, exit-node down
keeps the draft `confirmed`, optimizer split maths (fees, threshold,
minimum), card kinds, execution screen and term/identity builder,
preferred_products untouched. Phase 2a's `/plan` tests updated to the
proposal (only `vnext_drafts` changes). Full suite: **1546 passed**,
the 4 known pre-existing failures deselected, no new failures.

## 6. Verified read-only where it matters

Samples were generated from a *copy* of the production DB; the live
file's md5 was `10fb7ffe0b26` before and after. The live bot was not
restarted (PID 1409354, Phase 2a code) — deploying 2b is the parent's
anchor decision.

## 7. What the mock asks that Telegram cannot do, and the substitute

- **Tap a list line** — text lines are not tappable; numbered buttons
  (1–10 per page) open the item's sub-keyboard.
- **Chain cards with logos and a checkbox** — text cards with ✅/▫️ toggle
  buttons; logos are not renderable in a message keyboard.
- **Inline quantity stepper** — quick picks 1/2/3/4 (a real stepper would
  cost one edit per tap and hit Telegram's edit rate limit on a 20-item
  run); any other number by text ("קוטג — 6").
- **Long lists** — 4096-char messages ⇒ 10 items a page; "הצג פירוט
  מלא" is sent as a separate message and truncated at 4000 chars.
- **"עבור לתשלום"** — a link to the chain's own cart page (Shufersal
  `/online/he/cart/cartsummary`; Tiv Taam's cart is a header panel on
  the home page, so its link is the site root). Deliberately not an
  action, and it cannot be one under the hard rule.
- **Recipe "add to cart"** — adds the missing ingredients to the open
  draft (and to the list); it does not build a plan on its own when no
  draft is open (a 15–20 s build from a recipe button felt wrong), the
  reply points to `/plan`.

## 8. Before 2b is switched on for real (parent's call)

- The bot must be restarted to load this code (`scripts/refresh_bot.sh`).
- The first real run should be watched: the identities path
  (`add_specific_product` with the resolver's product) is new for
  Tiv Taam codes that are not barcodes — the engine falls back to name
  search when the code does not match, so the worst case is the old
  behaviour, not a wrong add.
- Waste and meal cards are inactive until the household reports waste /
  declares a meal (0 rows today).
