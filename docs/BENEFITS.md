# Benefits harvesting — behatsdaa + other clubs

**Owner:** this project (Gordon). Decided by Ishay 2026-09-02 (verbatim
quote and date recorded in `GOALS.md` under החלטות שהתקבלו). **Build
authorised directly by Ishay 2026-09-03.** Miri only *reads* the output,
through the existing CLI seam — this is not a new bot.

Full handover from the Strategist session: `~/portfolio-strategy/BENEFITS-HANDOFF.md`.

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

## Open questions for Ishay (also in HANDOFF §5)

- Availability window for the OTP login.
- The eligibility file: which clubs the household is actually in
  (declared by hand — ClubHub lists 100+, the household is in ~6).
- Success criterion for the whole thing.
