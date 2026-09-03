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
- **Never hold card details.** See the finding below. Card columns are
  not brought into this repo at all.

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

**Deliberately NOT copied:** `purchases_general.csv` (holds card data —
see finding), the Strategist's `lab/.env` (his secrets), and his
`state.json` / `profile/` (his session — we build our own login).

## Finding: the voucher history holds real card data

`~/portfolio-strategy/lab/purchases_general.csv` (76 voucher rows) has
populated payment-card columns — verified by counting non-empty cells,
never by reading a value: `creditCard16Digits` in 17 rows,
`creditCardExpirey` in 73, `dtsRedimCode` in all 76. The behatsdaa
`purchaseHistory` API returns card fields in its payload.

Consequence for this project: the voucher history is genuinely useful
(an expiry radar is worth building — 5 vouchers expired unredeemed, 9
never redeemed), but it must be re-fetched from the API with the card
columns **stripped on the way in**, and stored under the gitignored
`data/benefits/`. The card columns never enter this repo.

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
   Strategist's `state.json`. ← blocked on Ishay's availability.
2. Verify the API map; finish branch street addresses from checkpoint.
3. Reverse-engineer the general-pool enumeration (§3.2), then crawl.
4. Add the other clubs (Hi-Tech Zone, Leumi Bonus, הר"י, digital) as a
   **generic** club structure, not behatsdaa-specific — Liran uses hers,
   and the household view must be unified. Needs a `holder` field: הר"י
   and Leumi Bonus are Liran's and Ishay cannot redeem them.
5. Miri reads the output read-only through the existing CLI seam.
6. Scheduled units live here (this project has backup/timers; the
   Strategist project does not).

## Open questions for Ishay (also in HANDOFF §5)

- Availability window for the OTP login.
- The eligibility file: which clubs the household is actually in
  (declared by hand — ClubHub lists 100+, the household is in ~6).
- Success criterion for the whole thing.
