# Benefits harvesting — behatsdaa + other clubs

**Owner:** this project (Gordon). Decided by Ishay 2026-09-02 (verbatim
quote and date recorded in `GOALS.md` under החלטות שהתקבלו). **Build
authorised directly by Ishay 2026-09-03.** Miri only *reads* the output,
through the existing CLI seam — this is not a new bot.

Full handover from the Strategist session: `~/portfolio-strategy/BENEFITS-HANDOFF.md`.

## Data freshness — read this before trusting any behatsdaa figure

**All behatsdaa data is a one-time snapshot. It is NOT live and is not
refreshed.** Anyone — Miri, Nigel, any bot — consuming numbers that
originate here must treat them as of these dates and no newer:

| Dataset | Source | As-of / captured | Refreshes? |
|---|---|---|---|
| behatsdaa store catalog (982) | `catalog_tagged.csv` | harvested **2026-09-03 06:05** | **No** — static snapshot |
| behatsdaa full catalog (2903) | `catalog_full.csv` | harvested **2026-09-02 22:02** | No |
| behatsdaa branch addresses | `branches*.csv` | **2026-09-02 22:53 → 09-03 07:47** (partial crawl) | No |
| card activity history | `activities.csv` (in the Strategist's lab) | newest transaction **2026-08-03** | No |
| voucher history | `purchases_general.csv` (Strategist's lab) | newest order **2026-08-27** | No |
| **MAX** catalog (10,981) | `max_catalog.csv` | harvested **2026-09-03 20:03** | Re-runnable (`scripts/harvest_max.py`), but not scheduled |

**Why it will not refresh on its own:** the behatsdaa login is not
automated (TLS-fingerprint block; see below), so nothing here can re-pull
behatsdaa. Balances, new vouchers, expired vouchers, catalogue changes
since the dates above are **not reflected**. The wallet rates/ceilings
are structural and change rarely, so those age well; anything
account-level (balances, voucher status) was already stale the moment it
was captured and gets staler daily.

**For another bot asking "how current is this?":** the honest answer is
the table above. If a decision needs behatsdaa data fresher than early
September 2026, it cannot be met without a new login, which is a manual
step nobody has taken since. MAX can be refreshed by re-running its
harvester; behatsdaa cannot, today.

## Hard boundaries (inherited from CLAUDE.md, restated because money)

- **Pull only.** No feed or push of "opportunities." The bot answers a
  declared purchase intent; it never surfaces deals unprompted. A shown
  opportunity manufactures demand and works against the savings goal —
  Ishay's own reasoning: "עדיף שנצא פחות מאשר שנאכל עם 20% הנחה."
- **Read/harvest only, never redeem.** Reading what a wallet or club
  offers is the whole point; activating a benefit, redeeming a voucher,
  or spending a balance is not this bot's action.
- **Never hold redemption-value data.** See the finding below — the
  `purchases_general.csv` fields that look like card data are, best
  understanding, per-voucher redemption numbers, not a personal card. The
  distinction changes the risk category, not the handling: not brought
  into this repo either way.

## Data layout

All harvested data lives under `data/benefits/`, which is **gitignored in
full** — this repo pushes to GitHub every 30 minutes, and this is the
household's private financial data. Nothing under it is ever committed.

- `data/benefits/lab_rescue/` — the irreplaceable outputs rescued from
  the Strategist's `lab/` (which has no backup), 2026-09-03:
  - `catalog_tagged.csv` — 982 stores, manual fine-tagging + discount
    ceilings. The expensive, non-re-derivable one.
  - `catalog_full.csv` — 982 stores × category × city × online (2903
    rows). API-derivable, kept to save a re-crawl.
  - `branches*.csv` — street addresses from the branch crawl (partial).
  - `catalog_fingerprint.json` — per-wallet hash for change detection.
- `data/benefits/crawlers/` — the Strategist's crawlers, kept to adapt:
  `crawl_branches.py` (resumable, checkpoint, backoff — verified),
  `crawl_city.py`, `crawl_chains.py`, `crawl_ta.py`, `branch_crawl.py`.
  They depend on a warm browser **profile** and the Israeli exit node
  (SOCKS5 127.0.0.1:1055); they must be repointed at *our* session, not
  the Strategist's (see below).

**Deliberately NOT copied:** `purchases_general.csv` (holds a
redemption-value field, see finding), the Strategist's `lab/.env` (his
secrets), and his `state.json` / `profile/` (his session — we build our
own login).

**Read access:** `grocery_bot/benefits_catalog.py` reads
`catalog_tagged.csv` and the `branches*.csv` files from
`data/benefits/lab_rescue/` (override with `BENEFITS_DATA_DIR`), exposed
via `benefits-catalog` / `benefits-branches` in the CLI — see
`docs/MIRI_INTEGRATION.md`. Read-only, no fetching, no scoring.

## Finding, corrected 2026-09-03: not personal card data

`~/portfolio-strategy/lab/purchases_general.csv` (76 voucher rows) has
`creditCard16Digits` populated in 17 rows and `creditCardExpirey` in 73.
**The first write-up here called this "real payment-card data" and that
overstated it** — the field name alone, without checking what it holds.

Checked properly, without ever reading a value (only the non-sensitive
columns on the same rows): every populated row's `redimTypeName`
describes the same mechanism, several explicitly — **"ההטבה נטענה על גבי
כרטיס המועדון... יש להציגו בקופה / למסור את מספר הכרטיס"** (the benefit
is loaded onto the *club's own card*; show/give that number at the
register), others `מסופון` (a payment terminal at point of sale).
`benefitTypeId` is 2 or 3 (voucher/gift-benefit) for all 17. And
`paymentToken` — which would hold the actual funding instrument — is
**empty in all 76 rows here, and there is no card-shaped column anywhere
in `activities.csv` either.** No personal bank card appears in any data
rescued from this harvest.

