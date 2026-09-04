# What מירי can ask גורדון

The contract between the household's assistant (`~/familyos`) and this
project. **Written from this side; the routing itself is Miri's to
implement, in her own session.** Nothing here changes without the user.

The seam is a CLI, deliberately — each project has its own virtualenv, so
a shared import would couple their dependency trees. A subprocess call
has neither problem.

    cd /home/codex/grocery-automation
    .venv/bin/python -m grocery_bot.cli <command> [args]

Set `GROCERY_BOT_DB_PATH=/home/codex/grocery-automation/data/grocery_bot.sqlite3`.
**Nothing below needs this bot's Telegram token, a store session, or the
Israeli exit node.** That is deliberate: familyos should not hold a
credential it has no use for.

Every command prints one short line (or a short block) on stdout and uses
exit codes: **0 = done, 1 = nothing matched, 2 = usage error.** Exit 1 is
a real answer ("not on the list"), not a failure to retry.

---

## State of the wiring — corrected 2026-09-02

**All eleven commands below are wired and reachable from Miri.** Verified
by reading the familyos code, not assumed: `actions/groceries.py` wraps
each one, `bot/intent.py` classifies `grocery_recipe`,
`grocery_meal_plan`, `grocery_price` and `grocery_deals` among others,
and `bot/telegram_bot.py` dispatches to the wrappers.

This section previously claimed the opposite — that Miri routed free text
to `add-item` and called nothing else, so "תכנן לי תפריט שבועי" would land
as a list item with that name. **That was wrong**, and the mistake is
worth recording because of how it happened: it was inferred from
`HANDOFF.md` §4a, a note in *this* repo describing the other project,
rather than from the other project's code. A note about someone else's
system is evidence of what was true when it was written, and nothing
more. `actions/groceries.py` had wrapped meal-plan, recipe, recipe-text,
add-to-cart, list-items, nudge and confirm-card since 2026-09-01 — a day
before the claim was written.

One real gap did exist and is now closed: `price` and `deals` sat behind
`Config.from_env()` here and could not be called without this bot's
Telegram token. That was fixed on this side and wired on the familyos
side the same day (`familyos@9ac9538`).

---

## Commands

### Meal planning and recipes — the ones worth wiring first

The user named these as the most relevant to how he and Liran actually
talk. All three call a model (via the `claude` CLI), so they take
**roughly 10–40 seconds**. Miri should say something before waiting.

| Command | What it does |
|---|---|
| `meal-plan [request] [--by NAME] [--all] [--preview]` | Five weekday dinners plus one consolidated ingredient list |
| `recipe <dish> [--by NAME] [--all] [--preview]` | One dish → its buyable ingredients |
| `recipe-text [--by NAME] [--all] [--preview]` | Same, from recipe text on **stdin** — the OCR/screenshot/paste path |

**`--preview` adds nothing** and prints what *would* be added. Use it
when Miri wants to show the split and ask first. Without it, the
ingredients go straight onto the list. `--all` queues everything
including what the household probably already owns.

The default behaviour is the important one: **only what the household
probably lacks is added.** Ingredients are split against the pantry, and
the ones it likely has are printed with `~` instead of `+`. Blindly
queueing flour and sugar for a kitchen that owns flour and sugar
recreates the delete-by-hand chore this project exists to remove.

Real output, run 2026-09-02:

    $ meal-plan "משהו קליל, בלי בשר אדום" --by ישי --preview
      ראשון: פסטה ברוטב עגבניות עם קוביות טופו וקישואים
      שני: שניצל עוף בתנור עם פירה דלעת וגזר
      ...
    meal plan: תפריט שבועי
      + פילה עוף (לשניצל) (800 גרם)
      + ביצים (6 יחידות)
      ~ פסטה (פנה) (כנראה יש)
      ~ קוטג' 5% שומן (כנראה יש)
    preview only — nothing added (7 would be added)

The plan is grounded in the household's own recurring products, not a
generic healthy-menu answer — that is why it is worth calling this
rather than answering from the model directly.

### The list

| Command | Notes |
|---|---|
| `add-item <text> [--by NAME] [--qty N]` | An exact repeat folds onto the pending row and prints `already on the list` |
| `remove-item <text>` | Fuzzy match — `remove-item עגבניות` removes `עגבניות שרי`. Exit 1 if nothing matched |
| `list-items` | One per line, with who asked |

