# Coffee cart directory — coffeetrail.co.il

**Owner:** this project (Gordon). Requested by Ishay 2026-09-05, same
seam model as the benefits catalog (`docs/BENEFITS.md`): Miri reads the
output read-only through the existing CLI seam; this is not a new bot,
and there is no login or account layer involved at all — the source is
a public business directory.

## What this is

A household reference of Israeli coffee carts along hiking/driving
routes, harvested from coffeetrail.co.il (a WordPress site built on the
"MyListing" theme, custom-branded "Coffee Trail"). No credentials, no
OTP, nothing behind a login — everything harvested here is public
marketing content aimed at visitors, the same posture as the MAX benefit
catalog.

## Access facts (verified, not assumed)

Rob (portfolio-strategy) did the initial discovery 2026-09-05; re-checked
directly from this VPS rather than taken on trust, per this project's own
evidence standard:

- **No geo-block.** Plain `curl` from this box returns HTTP 200 —
  unlike Shufersal/Tiv Taam, no Israeli exit node is needed here.
- **No bot wall.** Cloudflare + LiteSpeed cache only; not
  Incapsula/Akamai/a WAF that challenges non-browser clients.
- **`robots.txt` permits the harvest.** It disallows `wp-admin`,
  `wc-logs`, `woocommerce_uploads`, `?add-to-cart=` and search-result
  pages. `/coffeecart/` and the taxonomy archive pages are unrestricted.
- **Enumeration is exact, not crawled.** `job_listing-sitemap.xml` lists
  every cart directly: **405** `<loc>` entries under `/coffeecart/`
  (plus the `/listings/` index page itself, skipped). No blind link-
  following needed.

## Per-cart data: JSON-LD, not HTML parsing

Every cart page carries a `<script type="application/ld+json">` block
with `"@type": "LocalBusiness"` — that node is the entire per-cart
extraction, no DOM parsing. (The page also carries Yoast's own sitewide
`@graph` block — `WebPage`/`BreadcrumbList`/`WebSite`/`Organization` —
whose `sameAs`/`logo` describe the **site itself**, not the cart; don't
confuse the two when reading raw JSON-LD by hand.)

**Fields actually populated — measured on the full harvest (405/405
carts), not the small sample that first suggested otherwise.** An
initial 4-cart sample found `openingHours`/`contactPoint.telephone`/
`sameAs`/`logo`/`photo` all empty, which would have been a wrong claim
to ship: the real, full-catalog numbers are

| Field | Populated |
|---|---|
| `address_text` / `lat`,`lng` | 396/405 (97.8%) |
| `opening_hours` | 257/405 (63.5%) |
| `phone` | 263/405 (64.9%) |
| `same_as` | 260/405 (64.2%) |
| `logo` | 262/405 (64.7%) |
| `photos` | 272/405 (67.2%) |

So roughly two-thirds of carts *do* carry hours/phone/socials/photos —
the small initial sample happened to land on the sparser third. This is
exactly the lesson the project's own evidence rule exists for: "checked
on 4 of 405" is not "checked," and the fix here was catching it before
writing it into a household-facing doc, not after.

**A real bug this surfaced, worth remembering for the next harvester
touching third-party JSON-LD:** one cart's `logo` field was a **list**,
not the plain string every other sample had — schema.org actually
permits `logo` as a string, an `ImageObject`, or an array of either, and
this site uses more than one shape across its own listings. This crashed
a live, 15-minutes-in harvest run at record ~30 of 405 with an
`AttributeError`. Fixed two ways, not one: `_as_text`/`_as_url`/
`_as_url_list` in `scripts/harvest_coffeetrail.py` coerce every optional
field regardless of shape, **and** per-cart parsing is now wrapped so
one malformed record logs and gets skipped (retried on the next run)
rather than killing the other 400+ already-fetched, still-polite
requests. Trusting a schema's stated shape without a fallback is the
same class of mistake as trusting a peer's claim without checking it —
verify, even when the source is a spec, not a person.

## Taxonomies — best-effort membership, not exhaustive

Ishay named five taxonomies for filtering, each with its own sitemap of
term-archive URLs: **region (43) · road (25) · foodtype (8) · type (4) ·
diners (3)**. Confirmed present and matching those counts.

