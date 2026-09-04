# Cross-path consistency in the grocery seam — mapping, 2026-09-04

Ishay's rule: two paths must not give different answers to one question,
or it casts doubt on the whole model. Where that can happen here, measured
— and a canonical-path proposal. **Mapping only; nothing changed.**

## Where two paths CANNOT diverge (checked, clean)

- **Shufersal price has one source.** `price` reads `catalog_products`
  (the Shufersal transparency feed); `store_prices` holds **no Shufersal
  rows** (freshmarket/keshet/osherad/politzer/ramilevy/tivtaam only). So
  there is no second path that returns a different Shufersal price.
- **One verb per question class.** The seam exposes exactly one command
  each for benefit (`benefits-catalog`), location (`benefits-branches`),
  price (`price`), promotions (`deals`). Miri is not choosing between two
  equivalent-looking verbs for the same question.

## Where the paths DO diverge (the real gaps)

1. **Benefit vs location coverage — 77%.** 982 catalogue merchants, but
   only **222** appear in the branch-address crawl: **760 (77%) have a
   benefit but no address.** So `benefits-catalog X` → "yes, 15% benefit"
   while `benefits-branches X` → nothing, for most merchants. Two paths,
   two pictures of the same merchant. **The absence of an address is not
   the absence of a benefit — and nothing currently says so.**

2. **Price is single-chain while the data is six-chain.** `price` answers
   Shufersal only, but `store_prices` holds prices for **six other chains
   incl. Tiv Taam**. "How much is במבה" therefore has two truths —
   Shufersal shelf price vs cheapest-across-chains — and today only the
   first is reachable. This is not a live divergence (the cross-chain path
   isn't exposed), but it **becomes one the moment a cross-chain verb is
   added**, if both then answer "price."

3. **`store_prices` is a history, not a snapshot.** One (store,barcode)
   has up to 29 rows (price observed over time). `latest_store_prices`
   picks newest — but any cross-chain feature MUST use it; reading the
   raw table would return stale prices and disagree with itself.

## Proposed architecture: one canonical path per question class

Declared in `MIRI_INTEGRATION.md` so Miri routes deterministically, not by
picking whichever verb looks equivalent:

| Question class | Canonical path | Rule that prevents divergence |
|---|---|---|
| Does a benefit exist / what rate | `benefits-catalog` | **The authority on existence.** A `benefits-branches` miss never overrides it |
| Where is the merchant | `benefits-branches` | **Address enrichment only.** A miss = "address unknown," never "no benefit / no store" (covers the 77%) |
| Current price of a product | `price` | Declared as **Shufersal shelf price**, single-chain, explicitly |
| Cheapest across chains | *(not exposed)* | If built, it becomes THE price answer, and `price` is re-scoped to "Shufersal shelf price" so the two never both answer "how much" |

## Cross-chain exposure — my assessment (Ishay's decision)

**Worth exposing — but only as the canonical price path, not a second
one.** The data is already there for six chains, `compare.py` already does
the EAN matching, and cross-chain price is the project's actual core value
(a phone chat is a poor table, but "X is ₪2 cheaper at Tiv Taam" is the
whole point). The risk is precisely Ishay's: if `price` (Shufersal) and a
new `compare` both answer "how much is X," they will disagree by design.
So the sound move is to make cross-chain **the** price answer and demote
`price` to an explicit "Shufersal shelf price" sub-answer — one canonical
path, re-scoped, not two equivalent ones. That re-scoping is the product
decision; the plumbing is small.
