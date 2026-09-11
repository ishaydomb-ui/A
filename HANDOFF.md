# HANDOFF — current state of grocery-automation

Living document. Rewritten on every **עוגן**, not appended to: a handover
note that grows into a diary stops being read. History lives in git and
in the progress log in [`GOALS.md`](./GOALS.md); this file answers one
question only — *if someone picked this up right now, what would they
need to know?*

**Last anchored:** 2026-09-07 09:10 (host time, CEST)
**Session:** https://claude.ai/code/session_01AR7esAYdoXQ71HXtqJPpQV
**Branch:** `claude/online-grocery-automation-b7pq4g`
**Status is in `git log`, not hand-typed here.**

**Since the last anchor (`c178749`):** a field audit of the whole
Telegram surface, a **real order placed by Ishay on 2026-09-07** that
went partly wrong and was dissected, and the build that came out of it.
Eleven commits, `53ec0f7`..`e600e91`. 663 tests pass.

## 1. Where things stand

**Running in production on the Contabo VPS**, as user systemd units:
`grocery-bot.service` (the bot), `grocery-prices.timer` (now refreshes
**every** chain, see below), `grocery-backup.timer`, `grocery-doctor.timer`,
`grocery-alert@.service`.

**Store data — the big change this round.**

| Chain | Prices | Promotions | Cart |
|---|---|---|---|
| Shufersal | own feed, 5,820 products | yes (`catalog_promotions`) | yes |
| **Tiv Taam** | **portal feed, 20,889 products** | **yes, 25,642** | yes |
| Politzer / Osher Ad / Rami Levy / Fresh Market / Keshet | portal feed | Politzer + Osher Ad only | no |
| Yohananof | refused — feed 618 days stale | — | no |