So this is almost certainly a **per-voucher redemption/gift-card number
the club itself issues**, not Ishay's card — inferred from context, not
certain, because reading the value to confirm is exactly the thing not
to do. The real residual risk is narrower than "card data": an
unredeemed voucher's number still carries spendable value, so leaking it
risks that specific balance, not the household's bank account.

Still handled with the same hygiene regardless, because the risk is real
even if bounded: the file is not copied into this repo, and if the
voucher history is fetched from the API here, it goes into the gitignored
`data/benefits/`, worked on in place rather than duplicated.

## Verified against the data (not taken on report)

- 7 wallets, matching the handover's rates exactly: מסעדות 20%, רשתות
  15%, מזון 7%, קרפור 10%, פייטר 15%, הוקרה 25%, ראש השנה 30%.
- Row counts: 982 stores, 142 card activities, 76 vouchers.
- `crawl_branches.py` is resumable (skips done chainIDs) with backoff
  (3–5s, aborts after 4 repeated no-data as a rate/session signal).

**Not yet verifiable — needs our own logged-in session (blocked on OTP):**
the §2 API endpoint map, and the §3.2 "hidden enumeration" of the general
benefits pool, which is the real open reverse-engineering task.

## Execution order (from handover §4d)

1. **Our own behatsdaa login** — SMS OTP, Ishay hands the code over live.
   Credentials in this project's `.env` (0600). Do **not** inherit the
   Strategist's `state.json`. ← blocked, see the login status below.
2. Verify the API map; finish branch street addresses from checkpoint.
3. Reverse-engineer the general-pool enumeration (§3.2), then crawl.
4. Add the other clubs (Hi-Tech Zone, Leumi Bonus, הר"י, digital) as a
   **generic** club structure, not behatsdaa-specific — Liran uses hers,
   and the household view must be unified. Needs a `holder` field: הר"י
   and Leumi Bonus are Liran's and Ishay cannot redeem them.
5. **Miri reads the output read-only through the existing CLI seam —
   done for the catalog, 2026-09-03**, ahead of the rest of this order at
   Ishay's request: `benefits-catalog` / `benefits-branches` in
   `grocery_bot/cli.py`, backed by `grocery_bot/benefits_catalog.py`.
   Documented for familyos in `docs/MIRI_INTEGRATION.md`. Wiring on the
   familyos side is not this project's work (same boundary as every
   other Miri command). What Miri gets is the catalog only — no
   purchases, balances or vouchers, since none of that has been
   harvested yet. **Explicitly Ishay's plan, not built here:** the
   refinement — which addresses are actually relevant, deduping further,
   whatever else — is Miri's own work on top of this raw data, not
   duplicated on this side.
