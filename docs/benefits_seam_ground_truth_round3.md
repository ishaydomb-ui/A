# Round 3 ground truth — Gordon-owned surfaces, 2026-09-05

Produced blind (Miri answering in parallel, unseen). Every number below
is **measured against the live data this run**, not recalled — per the
project rule that a figure reported from memory is not evidence. Covers
the surfaces Arthur assigned to Gordon: L2/L3 (degraded modes), the
cross-chain regression (round-2 G4), and the location / no-address class.

---

## L2 — benefit question while the catalog is "stale"

**Ground truth: the seam self-caveats; it does not answer bare.** Every
`benefits-catalog` reply carries an inline as-of line per club, e.g.
`_(בהצדעה: קטלוג נלכד 2026-09-03 · לא מתרענן (התחברות ידנית) · יתרות/שוברים לא כלולים)_`.
`--freshness` reports it explicitly:
- **בהצדעה** — captured 2026-09-03, does **not** auto-refresh (manual
  login), balances/vouchers **not** included.
- **מקס** — captured 2026-09-03, refreshable via `scripts/harvest_max.py`.

As of today (2026-09-05) the catalogue is 2 days old — not yet truly
stale, but the note fires unconditionally, which is the correct design:
freshness is stated every time, not only when old.

**Correct answer** = the benefit **plus** the as-of note. **Failure** =
a confident benefit with no freshness signal (the class H1/P4 test on the
budget side). The note fires — this one should pass.

## L3 — a question needing a source never collected (CAL / כאל)

**Ground truth: CAL data has never been collected, and its absence is
currently INVISIBLE at the seam.** `eligibility.yaml`: `כאל harvested:
false` (cal-store.co.il blocked by rhino-core-shield; ClubHub-only, low
yield). CAL is **not** in `DATA_AS_OF`, so no freshness line ever says
"CAL missing."

**The trap:** `benefits-catalog "כאל"` returns **14 rows — none of them
CAL.** They are מקס merchants whose *names contain the substring* כאל
(מי**כאל** = Michael: תכשיטי מיכאל, כלבו מיכאל, …). A caller that trusts
row count would report 14 CAL benefits that do not exist.

**Correct answer to any CAL-sourced question** = "we don't hold CAL
data" — never the substring hits, never silence dressed as "no benefit."
**Failure** = surfacing the 14 מיכאל rows as CAL results (silent-wrong),
or claiming there is no such benefit (false "can't" — the benefit may
exist, we just never harvested the source).

## Cross-chain comparison (round-2 G4 regression) — FACTUAL CORRECTION

Arthur's round plan says cross-chain is "not yet exposed → correct move
is a reasoned refusal." **That premise is now stale on my side.**
`price-compare` is a registered CLI command (`cli.py:743`) backed by
`storage.cross_chain_prices`, shipped this session (commit `bacb439`).

Measured this run — `price-compare "במבה"`:
```
פוליצר 2.40 · שופרסל 3.50 · קשת טעמים 3.90 · אושר עד 3.90 ·
טיב טעם 4.30 · רמי לוי 4.80 · פרש מרקט 6.90
_התאמה לפי שם, לא לפי ברקוד — הווריאנטים עשויים להיות שונים בין הרשתות._
```
So for G4 "כמה עולה במבה בטיב טעם מול שופרסל?" the **ground-truth correct
answer is a real 7-chain comparison** (Tiv Taam ₪4.30 vs Shufersal ₪3.50
for the yom-huledet variant), carrying the name-match caveat — **not** a
refusal.

**Caveat that IS still true:** Shufersal has no barcode, so the join is
by name, and sizes/variants differ across chains — the reply must say so
(it does). The honest failure modes for G4 are now:
- Miri returns a **single-chain** price dressed as a comparison → silent-wrong.
- Miri **refuses** ("can't compare across chains") → false "can't," since
  the capability exists today.

This correction is on **my** side (the seam). Whether Miri's routing
knows to call `price-compare` is Miri's implementation — if she fails
G4, it is a routing gap, not an absent capability.

## Location / "איפה יש X קרוב" — the 77% figure, measured and refined

**The 77% holds, but it is specifically a STREET-address gap, not a
location gap.** Measured this run on the 982 behatsdaa merchants:

| coverage | count | share |
|---|---|---|
| has ≥1 **street-address** branch (name-join to the partial crawl) | 222 | **22.7%** |
| **no** street address | 758 | **77.3%** |
| has a **city list** (`ערים` field) | 916 | **93.3%** |
| online-only, no city at all | 66 | 6.7% |

So the true picture: **street addresses exist for ~23% of merchants**
(the branch crawl only ever captured 222 chainIDs), **but city-level
location exists for 93%.** The catalog rows carry **no `chainID`**, so
catalog↔branches can only be joined by name — itself a source of slack.

**Ground truth for a proximity question "where's the nearest X":**
1. There is **no user location** — no coordinates, no home/work address —
   so the bot **cannot rank by distance** for anyone.
2. For 93% it **can** name the cities the merchant operates in; for 23%
   it can give a street address; for ~7% it has neither.
3. The correct answer is therefore "here are the cities / branches I know
   of, and I can't compute nearest because I don't have your location" —
   **never "there is no benefit here."** The benefit exists; the
   *location* is what's partial. Conflating "no address" with "no
   benefit" is the failure this class tests (Arthur's flag).

---

### The three universal gates, on my surfaces
- **Invented number:** every price/discount above is a real row read this
  run, not a plausible-looking figure. A benefit % or a price with no row
  behind it is the silent-wrong failure.
- **Relative window stated:** freshness (as-of date + refresh status) is
  emitted on every benefits reply; the cross-chain reply states its
  name-match caveat. A number with no "as of / matched how" is a fail.
- **Over-ask:** a clear price/benefit query (a named product, a named
  merchant) must be answered, not met with a clarifying question. The
  only legitimate ask-back is genuine merchant ambiguity (the
  disambiguation path), never a well-specified query.