`--by` matters: it is what makes `🙋לירן` appear beside the item in the
cart view, so the household can tell a personal request from a standing
one.

### Prices and deals

| Command | Notes |
|---|---|
| `price <query>` | Current shelf price, promotions, and ₪/kg with 🏆 on the best value |
| `deals` | Live promotions on the household's standing list |

Both were behind the Telegram token until 2026-09-02 and are now
token-free — this is new, and the reason "כמה עולה קוטג" could not be
asked through Miri before. Wired on that side in `familyos@9ac9538`,
which also had to teach the classifier that "כמה עולה" (what does it
cost now) is this, while "כמה הוצאנו על" (what did we spend) belongs to
the budget bot.

### Benefits catalog — new 2026-09-03, not yet wired on Miri's side

| Command | Notes |
|---|---|
| `benefits-catalog [query] [--json]` | Harvested benefit-club stores: wallets, discount ceilings, cities |
| `benefits-branches [query] [--json]` | Street addresses + phone for those stores (partial — the crawl is incremental) |

**Two clubs are in there now**, and each row carries a `club`:
**בהצדעה** (982 stores, manually tagged, rescued) and **מקס** (~11,300
discounts harvested from MAX's public API, 2026-09-03). No query returns
everything; a query does a plain substring match on store name, category,
city or region (or address, for branches).

**The two clubs carry different columns, on purpose.** behatsdaa rows
have `ארנקים` and `תקרת הנחה כוללת ₪` because it is a prepaid wallet;
MAX rows have `הנחה%`, `כתובת`, `עיר`, `אזור` and no ceiling, because a
card-linked discount has no balance to cap. An empty ceiling on a MAX row
means MAX has no such concept — not that the harvest missed it.

**Filtering by club is exact-match on the `club` field, not a search
term.** Searching "מקס" also matches "מקסיקנה", which is a behatsdaa
restaurant — so `club` is deliberately excluded from the substring
search. Use `--json` and filter on `club` when the distinction matters.
`--json` returns the full row set as a JSON array — use it for bulk
ingestion (the refinement work — addresses, relevance, whatever Miri
builds on top — is explicitly *her* side, not duplicated here).

Real output, run 2026-09-03:

    $ benefits-catalog מקסיקנה
    *קטלוג הטבות* — 1 תוצאות עבור "מקסיקנה"
    • רשת מקסיקנה — מסעדות ובתי קפה
       מסעדות(20%/₪500); פייטר(15%/₪2500) · תקרה ₪3000 · ערים: תל אביב - יפו; ...

    $ benefits-branches כפר סבא
    *סניפים* — ... תוצאות עבור "כפר סבא"
    • רשת מקסיקנה (מקסיקנה - כפר סבא)
       התעש 24 כפר סבא · 1700500993

**What this is not, yet:** no purchase history, no wallet balances, no
vouchers, no live deals — the harvest itself is blocked on a login (see
`docs/BENEFITS.md`). This is catalog data only: which stores participate,
at what rate, up to what ceiling.

**Freshness — it is a static snapshot, not live.** behatsdaa data is
as-of **2026-09-03** and does not refresh (its login is not automated);
MAX is also 2026-09-03 but re-runnable. Every result line carries the
as-of note for the club shown, and `benefits-catalog --freshness`
(add `--json` for a map) returns the status per club — use it when you
need to state how current a number is, e.g. answering another bot.

### What the benefits seam can and cannot answer (verified 2026-09-04)

For capability tests (Arthur's Domain 2). **Only two verbs exist today**,
both plain substring search — `benefits-catalog [q] [--freshness]` and
`benefits-branches [q]`. **`check` / `quote` / `plan` do NOT exist**; they
are designed-only. Three hard limits, all verified in the data:

- **No `holder` field anywhere**, and **הר"י was never harvested.** So
  "can Liran use the הר"י benefit" (Q9) cannot be answered from data — the
  correct behaviour is to say so, not to attribute anything. Reporting a
  yes here would be a fabrication.
- **No coordinates and no stored home/work location.** MAX's lat/long were
  dropped at harvest; only street + city remain. So **"near me / near
  home" (Q4, Q5) cannot compute distance** — at best a city filter *if the
  user names a city*. The honest answer includes "distance unknown."
- **No per-benefit expiry dates.** "Still valid?" (Q8) resolves only to
  the catalogue's as-of date via `--freshness`; there is no exit-code-3
  "found but stale" per benefit.

Per-question verdicts (keep unless noted):

1. **פוקס** — keep, good merchant-identity test. The catalogue holds
   `פוקס`, `פוקס אונליין`, `פוקס הום אונליין`, `terminal x` as *separate
   rows* with different wallet coverage; the seam cannot unify the group,
   so pass = return candidates, not one answer.
2. **שילב 400** — **correct the ground truth.** שילב is on ראש-השנה/
   הוקרה/רשתות/פייטר (30/25/15/15%), **not** the 7% food wallet — so the
   ₪700 *monthly* cap does not apply; those wallets carry `maxBalance`
   (₪500–2,500), not a monthly deposit cap. Tests `quote` (not built).
3. **plan 3000** — `plan` not built; and the ₪700/month schedule is
   food-wallet-only, so a clothing/general cart doesn't fit it. Keep as a
   designed-capability marker, flag NOT-YET-BUILT.
4/5. **near home / near work** — keep but reframe to city-filter; both
   need a location source we don't have. Consider merging into one.
6. **wallet vs plain at Tiv Taam** — keep; answerable from rates. Nuance
   to encode: the food-card discount is earned *on load, not on spend*
   (`grocery_bot/benefits.py`), and the compare must not encourage
   spending.
7. **⛔ browse** — keep, essential negative test; pass = refuse/redirect.
8. **שופרסל still valid** — keep, and note **Shufersal takes no loadable
   card at all**, so a "benefit at Shufersal" is itself a category error a
   good answer catches.
9. **הר"י holder** — keep as a *negative* test (see limit above).
10. **חלב price** — keep; routing test, belongs to `price`/`chaindeals`,
    not the benefits seam.

### Cadence and the card

| Command | Notes |
|---|---|
| `nudge [--last-nudged YYYY-MM-DD] [--why]` | Prints the overdue-shop message, or **nothing** when a shop is not due |
| `confirm-card ["<reply>"]` | Records that the ₪700 card was loaded this month |

`nudge` is already wired and runs hourly from familyos. Printing nothing
is the normal case; a caller that treats empty output as an error will
send noise.

`confirm-card` takes the household's **raw reply** and decides what it
means — pass the text through rather than interpreting it. "לא הטענתי
עדיין" contains "הטענתי"; treating it as a yes would silence the
reminder for a month the card was never loaded. Verified: `"כן טענתי"`
records, `"לא עוד לא"` records nothing, no argument records.

Confirming here silences the question everywhere, including at this
bot's own cart hand-off — one record, not two agreeing behaviours.

---

## The one command that is different

    add-to-cart <text> [--qty N]

This reaches the **real store carts right now**, so unlike everything
above it needs the store sessions and the Israeli exit. It fills every
enabled chain (Shufersal and Tiv Taam today) and names each one that
took the item. If nowhere took it, the request lands on the list rather
than vanishing.

`add-item` puts something on the list for the next cycle; `add-to-cart`
reaches the cart that is open right now. "תוסיף חלב" is the first;
"תוסיף חלב לעגלה" is the second.

**It never checks out.** No adapter in this project has a checkout
method and a test fails the build if one appears.

---

## How the bot itself routes free text, for reference

In its own Telegram chat this project classifies every message into one
of twelve intents — `add_item`, `remove_item`, `price_query`, `deals`,
`show_list`, `recipe`, `meal_plan`, `start_order`, `add_to_cart`,
`report_waste`, `smalltalk`, `unclear`. Miri does not have to reuse any
of this; the CLI is the contract. But the mapping is the obvious one, and
`scripts/route_check.py` here holds 33 real phrasings with their expected
intents (33/33 stable as of 2026-09-02), which is a ready-made test set
if it is useful.

One finding from building it, worth passing on: **an indirect phrasing is
where routing breaks.** "מה נאכל השבוע?" flipped between `meal_plan` and
`unclear` across identical runs, because the prompt covered only
imperative forms. It passed on the first test and failed on the second.
Any routing added on the Miri side should be checked more than once per
phrasing.