6. Scheduled units live here (this project has backup/timers; the
   Strategist project does not).

## Catalog is backed up to Drive (2026-09-03)

`catalog_tagged.csv` and `catalog_full.csv` are copied to
`gdrive:גורדון — קטלוג הטבות/` — verified, sizes match the originals.
Non-sensitive (store names, rates, ceilings; no personal or card data),
so Drive is fine. This is now the catalog's **only durable copy** — the
Strategist's `lab/` has no backup. Miri reading it from there is a later
step the user wants; the folder is ready for it.

## Login status: blocked by the anti-bot, 2026-09-03

The first live attempt did **not** get in, and the reason was worth
pinning down rather than retrying blindly:

- The original script reported "OTP_SENT" on the button click alone —
  false. A diagnostic showed the page returns **"שגיאה כללית"** (general
  error) after the send click, with no code field, so **no OTP was
  actually sent**. The script now verifies the code field appears and
  fails loudly otherwise (the "never trust a click" rule).
- An active **Incapsula** challenge is present on the page. A warm-up
  reload made it *worse* — a single clean load renders the login form,
  but a reload trips a full challenge page with no form. Reload removed.
- Likely compounded by the **shared exit-node IP**: the `portfolio-strategy`
  session hit behatsdaa ~10× ~30 min earlier and is itself in cooldown.
  We share `localhost:1055`, so the IP is degraded for behatsdaa
  specifically (per the per-site WAF finding in HANDOFF §3).

**Not resolved.** Needs a cooldown before retry, and possibly
coordination so two projects are not hitting behatsdaa through the same
IP at once. Do not keep firing — behatsdaa may rate-limit OTP sends per
account. Switching the Tailscale exit node would give a clean IP but is
shared infrastructure (three projects), so not done unilaterally.

### Blocking methodology, diagnosed 2026-09-03 (this is the real answer)

Probed the block directly (network + fingerprint capture, no evasion).
The homepage HTML returns 200 and earns an `incap_ses` cookie, so the IP
is **not** hard-blocked and this is **not** a timing/rate problem.
Waiting will not fix it. What actually happens:

- `x-cdn: Imperva` — the wall is **Imperva Incapsula**, fingerprint-based.
- The automation is **plainly visible**: `navigator.webdriver === true`,
  and the fingerprint is self-contradictory — the UA claims iPhone while
  `navigator.platform` is `Linux x86_64`, `plugins.length` is 0,
  `maxTouchPoints` is 0, `window.chrome` is absent. A real iPhone has
  none of those.
- Consequence: Incapsula returns **403 on `configuration.json`** (and on
  the web fonts) — the SPA cannot load its own config, so it throws
  **"שגיאה כללית"** and never fires the OTP request. That is why no code
  arrives: the send is never actually attempted.

So the block is **automation-fingerprint detection**, not the IP and not
the hour. Two ways past it:

1. **Manual login through a real, headed browser** — the same pattern
   this project already uses for the Self-Point store logins (noVNC: a
   human completes the login in a real browser on a virtual display, the
   challenge passes because it *is* a real browser, and the session is
   saved warm). In policy, robust, and consistent with how we beat the
   store reCAPTCHA. Friction: the user previously could not reach the
   noVNC port-forward from the phone (same issue as Victory, HANDOFF §3).
2. **Headless browser-stealth** (hiding `webdriver`, spoofing the
   fingerprint). This is anti-bot evasion; the harness safety classifier
   blocks implementing it, and it is a fragile cat-and-mouse path. **Not
   pursued** — needs the user's explicit direction, and even then the
   manual path is the better tool.

**Recommended: path 1.** The Strategist got in and harvested, so a real
session is achievable; the likely route was a headed/real browser, not a
headless one. The open sub-problem is the noVNC phone-access issue, which
is worth solving once since Victory needs it too.

### The block is TLS-fingerprint, not IP — and `curl` passes it (2026-09-04)

A later test overturned part of the picture above. The block is not the
IP and not authentication:

