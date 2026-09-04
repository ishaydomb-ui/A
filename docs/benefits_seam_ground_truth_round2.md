# Round 2 §G ground truth — search escaping & matching, 2026-09-04

Produced blind (Miri answering in parallel, unseen). Real CLI output.
Scored against my pre-run blind prediction — two confirmed, one refuted,
one new bug found.

**G1 — "מה המחיר של קוטג' 5%?"** *(`%` on a different product)*
`price "קוטג' 5%"` → **`קוטג' 5% 250 גרם בעלז — 6.40₪` and no chocolate**.
The `%` escape holds. **New finding beyond the probe:** the result count
swings on the apostrophe form, because product names are inconsistent
about it — `קוטג' 5%` (ASCII `'` U+0027) → 1 match, `קוטג 5%` (no
apostrophe) → 3, `קוטג׳ 5%` (Hebrew geresh U+05F3) → 0. So the `%` bug is
fixed but an **apostrophe/geresh normalization gap** is exposed. Pass =
real milk-cheese, no noise; a strong answer would also be apostrophe-
insensitive.

**G2 — "יש הטבה ב-H&O?"** *(English name + `&`)*
`benefits-catalog "H&O"` → **2 rows: `H&O`, `H&O און ליין`.** Works.
`&` is not a LIKE metachar, benefits search is Python `in`, and the store
is stored in English, so the English query matches. **My blind prediction
that English queries miss was too broad — refuted here.** The residual
risk is case, not English per se (see G3).

**G3 — "יש הטבה בterminal x?"** *(case-sensitivity — my prediction)*
**Confirmed.** `benefits-catalog "terminal x"` (lowercase, as asked) →
matches. `benefits-catalog "Terminal X"` (capitalised) → **"לא נמצא".**
Python `in` is case-sensitive and the row is stored lowercase, so the
verbatim question passes but any capitalisation breaks it. Pass = answers
the lowercase form; the fragility is real and is the predicted one.

**G4 — "כמה עולה במבה בטיב טעם מול שופרסל?"** *(cross-chain — my prediction)*
**Confirmed, and it's the core-value gap.** `price במבה` returns the
**Shufersal feed only** (12 products, with a promo). **No cross-chain verb
exists in the seam at all** (`chaindeals`/`compare`/`whereto` are not CLI
commands — grep returns nothing; `/chaindeals` is a Telegram-bot command,
not part of the seam Miri calls). The *data* exists — במבה is in
`store_prices` for 6 chains incl. tivtaam (6 rows) — so the comparison is
buildable (`compare.py`) but **not exposed**. Pass = a truthful "I can't
compare chains through this seam," or Shufersal-only with that caveat.
Fail = a confident cross-chain answer, or claiming Tiv Taam has no data.

**G5 — "יש הנחה באמריקן?"** *(shared-prefix ranking)*
`benefits-catalog אמריקן` → `אמריקן איגל` (fashion) first, then
`אמריקן פיצה - …`, `אמריקן בורגר`. All start with אמריקן (rank 1), ties by
length → the shortest, `אמריקן איגל`, leads. **The ranking returns all
candidates but cannot disambiguate intent** (fashion vs pizza vs burger);
if the user meant pizza, it isn't first. Pass = presents the candidates;
there is no single correct row to expect.

## Prediction scorecard (mine, before seeing these)
- **G3 case-sensitivity — HIT.** Predicted, confirmed exactly.
- **G4 cross-chain absent — HIT.** Predicted as the untested core value;
  confirmed no seam verb.
- **G2 "English misses" — MISS.** Too broad; English works when the name
  is stored in English. Corrected: the real axis is case, not language.
- **G1 apostrophe/geresh — new.** Not in my prediction; a real
  normalization gap the `%` probe happened to surface.

## Fixes deferred to after the full run (per the rule)
1. **Case-insensitive matching** (G3) — `.lower()` both sides in benefits
   search; cheap, high value.
2. **Apostrophe/geresh + `%`-in-name normalization** (G1) — fold U+0027 /
   U+05F3 and stray punctuation before matching.
3. **Cross-chain price** (G4) — the big one: expose `compare.py` through
   the seam so "X at Tiv Taam vs Shufersal" is answerable. This is a
   feature, not a fix — flag for Ishay, not a mid-cycle patch.
