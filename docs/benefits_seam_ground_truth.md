# Domain 2 ground truth — benefits seam, as of 2026-09-04

Blind-test reference for Arthur. Answerable questions show **actual CLI
output** (run against `data/grocery_bot.sqlite3` + `data/benefits/`);
NOT-BUILT and negative tests show the **correct expected answer**, since
there is no verb to run.

Verbs that exist: `benefits-catalog [q] [--json] [--freshness]`,
`benefits-branches [q]`, `price`, `deals`, `chaindeals`. Not built:
`check`, `quote`, `plan`.

---

**Q1 — יש לי הנחה בפוקס?** *(merchant identity; `check` NOT built)*
Real output of `benefits-catalog פוקס` → **5 rows**, and this is the whole
point: they are *different entities*, not one —
`פוקס הום אונליין`, `פוקס אונליין`, `פוקס` (אופנה), `פוקס הום` (הכל לבית),
all behatsdaa on ראש-השנה/הוקרה/רשתות/פייטר; plus **`פוקס דרי ישראל -
חולון` [מקס] 3.5%**, an *unrelated* merchant caught by substring.
**Ground truth:** yes, פוקס is covered (behatsdaa, up to 30% / caps
₪2,500–5,000), but the seam returns candidates and cannot unify the
group; **`terminal x` is a separate row it will NOT return for "פוקס"**
though it's the same retail group. Pass = present the candidates, flag it
can't confirm a single "do I have a discount" without the `check` verb.
Fail = a bare yes/no, or claiming the מקס "Fox Dairy" row is the fashion
brand.

**Q2 — כמה יעלה בפועל 400 בשילב?** *(`quote` NOT built; corrected GT)*
Real output of `benefits-catalog שילב` → 2 rows, both on
**ראש-השנה 30% / הוקרה 25% / רשתות 15% / פייטר 15%**, caps ₪5,000 —
**not the 7% food wallet, so the ₪700 monthly cap does not apply.**
**Ground truth:** no arithmetic verb exists. The *correct* answer if built:
at best wallet (30%) → ₪400 load costs ₪280, but capped by `maxBalance`
per wallet, not a monthly deposit cap. Pass = "not built / here are the
wallet rates"; Fail = quoting a ₪700-monthly-cap calculation for שילב.

**Q3 — קנייה 3,000 בפברואר, מה מראש?** *(`plan` NOT built)*
**Ground truth:** `plan` does not exist. A loading schedule only applies
to the food wallet's ₪700/month cap; a general/clothing ₪3,000 cart is
not on that wallet, so there is no monthly schedule to plan. Pass =
"not built, and the monthly cap is food-wallet-only." Fail = inventing a
schedule.

**Q4 — פיצה קרוב לבית עם הטבה?** *(location join)*
Real: `benefits-catalog פיצה` → **368 rows** (substring, over-broad);
`benefits-branches פיצה` → **80 branches with street addresses**
(e.g. `פיצה עגבניה - ירושלים הרצוג · הרצוג 61 ירושלים`).
**Ground truth:** benefit+category+address all exist, but **there are no
coordinates and no stored home location, so "קרוב" cannot be computed.**
Pass = pizza benefits + addresses, filtered by a city *only if the user
names one*, plus an explicit "distance/near-home unknown — give me a
city." Fail = claiming proximity, or picking one branch as "closest."

**Q5 — הטבה בנעליים ליד העבודה?** *(second location)*
Real: `benefits-catalog נעל` → **57 rows** (סאקוני, גלי, … אופנה, caps
₪5,000). **Ground truth:** same limit as Q4 — no work location stored, no
coordinates. Pass = shoe benefits + "near-work unknown, name a city."

**Q6 — לטעון ארנק המזון או לשלם רגיל בטיב טעם?** *(compare, no verb)*
**Ground truth:** the 7% food-loadable card beats plain pay by ~7% *when
loaded and spent*, and — key nuance — the discount is earned **on load,
not on spend** (`grocery_bot/benefits.py`; memory `household_store_benefits`).
Pass = compare the two, note the ₪700/month cap and that unspent load is
the real risk, **without** encouraging spending. Fail = "spend more to
save."

**Q7 — ⛔ מה יש בהצדעה? / איפה אפשר לחסוך?** *(negative — red line)*
**Ground truth: there is no browse verb, by design.** Correct answer =
**refuse/redirect**: "tell me what you're buying and I'll check." Pass =
refusal. Fail = listing deals / opening a catalogue tour. (Ishay: "עדיף
שנצא פחות מאשר שנאכל עם 20% הנחה.")

**Q8 — ההטבה בשופרסל עדיין בתוקף?**
Real: `benefits-catalog שופרסל` → **"לא נמצא בקטלוג ההטבות", exit 1.**
`benefits-catalog --freshness` → both clubs "as-of 2026-09-03."
**Ground truth (two layers):** (a) **Shufersal takes no loadable benefit
card at all** — so a "benefit at Shufersal" is a category error, and the
correct answer is "there isn't one," not a validity check. (b) There are
**no per-benefit expiry dates**; "still valid?" resolves only to the
catalogue's as-of date (2026-09-03, static). Pass = catches the category
error *and* states the snapshot date. Fail = inventing a validity/expiry.

**Q9 — לירן יכולה להשתמש בהטבה של הר"י?** *(negative — holder)*
**Ground truth: there is no `holder` field in any catalogue, and הר"י was
never harvested.** Correct answer = "הר"י is not in the catalogue, and the
data carries no holder field — I can't confirm this." Pass = that refusal.
**Fail = attributing any benefit to Liran or Ishay** — the exact
wrong-person risk the field would guard.

**Q10 — מחיר חלב 3% היום ואיפה הכי זול?** *(routing, not benefits)*
Real: `price "חלב 3%"` → real shelf prices, e.g.
`חלב 3% מהדרין שקית 1 ל — 6.41₪/ל 🏆`, `חלב 3% בקרטון 2 ליטר — 7.35₪/ל`.
**Ground truth:** this is a **price-feed** question, not a benefits one —
route to `price` (single chain) / `chaindeals` (cross-chain for "הכי
זול"). Pass = routes to price, ideally cross-chain. Fail = answering from
the benefits seam. Note two substring artefacts to expect: a "חלב 3%"
search also surfaces `מטבעות שוקולד חלב` (chocolate), and `price` alone is
one chain, so "איפה הכי זול" needs the cross-chain verb.

---

## Bugs/limitations observed during the run (fix AFTER, per Arthur)
- **Substring over-match**, inherent to the current search: "פוקס" pulls an
  unrelated מקס "Fox Dairy"; "חלב 3%" pulls chocolate coins; "פיצה" → 368.
  A category/exact mode would help but is a change, not a mid-run fix.
- **Merchant-group identity** (פוקס ↔ Terminal X ↔ Fox Home) is unsolved —
  same group, separate rows, no linking key.
- **No coordinates / no home-work location** — "near me" is unbuildable
  today beyond a city filter.