- Through the Israeli exit, **`curl` gets `configuration.json` = 200**;
  a headless Chromium gets **403** on the same URL, same IP, same moment.
- The real API is on a **different host**: `back.behatsdaa.org.il`, not
  `www` (found in the Strategist's captured traffic, `auto_api.json`).
  Endpoints are `/api/cards/GetCardGeneralInfo`, `GetCardActivities`,
  `/api/category/GetCategoryHeader`, `/api/users/getCurrentUser`, etc.
- `curl` to that API returns **401, not 403** — i.e. it *passes*
  Incapsula and the application rejects the credentials. A browser and
  `httpx` both get 403 (the Incapsula challenge) on the same call.

So Incapsula is fingerprinting the **TLS/HTTP client**, and `curl`'s
fingerprint is accepted where a headless browser's and httpx's are not.
That means **the harvest can run over plain HTTP with no browser at
all** — the thing that was assumed to need noVNC.

The 401 is simply the token: decoded, the `AccessToken` in the copied
session is a JWT with **`exp` 30 minutes after issue** (issued 06:10,
expired 06:40 on 2026-09-03). So:

- **Why it worked for the Strategist and not later:** he ran against a
  *live* token; it died 30 minutes on. When the session was handed over
  "urgently, it may expire", the token was already ~28h dead — the
  handover could never have worked, and the urgency was misplaced.
- **What is proven:** reading the API over HTTP, given a fresh token.
- **What is NOT yet proven:** the *login* over HTTP. The login endpoints
  (`/api/users/…`) answer 307-to-self to a bare POST, so the exact call
  shape (path, payload, how the OTP is requested and submitted) is not
  nailed down. The only known-working login is the Strategist's
  browser-based `login_sms.py`. Cracking an HTTP login would remove the
  browser from the loop entirely; it may or may not pan out, since the
  auth step specifically could still want a browser.

### Adopting the Strategist's session did NOT help — tested 2026-09-03

The Strategist's working session was handed over as urgent (it would
"expire"), reversing the earlier "build your own login" instruction, and
`~/portfolio-strategy/lab/state.json` + `profile/` (217MB) were copied to
`data/sessions/behatsdaa_state.json` and `behatsdaa_profile/`.

**It changes nothing, and the reason matters more than the result.**
With the copied profile loaded:

- 12 behatsdaa cookies carried over — the session really is present, and
  the login form no longer appears.
- `configuration.json` still returns **403**. So do the web fonts.
- `navigator.webdriver` is still `true`.
- The SPA never boots: **zero behatsdaa API calls**, no ₪ amounts, and
  clicking "ארנק דיגיטלי" does not navigate.

**The blocker was never authentication.** Incapsula rejects the
*browser*, not the *identity* — so a valid session cannot fix a
fingerprint check, however fresh it is. The urgency framing was
misplaced: this session is not a perishable asset being wasted, it is an
asset that cannot be used from a headless browser at all.

The copied files are kept (gitignored, 0600) because they are plausibly
**necessary but not sufficient**: with a browser Incapsula accepts, the
auth cookies would save a re-login. The missing piece is the browser, not
the session — which is the same conclusion as before, now tested rather
than reasoned.

## The other clubs — access assessment, 2026-09-03

Asked: can public aggregators cover מקס/כאל/לאומי בונוס at the depth we
have for behatsdaa, or must we log into each? Checked rather than
guessed. **The answer splits by club, because the clubs have different
data models.**

### The distinction that decides it: two data layers

- **Catalog layer** — which merchant, what discount. **Public.**
  Aggregators and issuers' own sites carry it.
- **Account layer** — my balance, my remaining ceiling, what I have
  already used, which voucher expires when. **Never public, login-only.**

behatsdaa gave us both *because we logged in*. That is the entire reason
that dataset is rich. An aggregator can never give the second layer.

**So the real question per club is: does its value live in the account
layer?** For a prepaid-wallet club (behatsdaa) it absolutely does — a
15% wallet with ₪0 balance is worthless, and the money already found was
in *unused ceilings and expired vouchers*, which is account state. For a
card-linked discount club, there is often no balance to miss, and the
catalog is most of the value.

### Per club