**Tiv Taam publishes a public feed and always did.** This file said for
a week that it had none ("checked: prices.tivtaam.co.il does not exist,
and it is absent from the usual publisher portals"). It is on
`url.publishedprices.co.il` — the portal this project already read for
five other chains — under username `TivTaam`, hourly, 54 branches, with
`PromoFull` beside `PriceFull`. The earlier check guessed a subdomain,
did not find one, and recorded an absence; publishing is required by the
2014 food-competition act, so "not found" should have prompted a harder
look. **Branch 019 (רמת החייל)**, confirmed by Ishay and corroborated
against the 301 barcodes he has really paid for (253 overlap, 47% exact,
₪0.40 median gap — beating נתניה/002 and the ליקוט site/519).

**`refresh-prices` now refreshes all chains.** `refresh_all_portal_chains`
existed, was tested, and had **no caller anywhere**, so the timer only
ever pulled Shufersal — which is why an audit found the portal chains
five days stale beside a Shufersal feed hours old.

**The Telegram surface**: `/start`, `/list`, `/price`, `/deals`,
`/alldeals`, `/chaindeals`, `/refresh_prices`, `/stockup`, `/cheaper`,
`/list_full [core|full|everything|fresh|pantry]`, `/digest`,
`/start_order`, plus new this round: **`/basket`** (the list priced at
every chain, ✅/🔄/❌ per line), **`/lastdeals`**, **`/done`**. `/propose`
was retired (used once ever, abandoned before its own redesign).

**The standing cart** (`standingcart.py`) is the current design, chosen
by Ishay 2026-09-07: `/done` records the shop and refills both carts from
the `everything` list (147 products) plus deals, so the cart is full
before he opens it and finishing is only deleting. Refill takes ~2.5h
across both chains; that is fine because it runs immediately after
`/done` and the cart is not needed for ~5 days.

## 2. In flight

Nothing half-built. The order-history comparison Ishay asked for is
**waiting on data, not on work**: his 2026-09-07 order had still not
appeared in Shufersal's own order history hours later, so "what was
actually bought vs what the list proposed" cannot be computed yet. The
nightly sync will pick it up.

## 2b. Malls — answering "which shops here have a discount" (2026-09-09)

`grocery_bot/malls.py` + `benefits-mall` in the CLI. Six malls modelled:
שבעת הכוכבים הרצליה 52 chains, דיזנגוף סנטר 41, ביג פאשן גלילות 37,
עזריאלי ת"א 35, רמת אביב 32, TLV גינדי 23.

**The rule that matters if you extend it:** a mall is decided by the
*address*, never by what a branch calls itself. Measured — "עזריאלי" in
a branch name returns shops in Holon, Haifa, Ramla, Modiin and Akko;
"גלילות" catches קניון פי גלילות, a different site. Three malls also
need a house number, because they sit on ordinary streets (דיזנגוף 50 is
the Center, 116/122/269 are not; החשמונאים 88-132 is TLV).

Adding a mall means reading its real address forms out of the harvest
first — every term in the file was observed, none invented. 28 tests,
including 35 real phrasings.

## 2c. Understanding: the loop and the phrasebook (2026-09-09/10)

**Hybrid loop** (`grocery_bot/loop.py`), approved by Ishay: runs only
when the classifier returns `unclear`, returns the same ParsedMessage,
so handlers and gates are untouched. `sanitise` refuses `start_order`,
`add_to_cart` and `shopped` whatever the model returns — `shopped` is
guarded because it refills both real carts. The barrier is in code, not
in the prompt, deliberately. Capped at 30s after the sweep found one
message averaging 62.7s where its peers were 15.6s.

**Phrasebook**, two tiers. `tests/test_phrasebook.py` is free and runs
with the suite (mall/merchant/product resolution — table lookups).
`scripts/check_understanding.py` is opt-in and costs a model call per
phrasing; it repeats each one and reports a *rate*, because
classification is not deterministic. Last full sweep 2026-09-10:
**84/84 at --repeat 3, no instability.**

`--export` writes the corpus as JSON for Miri, who routes messages
before they reach this project. Intent labels are deliberately excluded
from the export: her question is routing, mine is resolution.

**Not to be re-derived:** קסטרו and רנואר carry no behatsdaa benefit at
all (zero occurrences in the raw catalogue, 2026-09-10). "אין הטבה" is
the truthful answer, not "לא מצאתי".

## 2d. External AI review — verification in progress (2026-09-11)

Ishay commissioned an outside review of the shopping flow, from
`docs/reports/2026-09-11-shopping-flow-and-user-interface.pdf`. The
reviewer had **no code or account access** — every claim is inference
from that report, so each needs checking before it is acted on. Three
checked so far:

1. **VERIFIED BUG — FIXED 2026-09-11.** "A perishable enters the cart
   despite the rule." True: in `dealfill._barcode_picks` (the Tiv Taam
   path) `_looks_perishable` was guarded by `if not familiar`, so a
   previously-bought perishable skipped the check entirely. The
   Shufersal name path applies `pantryable` to everything, so the two
   chains disagreed. `_barcode_picks` now takes `pantryable_only` and
   applies the guard to familiar picks too; novel picks keep it
   unconditionally, since there the name is the only evidence there is.
   Verified against the live DB: "בצק פריך מלוח 900 גר מעדנות" at 41%
   off was in the default Tiv Taam picks before and is not now, and
   appears only under `pantryable_only=False`. Three tests pin it
   (`tests/test_dealfill.py::BarcodeChainDealTests`).
2. **Already handled; my report was the gap.** "A short basket can still
   look cheap." `basketview` already compares "על N פריטים זהים",
   includes delivery (`saving_with_delivery`), and refuses comparison
   below `MIN_COVERAGE` with an explicit line. The PDF described only
   the substitute exclusion, which is why the reviewer inferred a hole.
3. **Partly right.** "Exact match by name prefix is not enough."
   `_name_match_rank` is four-tier with word boundaries, not a blind
   `startswith` — but the reviewer's point about **size and selling
   unit** is unverified and still open.