**No clean join exists between a cart and its terms — checked three
ways before settling on the one that partially works:**
1. **No REST API.** `wp-json/wp/v2/job_listing` returns `rest_no_route`
   (this CPT isn't registered for the REST API — WP Job Manager/
   MyListing's own choice, not a permissions issue).
2. **No per-cart-page marker.** Every single cart page renders the site's
   full sitewide region mega-menu (~30 region links) — that is
   Elementor navigation, not "this cart's region." There is no distinct
   class or wrapper separating an assigned term from the menu.
3. **Term archive pages list carts, but paginate via AJAX beyond the
   first server-rendered batch.** The Jerusalem region archive's own
   copy says "35 listings"; only 29 static `/coffeecart/` links are in
   that page's raw HTML. The rest load through a custom plugin
   (`Coffeetrail-Extended-Map`, overriding the MyListing theme's map
   AJAX) this harvester does not drive — reverse-engineering that
   endpoint (a private per-request "fingerprint" cache keyed to filter
   state) was judged disproportionate effort for a filtering nicety, not
   the core ask.

**Confirmed at full-catalog scale, not just the one Jerusalem sample:**
several unrelated large terms — `type/coffee-cart`, `type/stationary` and
all three `diners/*` capacity brackets — each cap at **exactly 149**
carts, independently. That is not a coincidence of real membership; it's
strong evidence of a shared server-side batch size for the initial
render, the same ceiling the Jerusalem sample hit. **Rule of thumb:** a
term landing at some number well under ~149 (e.g. `road/40` at 41,
`road/4` at 66) is probably its true, complete count; a term landing at
or near 149 is almost certainly truncated. `region` covers 390 of 405
carts across its 43 terms combined — good overall reach — but any single
popular region can still be undercounted the same way.

So `terms.json`'s per-term `carts` lists are **a floor, not a claim of
completeness** — every `coffee-by-term` result says so explicitly
("רשימה חלקית"). If exact region/road membership becomes load-bearing
later (not just "some carts in this area" but "all of them"), the
`Coffeetrail-Extended-Map` AJAX endpoint is the next thing to reverse-
engineer, or ask the site owner directly for a data export — cheaper
than guessing at their JS.

## Freshness and change detection

`dateModified` isn't on the LocalBusiness node itself, but the same
page's Yoast graph carries a `WebPage.dateModified` — confirmed
identical to the sitemap's own `<lastmod>` on every row checked, so
either is a valid change signal. The harvester prefers the page's own
value and falls back to the sitemap's. **This is the resumability key**:
a re-run compares `date_modified` before re-fetching, so an unchanged
cart costs nothing on the next monthly run.

`coffee-catalog` has no `--freshness` flag of its own yet (unlike
`benefits-catalog`); `coffeetrail_catalog.freshness()` returns
`{carts, oldest_change, newest_change}` for a caller that needs to state
an as-of — wire a CLI flag for it if Miri needs one directly rather than
via `--json` on `coffee-catalog`.

## Politeness (Ishay's own terms, honored exactly)

One request every 1.5s (±0.5s jitter), strictly sequential — this is a
small business's site, not an API meant for bulk consumption. A full
harvest (405 carts + 5 taxonomy sitemaps + 83 term archives ≈ 490
requests) takes roughly 12–14 minutes. Monthly refresh is the
expectation; this project holds no continuous-polling schedule for it
the way it does for Shufersal's price feed.

## Commands (see `grocery_bot/coffeetrail_catalog.py`, wired in `cli.py`)

| Command | Notes |
|---|---|
| `coffee-catalog [query] [--json]` | Substring search on name/description/address |
| `coffee-nearby <lat> <lng> [--radius KM] [--open-now] [--json]` | Haversine-sorted, nearest first. Default radius 15km; `--radius 0` = no limit |
| `coffee-terms [taxonomy] [--json]` | Lists taxonomy slugs, or one taxonomy's terms with counts |
| `coffee-by-term <taxonomy> <slug> [--json]` | Carts under one term (best-effort — see above) |

**Why lat/lng and opening_hours are kept structured, not display text**
(Ishay's own framing, and the actual point of this harvest over just
reading the site): a free-text address can only ever be *shown*; a real
number can be *computed over*. That is what makes "עגלת קפה קרובה"
(`coffee-nearby`) and "מה פתוח עכשיו" (`open_now()`, exposed via
`--open-now`) answerable at all, versus a description a human has to
read themselves.

**`open_now()` returns `None`, not `False`, when hours are unknown or
unparseable** — the same "absence ≠ negative answer" principle as the
benefits seam's L3 fix (`docs/benefits_seam_ground_truth_round3.md`):
conflating "we don't have hours for this cart" with "it's closed" is a
silent-wrong, not a small imprecision. With `opening_hours` populated on
63.5% of carts (see above), roughly a third of `--open-now` filtering
will legitimately answer "don't know" rather than yes/no — that is the
honest answer given the data, not a bug.

## What this is not

No login, no personal data, no purchase/review history, no live
availability (a cart that closed down remains in the directory until the
site itself removes it — the harvest reflects **coffeetrail.co.il's own
data**, not ground truth about which carts still operate). No exhaustive
taxonomy membership (see above). No continuous refresh — re-run
`scripts/harvest_coffeetrail.py` monthly, or after being told the
directory changed.

## Status

**First full harvest completed 2026-09-05: 405/405 carts, all 5
taxonomies (region 43 · road 25 · foodtype 8 · type 4 · diners 3 —
matching Ishay's counts exactly).** One cart and one region archive
timed out on the first pass; both were picked up cleanly by a second,
resumable run costing only those two requests, not a re-harvest.
`date_modified` ranges from 2025-02-04 to 2026-09-05 — the newest value
(`asherke`) matches the sitemap file's own top-level `<lastmod>` exactly,
which suggests the site's sitemap regenerates that stamp on some entries
whenever *anything* in the sitemap changes, not only on that specific
cart's own edit. Treat a `date_modified` this close to a harvest run's
own date with mild suspicion; most rows carry an earlier, presumably
genuine per-cart edit date.

This section will drift the moment the next monthly refresh runs — for
a live count, `coffee-catalog --json | python3 -c "import json,sys;
print(len(json.load(sys.stdin)))"`, or `coffee-terms` for taxonomy
coverage. Everything else in this file (access facts, field-population
percentages, the taxonomy-truncation finding) is a durable finding about
*how the site behaves*, not a point-in-time snapshot, and should still
hold on the next refresh unless the site itself changes.