| Club | Model | Catalog access | Verdict |
|---|---|---|---|
| **מקס / MAX** | Card-linked discounts (הטבות פלוס, thousands of businesses) | `/benefits/bizplus` returns 200 and is **explicitly `Allow`ed in their robots.txt** | **Scrape first-party, no login.** Best case: authoritative and permitted |
| **כאל / CAL** | Card-linked discounts | **HTTP 400, and a real headless browser is rejected too** — "The requested URL was rejected. Please consult with your administrator" with an F5/Akamai support ID, on root and `/benefits` alike (re-tested 2026-09-03 with Playwright, not just curl) | Not a curl artefact: CAL blocks automation outright. Getting in would need fingerprint-spoofing, which is the evasion line. **Take CAL's catalog from ClubHub instead** |
| **לאומי בונוס** | **Accrual** — bonus earned on card spend, redeemed for vouchers at 10–20% uplift | n/a yet | **Has real account state (accrued balance).** Catalog alone cannot answer "how much bonus do I have". Also **Liran's**, not Ishay's |
| **הר"י** | Not yet characterised | n/a yet | **Liran's.** Login needs her credentials and consent — not merely a technical step |

### ClubHub as the fallback aggregator

Public catalog, no login for browsing, and `robots.txt` is fully
permissive (`Allow: /`, a published sitemap, no `Disallow`, no
crawl-delay). Covers CAL and MAX explicitly. Two caveats:

- **It is shallower than what we have.** No spending ceilings, no branch
  addresses, no validity dates — exactly the fields that make the
  behatsdaa catalog actionable. *Provisional:* this came from a
  summarised page read, not from inspecting their API; the site is a JS
  app, so confirm against real traffic before relying on it.
- **It is a copy of a copy.** Third-party freshness is their scraping
  cadence, not the issuer's truth. First-party (MAX) beats it where
  available.

### MAX — harvested 2026-09-03, no login needed

**Solved.** `scripts/harvest_max.py` pulls MAX's public "הטבות פלוס"
catalog into `data/benefits/max_catalog.csv`, and it needs **no
credentials, no session, and no Israeli exit node**.

Ishay offered his MAX login (ID + last 8 card digits, with an SMS code to
follow). **It was declined and never used** — the catalog is public
marketing material aimed at non-customers, proved by driving the page and
then the API anonymously. His account would have given a different and
bigger thing (his card, his transactions) that a catalog does not need,
and that the budget project already sees via statements. *Lesson worth
keeping: "can I get this data?" and "should I log in?" are separate
questions, and the first should be answered first.*

**The endpoint, found in the page's own Angular TransferState blob rather
than guessed** (a bare `GET` 404s without these params):

    GET /api/benefitsPlus/getDiscountsPlus?isMobile=false&loadLobby=false&page=N

- **11,341 discounts**, 12 per page, with an `isLast` flag.
- Each record carries name, discount %, category, street address, city,
  region, phone, website, description, update date — and latitude/
  longitude. **Richer than ClubHub**, which has no addresses at all, and
  richer per-branch than the behatsdaa catalog.
- Permitted: `robots.txt` `Allow`s `/benefits/bizplus` and none of its
  192 `Disallow` rules covers `/api`.
- The harvester is slow on purpose (~1.2s + jitter between pages),
  checkpointed after every page so it resumes instead of restarting, and
  bounded at 1200 pages so a changed API cannot spin forever.

### Harvest readiness for the rest — checked 2026-09-03

Asked whether the other clubs could be catalogued into the behatsdaa
shape right away. **No — what exists so far is the access map above, not
a single merchant record from any of them.** What the probing established,
so it is not redone:

- ~~MAX's API signature is unknown~~ — **solved, see above.** Worth
  keeping one detail: the `/benefits/bizplus` page shows only a
  **rotating carousel of 4 cards**, so scraping its DOM looks like a tiny
  catalog and quietly gives a different 4 each visit. The list is not on
  the page; it is behind the paged API.
- **MAX needs no Israeli exit** — it answers 200 on a direct connection,
  so harvesting it does not spend the household's home bandwidth (same
  reasoning as the price feeds).