4. **VERIFIED, AND THE WORST ONE — partly fixed 2026-09-11.** Their 5d,
   "a barcode match does not prove the promotion applies." Correct, and
   the money was larger than the perishable bug: every one of the 12
   picks the Tiv Taam cycle proposed that day was a "השני ב־" promotion
   whose `discounted_price` is the price of the *second* unit. Reported
   saving ₪161.87; real saving at quantity 1, zero. `min_qty` does not
   flag them (1,530 live promotions are worded multi-buy and carry
   `min_qty = 1`), so `_needs_more_than_one` checks the wording too.
   Refused from auto-add now; 6 genuine single-unit deals remain.
   **Open for Ishay:** whether to buy two and make the promotion real,
   or surface them in the report as "worth taking two" without adding.
   **Unverified assumption left standing:** club-priced deals ("יין
   49.90 - מועדון") are kept, assuming the account is a TivCoins member.
   Not checked against the account.

5. **Found while fixing 1, nobody's claim.** `_looks_perishable` was a
   substring test, so "טרי" matched אטריות/פטריות/טריאקי/ניטריל and
   "פיר" matched פירורי לחם — 5,591 of Tiv Taam's 24,741 names read as
   perishable. Harmless while the guard only refused strangers; once it
   runs on the weekly list it withholds real deals in silence. Now
   matched at a word start behind an optional prefix letter; 4,921 names
   flagged, the real perishables all still caught.

**All ten sections are now checked.** The full response —
what was fixed, what is already handled, what is not being implemented
and why, and the technical clarifications worth sending back to the
reviewer — is in
[`docs/reports/2026-09-11-external-review-response.md`](docs/reports/2026-09-11-external-review-response.md).

Three things there wait on Ishay rather than on code:

- **Buy two?** Multi-buy deals are reported, not bought. Adding two when
  the promotion is unambiguous is a small change if he wants it.
- **Three tiers instead of fill-and-delete** (reviewer §1) — a direct
  conflict with his 2026-09-06 decision to fill from "באמת הכל" (147).
- **Request states: added / ordered / delivered** (reviewer §7) — the
  best recommendation in the review, and factually right:
  `adhoc_requests` carries a binary `consumed` flag. Not done today
  because what each state *does* is a product decision. **This is the
  first thing to pick up next.**

## 3. Blocked, and on what

- **The running bot is older than the code.** `grocery-bot.service`
  started 2026-09-09 17:15; every commit since — the loop, the 30s cap,
  and `safesend` — is on disk and not live. One command fixes it:
  `systemctl --user restart grocery-bot.service`. Flagged to Ishay
  2026-09-10; his call, not done here.
- **The Israeli exit runs through Ishay's iPhone, not the TV box.** The
  Xiaomi Android TV box has been unreachable since ~2026-09-01 ("offline,
  last seen 6d ago", `tailscale ping` times out). The phone works but is
  erratic — measured 1.7s, 1.9s, then 8.1s for one page — and that caused
  repeated 30s Playwright timeouts during the real order. Needs someone
  physically at the box.
- **Tiv Taam's autocomplete is still the weak link for unresolved terms.**
  `localmatch` now resolves 34 terms locally from the feed, but the rest
  still go through the dropdown, which returned zero rows for `ביצים`
  three times running while the site finds it fine by hand. The proper
  fix is the Self-Point API's `filters[must][term][localBarcode]`, which
  the API honours, so the browser only ever performs the add.
- **behatsdaa needs one more login; the address work is done.** Ishay
  logged in 2026-09-09 and the session was captured, which unblocked the
  branch harvest: **937 of 972 chains with physical branches now have
  street addresses, up from 222** — the "77% have no address" blocker
  cited all over these docs is closed. The session has since expired
  (`AccessToken` and `.AspNetCore.Session` are gone from both the saved
  state and the browser profile). Two things wait on a fresh login:
  `scripts/harvest_wallet_chains.py` (per-wallet רשימת רשתות — queued,
  exited `SESSION_EXPIRED` on its first run) and the last **35 chains**
  without addresses. Restart the watcher via
  `systemd-run --user --unit=behatsdaa-watch --collect -E DISPLAY=:99
  -E WATCH_HOURS=8 -E PLAYWRIGHT_PROXY=socks5://localhost:1055
  .venv/bin/python3 scripts/behatsdaa_watch_login.py` and have him log in
  on noVNC; detection now reads the browser's whole cookie jar and will
  catch it.
- **Victory** — unchanged, see `docs/BENEFITS.md`. Still needs the
  noVNC-from-phone route.

## 4. Handover procedure

Run on **"העברה"**, after the anchor. Written 2026-09-01; the user
referred to a section 4 that did not exist yet, so this is a proposed
procedure — correct it rather than work around it.

1. **Anchor first.** Nothing below is worth doing on top of unsaved work.
2. **No bundle mechanism exists here** (unlike familyos, which bundles to
   Drive). GitHub is the only off-box copy, so a push landing is the
   whole backup — verify it rather than assume it.
3. **Verify the tree is genuinely clean** — `git status`, and confirm the
   push landed on origin rather than trusting the command's exit code.
4. **Confirm production still runs.** `systemctl --user list-timers` and
   `systemctl --user list-units --failed`. A handover that leaves a dead
   timer behind hands over a silent failure.
5. **Refresh §1–§3 of this file** so they describe reality now, not when
   they were written.
6. **State the open questions waiting on the user** (§5). These are the
   things a new session cannot derive from the code and would otherwise
   silently re-litigate.
7. **Record the conversation id** above, so the transcript can be found.
8. **Name what is deliberately not done**, with the reasoning — otherwise
   the next session rediscovers a decision as though it were a bug. The
   clearest current example: uncommitted work is reported, never
   auto-committed, because this repo pushes to a code host and holds
   store credentials.

## 4a. Live and owned elsewhere

`grocery-nudge.timer` runs **in the familyos project, not here** — hourly,
sending through the מירי bot into the family group, approved by the user
on 2026-09-01. This repo only supplies the text: `cli nudge` prints a
message when a shop is due and prints nothing when it is not.

Two rules live on that side and are deliberately not duplicated here:
it will not send outside 09:00–21:00 Israel time, and it records the
sent date only after a confirmed delivery so a failed send retries.
Replies are routed by intent — free text to `add-item`, card
confirmations to `confirm-card` — so nothing depends on knowing a nudge
was sent.

The user's standing decision from the same conversation: **future systems
should reach the household through מירי rather than each gaining its own
bot.** This CLI is the seam.

**Two design rules that came out of building it, worth reusing:**

*A question the household can be asked in two places needs one record,
not two agreeing behaviours.* The ₪700 card question appears both in the
nudge and at this bot's cart hand-off; both read and write
`benefit_confirmations`, keyed `(kind, month)`, through `cardreminder`.
Confirming anywhere silences everywhere by construction. Being asked
twice about one allowance is the kind of small indignity that makes
people stop reading a bot at all.

*Buttons and intent-routing are not interchangeable; the surface
decides.* A button cannot be wrong about what a tap meant, so it wins in
a one-to-one flow like this bot's. In the shared family group a button
raises a second question — whose tap counts? — so מירי routes replies by
intent there instead. Neither is the better technique in general.

## 4b. Stray system-scope units — not ours, and red

Two units in `/etc/systemd/system/` carry this project's name, were
installed by someone else on 2026-09-01 evening, and have failed on every
trigger since:

- `grocery-doctor.service` — a copy of our user unit. It uses `%h`, which
  at system scope resolves to `/root`, so its paths cannot exist.
  `Result: resources`.
- `grocery-backup-daily.service` — runs `/usr/local/bin/grocery-backup.sh`,
  which bundles to Drive. Not ours.

**Our backup is unaffected and healthy.** Ours are user-scope units that
push to GitHub, touch rclone nowhere, and run as codex by construction.
Verified: heartbeat fresh, doctor reports healthy, branch level with
origin, and all four `OnFailure=` targets load.

Worth knowing before anyone "restores" that second backup rather than
deleting it: it has never once succeeded — `gdrive:Backups/grocery` does
not exist — and even had it run, `git bundle create ... HEAD` captures a
single branch rather than `--all`. It was confidence without coverage,
which is worse than no second backup at all.

Both have **enabled timers at system scope**, so they are not dormant:
`grocery-doctor.timer` fires hourly and fails hourly, and
`grocery-backup-daily.timer` fires nightly at 02:00. Their `OnFailure`
target does not exist there (an older `%n` produced
`grocery-alert@grocery-doctor.service.service`), so every one of those
failures is silent.

Removing them needs sudo, which this session does not have. It is the
user's call, and they are someone else's work. The full set:

    sudo systemctl disable --now \
      grocery-doctor.timer grocery-doctor.service \
      grocery-backup-daily.timer grocery-backup-daily.service

## 4c. Traps worth not repeating

Started as two, grown since — all the same shape: **a check that returns
the same result whether or not the thing is true is not evidence.**

- `systemctl is-active` proved the bot was running, not that it ran the
  new code — a 30-minute-old process satisfied it, and a change was
  reported live while never being served. Use `scripts/restart_bot.sh`,
  which compares the main PID either side and fails if it did not change.
- **A heartbeat file proved the *script* ran, not that the step inside it
  *succeeded*.** `auto_push.sh`'s DB-backup heartbeat only ever recorded
  "the script executed," so `rclone` silently resolving to nothing under
  systemd `--user`'s PATH (it lives in `~/bin`, off that PATH) looked
  identical to a healthy backup for as long as anyone checked — the real
  error was also swallowed by `>/dev/null 2>&1`, so even reading the
  journal showed nothing. Caught 2026-09-05 by Arthur from raw journalctl
  counts, not from any check this project had in place. Fixed with a
  full path and a heartbeat field for the *inner* step's own outcome
  (`db_backup_failing_since`), not just the outer script's. The general
  form: a liveness check on a wrapper proves the wrapper ran, never that
  the thing it wraps did what it was supposed to.
- A rendered Telegram link proved it looked tappable, not that tapping it
  did anything: a `t.me` deep link opened from inside the bot's own chat
  arrives as a bare `/start` with the payload stripped. The cross-chain
  list is now the plain `/chaindeals` command, registered in the menu.
- A failed-units listing cannot show a *waiting* timer, so "no timer
  exists" was concluded from a listing that could never have shown one.
- An unbounded `until [ -f X ]` loop cannot tell "not ready" from "never
  coming": one waited 23 hours for a file whose producer had already
  died. Bound every such loop.
- A login script reported "OTP_SENT" on a button click, not on a code
  field actually appearing — the site had returned a general error and
  no code was ever sent. Same shape as the rest of this list. Now
  verified against real page state (`scripts/behatsdaa_login.py`).
- **A warm-up reload made an anti-bot challenge worse, not better** —
  intuition said "give the JS challenge time to settle," but a single
  clean load rendered the login form while a reload tripped a full
  challenge page. Worth remembering before adding a "just in case" reload
  anywhere near Incapsula.
- **A partial fix across sibling systemd units is a fix that reads as
  "done" until the one skipped unit fails.** `d8d1132` fixed the
  `apparmor_restrict_unprivileged_userns` / `218/CAPABILITIES` kernel
  issue on four `--user` services; `grocery-prices.service` — a fifth,
  equally affected one — wasn't in that commit, and sat `failed` silently
  for ~19 hours after the next reboot (found 2026-09-05, doing the
  handover's own §4 checklist). When a fix touches N files matched by a
  shared cause, grep for every file matching that cause, not just the
  ones already in front of you.
- **A failed message did not just lose the message — it lost the cart.**
  On the real order of 2026-09-07 the summary send raised
  `BadRequest: can't find end of the entity` (a `*` in "עגבניות חתוכות
  דק 400\*3ג", the trap `mdtext.py` already documents), and because
  `start_order` sends the summary *before* asking about ambiguous items,
  the exception aborted the rest of the flow. 39 Tiv Taam items were
  sitting as unanswered questions, so that cart ended with 7 items while
  Shufersal's was full. Two lessons, not one: escape store text
  everywhere it enters Markdown, and never let a cosmetic send sit in
  front of the steps that actually finish the job. Both fixed; the
  summary now goes through `_send_markdown`, which degrades to plain
  text.
- **"The carts are filled, only the report was lost" was reported to
  Ishay before checking the database.** It was wrong — see above — and
  the DB said so plainly (`pending_ambiguities`, 39 rows, one query).
  Check the state before characterising a failure to the user.
- **Asking the user is not free, and 39 questions is not a workflow.**
  Tiv Taam's adapter treats any multi-row autocomplete as ambiguity and
  delegates it. That is correct per item and absurd in aggregate. Where
  a local catalogue exists, resolve there and ask only about what is
  genuinely unclear.
- **A resolver that always answers is worse than one that refuses.**
  The first `localmatch` picked בננה ציפס (crisps) for "בננה" — the Tiv
  Taam feed carries no fresh bananas at all, so every candidate was
  wrong — and מלפפון במלח (pickles) for "מלפפון", because it sorted
  cheapest-first and pickles undercut cucumbers. Product memory buys the
  same thing every week without asking again, so refusing is cheap and
  being confidently wrong is not.
- **A dead learning loop looks exactly like a well-behaved one.**
  `stock.py` says removals matter more than the thresholds, and
  `skipped_count` is 0 for all 294 products — its only input was the
  `/propose` flow, used once, ever. A mechanism with no input path
  produces no complaints.
- **"Not found" is not "does not exist" — the Tiv Taam feed edition.**
  Recorded here for a week as having no public feed, on the strength of
  one guessed subdomain. It was on the portal this project already used,
  under an obvious username. Where publication is legally required,
  absence of evidence is a reason to look harder.
- **An unescaped `%` in a product name was a live SQL LIKE wildcard.**
  Searching "חלב 3%" built `LIKE '%חלב 3%%'`, whose trailing `%` matched
  any suffix — so "חלב 36" (a chocolate) came back for a milk query, for
  months, silently. The bug returns *more* rows, never an error, so no
  test caught it. Fixed across all four LIKE paths with an escaped term +
  `ESCAPE '\'` (`_like_contains` in `storage.py`); regression test
  `LikeWildcardEscape`. Any new LIKE must go through `_like_contains`.

## 5. Open questions for the user

- **Items 1–3 and waste-(ב) are DONE** (approved 2026-09-04, shipped this
  session). Not open. The success criterion Ishay gave leans on
  **location** ("near me / next door"), which is the real, measured gap —
  no coordinates, no home/work. Measured 2026-09-05: **22.7%** of
  behatsdaa merchants (222/982) have a street address; **93.3%** (916/982)
  at least have a city list. So the honest answer to "where's the nearest
  X" is "here are the cities I know of; I can't rank by distance because
  I don't have your location" — never "no benefit here." If benefits
  become a priority again, closing street-address coverage + a location
  source is the highest-leverage work; see `docs/BENEFITS.md` and
  `docs/benefits_seam_ground_truth_round3.md`.
- **One small open question I asked Ishay:** whether frozen (קפואים)
  should join the *cross-chain* bulk-hoard list. It's already covered at
  the contextual hand-off level; I lean against the cross-chain add
  (freezer space is finite, unlike a diaper closet). Awaiting his call.
- **The SQLite DB is the household's only copy of `price_history`
  (~23k+ daily rows, irreplaceable) and it is gitignored, so GitHub's
  backup does not cover it — the earlier note here that this was
  "confirmed present and running" was wrong, corrected 2026-09-05.**
  Arthur (usage-audit) caught it from journalctl: 44 straight failures
  that day, zero successes. Root cause, reproduced under the real
  systemd sandbox: `rclone` lives at `~/bin/rclone`, and systemd
  `--user`'s default PATH doesn't include `~/bin` — the backup had
  likely **never once succeeded via the timer**, only interactive-shell
  checks (which do have `~/bin` on PATH) looked healthy. This is exactly
  the earlier mistake: "the heartbeat shows it ran" conflated *the
  script ran* with *the backup inside it succeeded* — two different
  facts the heartbeat never actually distinguished. **Fixed and verified
  against the live unit** (full path, real stderr no longer swallowed,
  `db_backup_failing_since` + a watchdog check added so a future
  regression can't go silent the same way). **Verified twice more since:
  20+ consecutive automatic timer runs today, zero failures, and
  `rclone md5sum` on the live Drive file matches the local DB's own
  `md5sum` exactly** — the strongest check short of an actual restore.
  **Still genuinely untested: restoring the Drive copy onto a fresh
  machine and opening it** — content-identical is not the same claim as
  restorable, even if it's now very strong evidence toward it. Separately,
  lower stakes: an **isolated
  `gdrive-grocery:` rclone remote** is still deferred by Ishay
  ("בהזדמנות אחרת") — the shared `gdrive:` remote is what's in use, and
  now genuinely working.
- **behatsdaa live data still needs a login, but the route narrowed:**
  the block is TLS-fingerprint, `curl` passes it, and reading the API
  needs only a fresh 30-minute JWT (§3). Not worth doing until live data
  (balances, voucher expiry) is actually needed; the catalogue is done.
- **Liran's clubs (הר"י, Leumi Bonus)** — need her credentials/consent,
  not Ishay's. הר"י catalogue is behind her member login; Leumi Bonus has
  account state a public catalogue cannot give. Both are the account
  layer, the only place real per-spend money sits.
- **Benefits harvest — three inputs still needed if it goes further.**
  Ownership and build authorization are both settled with verbatim quotes
  and dates (`GOALS.md` under החלטות שהתקבלו) — **do not ask again.**
  What's actually open:
  1. **The eligibility file** — which clubs you're actually in. ClubHub
     covers 100+; you're in roughly 6, so declaring them by hand removes
     ~95% of the data. Not derivable from the budget xlsx — הייטקזון and
     הר"י appear in no budget file.
  2. **`holder`** — הר"י and לאומי בונוס are Liran's; you cannot redeem
     them. A benefit with no holder field is reported to the wrong
     person. (Ishay 2026-09-04: shared household purse — "מה ששלי שלה",
     everything goes to the joint household — so `holder` is about *who
     can physically redeem*, not whose money it is.)

  The success criterion is now settled (Ishay 2026-09-04): know in real
  time, at a moment of intent (a place/category/store), where a discount
  applies here or next door — relevant-only, with periodic
  capture-vs-leftover reflection to optimise loadable cards; failure is
  symmetric (missed discount **or** spam). The binding gap is location,
  not catalogue. Full status (login, catalog, Drive backup, what Miri can
  already read) is in `docs/BENEFITS.md`, not duplicated here.
- **Which Victory branch do they actually shop at?** Prices are per
  branch; Ramat Gan (קניון איילון, id 2447) is pinned as a guess and
  there are four Tel Aviv stores.
- **What TivCoins balance does the app show?** To reconcile against the
  computed 3% accrual.
- **Waste reporting — layer (א) free text + layer (ב) one targeted
  question are BUILT** (`waste.py`, wired into the cart hand-off in
  `telegram_bot.py`; `TargetedQuestion` test). What's still open is only
  live tuning: the picker falls back to most-frequently-bought because
  this household's stock table sorts everything into dry departments, so
  no item reads as perishable — the first real reports will show whether
  the fallback asks about the right things.
- **Coffee-cart directory (coffeetrail.co.il) is harvested and the seam
  is built, but not yet wired on Miri's side** — same state benefits was
  in right after its first harvest. `coffee-catalog`/`coffee-nearby`/
  `coffee-terms`/`coffee-by-term` all work today via the CLI; someone
  needs to add them to Miri's routing before a household member can
  actually ask "עגלת קפה קרובה" through her. `docs/COFFEETRAIL.md`.

## 6. Things that will bite a new session

Store-adapter traps: [`docs/ADDING_A_STORE.md`](./docs/ADDING_A_STORE.md).
Login/anti-bot traps across every site touched so far (geo-block,
rate-based WAF, reCAPTCHA, Incapsula fingerprinting), what solved each,
and the legitimate-access-vs-evasion line:
[`docs/SITE_ACCESS_PLAYBOOK.md`](./docs/SITE_ACCESS_PLAYBOOK.md). The
three from `ADDING_A_STORE.md` that cost the most time:

- **A geo-block returns HTTP 200**, so it looks like broken selectors.
- **"Forbidden" can mean "you forgot a parameter"** — Self-Point's
  products endpoint wants an Elasticsearch-shaped `filters` argument and
  needs no login at all.
- **`xdotool` is not installed here.** It once produced a confident,
  wrong "zero windows on the display" diagnosis. Use `xwininfo` or a real
  screenshot.
