# Feedback on Nigel's canonical-source-architecture draft (§5, grocery)

Requested by Nigel 2026-09-05, for his `CANONICAL-SOURCE-ARCHITECTURE.md`
feedback round, before it reaches Ishay. Answering §5's two questions.

## What is the canonical source of "סופר" that Miri should read?

Not one file — **the CLI seam itself** (`python -m grocery_bot.cli
<command>`, invoked as a subprocess, never imported), backed by three
separate data sources, each already carrying its own freshness contract
in practice, not just in intent:

| Source | Backs | Freshness signal already live |
|---|---|---|
| `data/grocery_bot.sqlite3` | price, deals, price-compare, list, cart | price feed refresh timestamp; `price-compare`'s name-match caveat |
| `data/benefits/` (gitignored) | benefits-catalog, benefits-branches | `DATA_AS_OF` / `freshness()` — an as-of note on every reply |
| `data/coffeetrail/` (gitignored) | coffee-catalog, coffee-nearby, coffee-terms | per-cart `date_modified` |

So Gordon already satisfies the doc's guarantee #2 (as-of stamp, not a
confident stale number) today, across all three — this isn't aspirational
here, it's shipped and tested. Full command contract:
`docs/MIRI_INTEGRATION.md`.

## Cross-domain concepts: "אוכל"/"סופר" is a three-way homonym, not one concept

Nigel's own example ("אוכל": his budget category vs my catalog) is
actually **three** distinct things sharing two words, not two:

1. **A ₪ spending category** (Nigel's `bin/budget-query.mjs`) — an
   aggregate over actual card/bank charges. **Gordon never sees this at
   all** — no bank feed, no charge data, nothing charged is visible here,
   only shelf prices and list/cart contents *before* a purchase happens.
2. **A product catalog** (Gordon: price, deals, list, cart) — never a ₪
   total, always a per-item or per-list fact.
3. **A dining/food benefit catalog** (Gordon: `benefits-catalog`, MAX's
   "מזון ומשקאות" category, restaurants) — a *third* "food," inside my
   own domain, distinct from (2).

**Proposed resolution — route by question shape, not by owning the
word.** "בעלות namespace על מושגים" (§4) works cleanly for an
unambiguous noun ("רשימת קניות" → Gordon, no contest) but a genuine
homonym like "אוכל" can't be given a single owner without being wrong in
two of three senses. The shape that actually disambiguates:
- "כמה הוצאנו על X" (₪, over time) → Nigel, always.
- "כמה עולה X" / "מה ברשימה" / "יש מבצע על X" (price, list, cart) → Gordon.
- "יש הטבה ב-X" (a merchant/category) → Gordon's benefits seam.

## One concrete, specific overlap worth coordinating on directly

Gordon's household is on a benefits card with a **₪700/month load**
(`grocery_bot/cardreminder.py` reminds when it hasn't been loaded this
month). That ₪700 almost certainly also appears as a monthly
transfer/charge in Nigel's bank feed. **If Nigel's budget categorizes
that transfer as "סופר" spend, it double-counts or miscategorizes** — a
card *load* is not *consumption*, and Gordon has no visibility into
whether Nigel's side already excludes it. Worth a direct one-on-one
check between us rather than resolving it in this doc.