- **הר"י's public page is a landing page, not a catalog.**
  `ima.org.il/VIP/` returns 200 (Cloudflare, not blocking) and `robots.txt`
  allows it, but the 112KB contains no merchant or discount data — only
  login references and policy links. So the catalog sits behind the member
  login after all; the earlier hope that it might be public is wrong.
- **מועדון יחד is Isracard-operated** (`marketing.isracard.co.il/clubs/yahad/`)
  and returns **403 from Cloudflare**.

Doing this properly is a per-source reverse-engineering job of the same
order as the original behatsdaa harvest — not a normalisation pass over
data we already hold.

### CAL and ClubHub — measured 2026-09-04, and NOT harvested

Investigated properly and then declined, because the numbers do not
justify it. Recorded so it is not re-derived.

**A correction first: CAL's benefits store is a different domain** from
the one previously tested. `cal-online.co.il` (banking) is Akamai-blocked;
the store is `cal-store.co.il`. The earlier conclusion was right for the
wrong reason. Testing the right domain: **`cal-store.co.il` returns 503
from `rhino-core-shield`**, `diners-store.co.il` the same, and
`hvr.co.il` (חבר) 403s. So CAL is genuinely not first-party harvestable
— just not for the reason first written down.

**What ClubHub actually is:** not a merchant-discount catalogue. It is a
**per-product price comparison across clubs** — one attraction or gadget,
priced via MEGALEAN / PAIS_PLUS / MAX / CAL / BEHATSDAA and so on. A
different data type from the MAX and behatsdaa catalogues, which map
*merchants* to *rates*.

It is technically easy: Next.js with `__NEXT_DATA__` server-rendered into
every page, `robots.txt` fully permissive, no anti-bot. **The problem is
yield, measured on a random sample of 12 real deal pages:**

- 25,000 deal pages across five sitemaps → **~8 hours** at polite pacing,
  versus 20 minutes for MAX's entire catalogue.
- **13 provider-items across 11 parsed pages** — about 1.2 per page.
- **10 of 11 pages carried a single provider**, so there is no comparison
  on them at all.
- **CAL appeared on 1 of 11 pages (~9%).**

So eight hours of someone else's bandwidth buys perhaps ~2,000 one-off
product offers, mostly without a comparison, for a club whose *regular
spending* discounts we still would not have. **Declined.** If CAL is ever
needed, a targeted lookup at decision time is the sane shape, not a bulk
crawl.

**Worth knowing anyway — ClubHub also carries the login-blocked clubs:**
`BEHATSDAA` / `BEHATSDAA_LOADABLE`, `BEYAHAD` (the Histadrut's club, on
the *same platform* as behatsdaa — `hist.org.il/card/chargingCard/…`),
and `HVR` = **חבר**, not הר"י. If the account-layer clubs ever need a
public window, this is one.

### Recommendation

Do not treat this as one decision. **MAX: scrape first-party, no login
needed.** **CAL: do not fight Akamai — take its catalog from ClubHub**
unless a gap proves otherwise. **Leumi Bonus and הר"י: both are Liran's,
and Leumi Bonus has account state a catalog cannot supply** — so those
two are the only ones where a login is genuinely unavoidable, and both
need her, not Ishay. Confirm הר"י's model (wallet vs discount) before
committing to a scraper for it.

## Wallet rates and ceilings, and how a card charge decodes

Extracted from `catalog_tagged.csv` on 2026-09-04 for the budget project,
worth keeping because it is asked repeatedly:

| Wallet | Rate | Ceiling ₪ | Stores |
|---|---|---|---|
| ראש השנה 30% | 30% | 500 | 699 |
| מבצע הוקרה 25% | 25% | 500 | 676 |
| רשתות בהצדעה | 15% | 1500 | 676 |
| פייטר | 15% | 2500 | 545 |
| מסעדות | 20% | 500 | 262 |
| מזון+אונליין | 7% | 1500 | 22 |
| קרפור | 10% | 750 | 12 |

**Two different fields — both owned here, named explicitly.** Gordon
(this project) owns these numbers; Nigel and Rob point here rather than
restating them.

- **`maxBalance`** — the ceiling column above (₪1,500 on מזון, ₪2,500 on
  פייטר, …). Source: the catalogue (`catalog_tagged.csv`). This is the
  cap on how much a wallet can *hold*, not a monthly figure.
