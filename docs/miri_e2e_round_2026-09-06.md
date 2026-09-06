# Independent measurement — Miri E2E round, 2026-09-06

Ishay (relayed by Arthur, verbatim, 06.09.2026): "הרץ בדיקות באגים
ויכולות על כל השאלות הבאות." Measured independently from Miri's own
run, per Ishay's own rule (05.09): the gap between two measurements is
the detector, not a single "looks reasonable" judgment. Every answer
below is the real CLI output, run just now, against production data
unless stated otherwise.

## Direct questions (§ numbered as given)

**1. "יש הנחה בשילב?"**
`benefits-catalog "שילב"` → **yes**, 2 rows (שילב, שילב אונליין):
ראש השנה 30%/₪500, מבצע הוקרה 25%/₪500, רשתות בהצדעה 15%/₪1500, פייטר
15%/₪2500, תקרה ₪5000. **Source:** `data/benefits/lab_rescue/catalog_tagged.csv`
(behatsdaa). **Freshness:** stated inline on every reply — "קטלוג נלכד
2026-09-03 · לא מתרענן (התחברות ידנית) · יתרות/שוברים לא כלולים."

**2. "כמה עולה קוטג'"**
`price` tested across **4 spellings**: `קוטג'` (ASCII '), `קוטג` (none),
`קוטג׳` (Hebrew geresh U+05F3), and `קוטג' 5%`. **All four return
identical results for the bare query** (8 rows, ₪3.30–₪7.10, cheapest-
per-kg flagged 🏆) — the apostrophe/geresh normalization fix (2026-09-04)
and the `%` LIKE-escape fix both hold. `קוטג' 5%` correctly narrows to 4
rows (only the 5%-fat ones). **No wording-dependence found here** — this
is the one place a prior finding said it *would* diverge, and it no
longer does. **Source:** Shufersal public price feed, branch 9.
**Freshness:** `catalog_meta()` → published `2026-09-06T03:00:00`,
refreshed `2026-09-06T04:17:57Z` (today, ~4h old at test time).

**3. "יש מבצעים השבוע?" — open question, the browse/discover line**
The only deals verb in the seam Miri can call is `deals` (`cli.py`'s
`_deals`). **Structurally incapable of crossing the line**, not just
policy: it calls `storage.list_active_base_items()` then
`find_deals_for_base_list(storage, items)`, which only searches for
promotions on products *already on the household's own standing list* —
there is no code path from this command to the general catalog. Real
output today: 3 rows (בצל יבש -34%, כרוב לבן -20%, קורנפלקס -21%), header
self-labels "מבצעים על פריטי רשימת הבסיס." **A broader "novel/never-
bought" deals surface does exist** (`hotdeals.find_extended`, behind
Telegram's `/alldeals`/`/chaindeals`) but it is **not part of the CLI
seam Miri calls at all** — confirmed by grep, only `"deals": _deals` is
registered. So from Miri's side, this question can only ever be answered
against the household's own list, by construction — never a general
"here's what's on sale" push. **Source:** `store_prices`/promotions
tables, filtered by `base_list`. **Freshness:** same price-feed stamp as
Q2.

**4. "הטענתי את הכרטיס"**
`confirm-card "הטענתי את הכרטיס"` → `recorded: card loaded for 2026-09`.
Verified `cardreminder.confirmed()` → `True`,
`cardreminder.decide()` → `should_ask=False`. Also adversarially checked
negation handling (not asked, but worth recording): `"לא הטענתי"` →
`False`, `"עדיין לא"` → `False`, `"כן הטענתי"` → `True` — correctly
distinguishes "not" from the affirmative substring it contains.
**Source:** `benefit_confirmations` table, `kind='benefit_card_loaded'`.

**⚠️ Methodology note, disclosed rather than hidden:** this command
*writes*, and I ran it against the real production DB before thinking
that through — it set a **real, false** "card loaded" record for
2026-09 that would have suppressed a genuine reminder about a ₪700
benefit that was not actually confirmed. Caught it, verified the
`confirmed_at` timestamp matched my test exactly (not a pre-existing
row), and deleted it — `cardreminder.decide()` now correctly shows
`should_ask=True` again. Lesson applied for the rest of this round: every
state-mutating command below ran against an isolated temp DB, not
production; every read-only one ran against production directly.

## Via Miri (indirect checks)

**"תוסיפי חלב לרשימה" + "מה יש ברשימת הקניות"** — run together on a temp
DB to avoid touching the real list: `add-item "חלב"` → `added: חלב`;
`list-items` → `חלב  🙋 unknown` (no `--by` given, so attribution
defaults to unknown — correct, not a bug). Both work as documented.

**"תכיני לזניה"** — run as `recipe "לזניה" --preview` **against
production**, read-only by construction (`--preview` returns before any
write — verified in `cli.py`, line 326). Output: 8 ingredients to add
(דפי לזניה, בשר טחון, רסק עגבניות, עגבניות מרוסקות, שום, חמאה, גבינה
צהובה, פרמזן), 4 flagged "כנראה יש" (בצל, קמח לבן, חלב, שמן זית) from
existing stock/purchase history. **Interesting real-data cross-check:**
the production list (checked separately, read-only) already carries
these exact lasagna ingredients attributed to `ישי (מתכון: לזניה)` —
i.e. Ishay genuinely ran this for real at some point before today; my
`--preview` run added nothing new (confirmed by the "nothing added"
line), so this is pre-existing state, not something I created.
**Source:** `nlu.expand_recipe` (static recipe data) +
`pantry.split_ingredients` against `storage`'s stock/purchase-history
table for the have/missing split.

## Summary

No wording-dependent finding this round — the one place history
predicted one (apostrophe/geresh on קוטג') is now confirmed fixed across
4 forms. One real, self-caught methodology mistake (a write-command test
against production) is disclosed above and reverted. Every "no" or
"can't" above is a structural fact (verified in code), not an inference
from output alone.
