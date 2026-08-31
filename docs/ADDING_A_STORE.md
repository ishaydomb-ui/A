# Adding a second chain — what Shufersal already taught us

Written 2026-08-31, before starting Tiv Taam, so the same days are not
spent twice. Every item below is a bug that actually happened here, not
a precaution someone imagined. Read it before writing an adapter.

The economics are worth stating up front: almost none of the value in
this project came from the cart automation. It came from price
analysis, purchase history and knowing the household. The adapter is
plumbing — build it thin, reuse what exists, and spend the time on the
comparison engine instead.

---

## 1. Reuse rather than re-implement

Already store-agnostic. Do not fork these:

| Component | Why it already works for a second chain |
|---|---|
| `storage.py` | Every table carries a `store` column |
| `stock.py` | Tiers/departments computed from any order history |
| `disambiguate.py` | Product-choice logic is not chain-specific |
| `unitprice.py` | Normalises ₪/kg from any feed |
| `radar.py`, `catalog.py` | Query the local catalog, not a website |
| `pantry.py`, `nlu.py`, `digest.py` | No store knowledge at all |
| `cartview.py`, `checklist.py` | Render results, whatever produced them |
| `exitnode.py`, `connectivity.py` | The Israeli exit is shared |

What genuinely needs writing: an adapter implementing `StoreAdapter`,
a price-feed reader if the URL shape differs, and login.

## 2. Start with the public price feed, not the account

Israeli chains publish prices and promotions by law. That half needs no
credentials, no login, and no proxy — Shufersal's feed works fine from
France while the *store* is geo-blocked. Build and validate the
comparison on public data first; the account only matters for filling a
cart.

## 3. The traps, in the order they bit

**Geo-blocking returns HTTP 200.** Both chains serve a block page with a
success status. A missing or non-Israeli exit therefore looks exactly
like broken selectors. `ShufersalAdapter` refuses to start without a
proxy for this reason; do the same, and verify the exit reports country
`IL` rather than merely being reachable.

**A "success" that is really the homepage.** The bulk-match endpoint
answers `200` with the homepage when the CSRF header is missing. Check
the *content type and shape*, never just the status code.

**Endpoints recommend, they do not match.** Asked for a nonsense
product, Shufersal's matcher confidently returned milk. Validate that a
returned name actually resembles what was asked before trusting it.

**Attributes lie.** `data-product-purchasable` reads `false` even for a
logged-in session — it is not an availability signal. Trust
`stockLevelStatus`; in the saved-list JSON, 101 of 357 items were
`outOfStock` while all 357 claimed `purchasable: true`.

**A near-miss URL soft-404s.** `/online/he/myaccount` (no hyphen)
returned a 404 page that still lacked the word "login", so the session
check passed against it. Validate a session on *content*, not on a URL
not containing something.

**Quantity units are per selling method.** `BY_UNIT` is a count,
`BY_WEIGHT` is grams, `BY_PACKAGE` is grams that must be divided by
package weight. Reading a 1300g bag of carrots as a count orders 1300
bags. The discriminator is `sellingMethod`, never the unit string.

**An item already in the cart hides its add button.** Playwright then
waits out the full timeout on an element that will never be visible and
reports an error for something sitting in the cart. Detect the in-cart
state and set quantity instead.

**Clicks fail for reasons unrelated to the click.** Carousels
(`slick-track`) intercept pointer events; the DOM re-renders mid-click.
Locate by product code rather than position, and verify the *outcome*
rather than trusting the click — removal reported failure ten times
while succeeding ten times.

**Asterisks in product names break Telegram.** Israeli multipacks are
written `6*330 מ"ל`; 349 of 5,807 products contain one, and one is
enough for Telegram to reject an entire message. Escape store text
before it enters Markdown (`mdtext.escape`).


**"Forbidden" can mean "you forgot a parameter".** Self-Point's products
endpoint refuses every plain query with `{"error":"Forbidden"}`, which
reads like a permissions wall and sent us looking for a login that was
never needed. It wants an Elasticsearch-shaped `filters` argument and
rejects anything else. Given one it takes a whole *list* of barcodes and
answers in a single unauthenticated call. Before concluding an endpoint
is gated, get its real parameters from the site's own traffic.

**The order summary omits the numbers the order detail has.** Tiv Taam's
order list carries payment method ids with no amounts, so totalling
benefits from it reports zero for every scheme and reads convincingly as
"the card was never used". The amounts exist only in the order detail,
under `secondaryPayments`.

**A second chain may be the same platform as the first.** Tiv Taam and
Victory both run on Self-Point; recognising that turned a second adapter
into a row in a registry. Cloudflare's own block page named the origin
(`self-point.com`) when the site itself would not load — worth reading
the error page rather than only its status code.

## 4. Behaviour to copy, not just code

- **Fail closed on access.** An empty allowlist denies everyone.
- **Never trust a click; verify the cart.**
- **Prefer the store's own bulk facilities** — one call resolved 89
  items in 42s versus fifteen minutes of per-item search.
- **Queue, do not fail, when the exit node is down.** It is a TV box
  and a phone; being offline is routine, not exceptional.
- **Report honestly.** Out of stock, not found and error are three
  different things to a person.

## 5. What is genuinely new for a second chain

- Its own discount mechanics. Tiv Taam has a club, a loaded card and
  coins; the terms must be *read from the account and past orders*,
  because published terms and actual charges differ.
- Basket-splitting: which chain for which item, and whether a
  discount cap changes the answer part-way through a basket.
- Two carts open at once — the digest and cart view assume one store
  today.