- **`maxDepositForMonth`** — **₪700/month on the 7% food wallet (מזון+
  אונליין)**. Source: Ishay's account screen, confirmed verbatim
  2026-09-04: *"ה-700 זה הקאפ החודשי להטענה לכרטיס רשתות מזון (בפועל
  התשלום הוא 700×0.93)."* So the household loads up to ₪700/month and is
  charged ₪651 (700 × 0.93 = the 7% discount) — which is exactly the
  ₪651 statement line the decode below resolves to this wallet.

The behatsdaa API also exposes `maxDepositForMonth` per wallet in
`GetCardGeneralInfo`, but that needs a logged-in session we do not have,
so ₪700 is confirmed only for the food wallet, from the account screen.
The `maxBalance` column, being from the catalogue, is complete.

**A charge decodes as `face value × (1 − wallet rate)`** — verified
against the budget project's real statement lines:

- ₪651 = a ₪700 load at 7% → the food wallet.
- ₪850 = a ₪1,000 load at 15% → פייטר *or* רשתות; the two share a rate,
  so the amount alone cannot separate them.
- ₪1,729 and ₪410 match no single load, so some statement lines
  aggregate several loads.

**`activities.csv` cannot attribute a load to a wallet.** All 54 load
rows are identical — `businessName` "טעינת כסף", `chainName` "כרטיס
נטען", no wallet identifier. The decode above is the workaround.

### Three of the wallets are the same store list — measured 2026-09-04

Asked whether each non-food wallet is dominated by one kind of spending,
so a load could be filed to a category. **It cannot, and the reason is
structural.**

Store-set overlap from `catalog_tagged.csv`:

    רשתות ∩ הוקרה = 675 of 676
    רשתות ∩ ר"ה   = 673 of 676   (ר"ה adds 26)
    פייטר ∩ רשתות = 400 of 545   (+144 of the 262 restaurants)

**רשתות (15%), הוקרה (25%) and ר"ה (30%) are the same ~676 shops at three
different rates**, so nothing about *what was bought* separates them.
פייטר is roughly רשתות plus restaurants. Their category mixes are
correspondingly near-identical — ~21% בילוי ופנאי, ~19% ספא, ~16% אופנה,
~11% אירוח ונופש — and no wallet has a dominant type.

**The methodological trap, flagged to the budget project:** this
distribution counts *eligible shops*, not *where money went*. A wallet
being 16% clothing shops says nothing about the share of spend that was
clothing. It must not be used as a split key for real money.

**A likelier discriminator is the date.** Since the three share shops,
the only thing separating them is the rate, and the rates look seasonal
(ר"ה = Rosh Hashana, הוקרה = a tribute promotion). Load dates exist on
the budget side; this project cannot attribute a load to a wallet to
verify it.

### The decode is not uniquely invertible — measured 2026-09-04

`scripts/behatsdaa_charge_table.py` emits the full face→charge table
(`data/benefits/behatsdaa_charge_decode.csv`) *and* a collision report,
because the table on its own is a trap:

- Over a ₪10 grid to ₪3,000: 1,544 distinct charges, **227 (14%)
  reachable from two different rates.** ₪84 is either ₪105@20% or
  ₪120@30%; ₪180 is ₪200@10% or ₪240@25%. A decoder assuming uniqueness
  files real money under the wrong category silently.
- **Restricting candidates to the 15 observed load sizes cuts ambiguity
  from 14% to 2%** — 88 charges, only two of them ambiguous:
  ₪255 (₪300@15% or ₪340@25%) and ₪560 (₪700@20% or ₪800@30%).
- **Inherent and unfixable by amount:** פייטר and רשתות בהצדעה are both
  15%, so no charge can separate them. They are different wallets with
  different ceilings (₪2,500 vs ₪1,500), so treating 15% as one bucket
  merges two things.

## Open questions for Ishay (also in HANDOFF §5)

- Availability window for the OTP login.
- The eligibility file: which clubs the household is actually in
  (declared by hand — ClubHub lists 100+, the household is in ~6).
- Success criterion for the whole thing.
