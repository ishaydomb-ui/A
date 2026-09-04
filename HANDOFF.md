# HANDOFF — current state of grocery-automation

Living document. Rewritten on every **עוגן**, not appended to: a handover
note that grows into a diary stops being read. History lives in git and
in the progress log in [`GOALS.md`](./GOALS.md); this file answers one
question only — *if someone picked this up right now, what would they
need to know?*

**Last anchored:** 2026-09-04 22:15 (host time, CEST)
**Conversation id:** `eb6175a8-1890-4712-98a2-cd9a24f82ed2`
**Session:** https://claude.ai/code/session_01BR6ULKQXHnkwAme1Hk4z9G
**Branch:** `claude/online-grocery-automation-b7pq4g`
**Status is in `git log`, not hand-typed here** (per the cross-project
D.3 rule, 2026-09-04). This file holds decisions and open items only.

**Since the last anchor (see `git log` for detail):** Ishay approved and
this session shipped items 1–3 of the benefits/seam plan plus waste-(ב):
- **Cross-chain price** — `price-compare` (canonical "cheapest" across all
  chains; `price` re-scoped to Shufersal-shelf so the two never collide).
- **Waste layer (ב)** — one targeted question at the cart hand-off end
  (`waste.pick_targeted`), buttons record a fraction; layer (א) free-text
  already existed.
- **Merchant disambiguation** — `benefits-remember` term→merchant memory,
  reusing `remember_choice`; `benefits-catalog` resolves to it.
- **Normalization** — case + apostrophe/geresh folded before matching, so
  a form-miss no longer reads as "doesn't exist."
- **The `%` bug (most important)** — a literal `%` in a query ("חלב 3%")
  was a SQL LIKE wildcard, silently matching "חלב 36" (chocolate) for
  months. Escaped now across all four LIKE paths. See §4c.
- Decisions recorded: eligibility (6 clubs, `data/benefits/eligibility.yaml`),
  no `holder` (household-level), success criterion, TivCoins 3.2.

Earlier this session: MAX + behatsdaa catalogues live to Miri with
per-club freshness; Tiv Taam memory seeded (292); the userns reboot fix
(`d8d1132`, Ishay); SQLite backed up off-box; Arthur's audit block done.

---

## 1. Where things stand

**Running in production on the Contabo VPS**, as user systemd units:

| Unit | What it does |
|---|---|
| `grocery-bot.service` | The Telegram bot (active) |
| `grocery-prices.timer` | Refreshes the Shufersal price feed, a few times a day |
| `grocery-backup.timer` | Pushes commits to GitHub every 30 min |
| `grocery-doctor.timer` | Hourly backup health check (new, 2026-09-01) |
| `grocery-alert@.service` | `OnFailure=` notifier shared by all of the above |

**Store access:**
- **Shufersal** — logged in, cart add/remove verified, public price feed
  working (5,821 products). Order history readable.
- **Tiv Taam** — logged in (session survives with no browser running).
  Account, orders, coupons, smart list all readable. Cart **read and
  cleared** verified against the real account 2026-09-02: `cart_summary`
  returns the panel's own total including delivery, `clear_cart` removes
  line by line and verifies on line elements. **Its search is the weak
  link** — see §3.
- **Victory** — prices readable with **no login at all**; account login
  still outstanding (see §3).

**Everything needs the Israeli exit** (`PLAYWRIGHT_PROXY`, Tailscale
SOCKS5 on `localhost:1055`). Without it the chains return block pages
with HTTP 200, which reads like broken selectors.

**Benefits harvest (behatsdaa) — a separate subsystem, owned by this
project.** Ownership and build authorization are both settled with
verbatim quotes and dates (`GOALS.md`) — do not re-ask. Status, in full,
lives in `docs/BENEFITS.md`; the short version: the store catalog (982
stores, manually tagged) was rescued and is backed up to
`gdrive:גורדון — קטלוג הטבות/`, and Miri can already read it read-only
via `benefits-catalog`/`benefits-branches` in the CLI (built
2026-09-03, documented in `docs/MIRI_INTEGRATION.md` — familyos-side
wiring is not done, that's the user's next step). **No live harvesting
has happened yet** — our own behatsdaa login is blocked by an Incapsula
fingerprint check, not a timing problem (see §3). Three product inputs
are still open before any harvest step; see §5.

## 2. In flight

**One thing is deliberately half-done, and the user asked for it to be
remembered: step 2 of the Tiv Taam reliability plan — resolve product
names against our own catalog instead of the live dropdown.**

Steps 1 and 3 are built (remember a clean resolution; retry an empty
search). Step 2 is the real fix and was not started, on purpose: it is a
proper piece of work and the session was ending.

*What it is:* the name → product step should not touch the live site at
all. The barcode is already in `store_prices` (743 Tiv Taam rows) and the
catalog is keyed by EAN across eight chains, so `compare.py`'s matching
already does most of this. Resolve name → barcode locally, then use the
Self-Point API's `filters[must][term][localBarcode]` — which *is*
honoured — to get the Tiv Taam product id, and let the browser do only
the add.

*Why it matters:* the autocomplete returned 4, then 0, then 5, then 1
candidate for "קוטג" in one afternoon. A 0 is reported as `not_found` for
a weekly staple, and a 1 is added and now *remembered* — so a half-loaded
dropdown can pin the wrong product. Step 1 accepts that risk knowingly
(see the comment in `orchestrator._add_one`); step 2 removes it.

*Do not build it on API name search.* `filters[must][match][name]`
returns HTTP 200 with products but is ignored — three different terms
return byte-identical results with `total=10000`. Written up in
`docs/ADDING_A_STORE.md` §7.

The last completed pieces, newest first:

- **Benefits catalog exposed to Miri, read-only** — `benefits-catalog`
  and `benefits-branches` in the CLI, backed by
  `grocery_bot/benefits_catalog.py`. Reads flat CSVs under
  `data/benefits/` (gitignored, not the sqlite DB). 16 tests against a
  temp data dir. Same token-free contract as every other Miri command.
- **Catalog backed up to Drive**, verified against the originals —
  `catalog_tagged.csv` + `catalog_full.csv` in
  `gdrive:גורדון — קטלוג הטבות/`. Now the catalog's only durable copy;
  the Strategist's `portfolio-strategy/lab/` has no backup of its own.
- **`docs/SITE_ACCESS_PLAYBOOK.md`** — a cross-project reference on every
  login/anti-bot barrier hit so far (geo-block, rate-based WAF,
  reCAPTCHA, Incapsula fingerprinting), what solved each, and the policy
  line between a legitimate login and anti-bot evasion. Shared with
  Arthur to route to the other bots, recommendation-only.
- Eight chains priced by barcode; five new ones from the transparency
  portal. `whereto` answers "where should the whole shop go this week".
- `nudge` — the six-day message: reminder, free-text reply, card
  question, ten deals and a link to twenty more.
- `threshold` — the ₪599 gift and one-short multi-buys, checked before
  the hand-off to pay.
- `habits` — one consumption rate per product across chains.
- `waste`, `shelflife`, `pricecontrol`, `smartlist`, `hotdeals`.
- `adapters/tivtaam.py` — Tiv Taam cart filling. ENABLED_STORES has both
  chains. No checkout method exists and a test enforces that.
  **This line used to claim "verified against the real account (search,
  add, verify, clear)". It was overstated** — there was no `clear` method
  in the file at all, and the add path reported successful adds as
  failures. Both were found and fixed on 2026-09-02 by actually running
  it; `clear_cart` is new. Left visible as a reminder that "verified"
  in this file has to mean a command was run, not that code was read.
- Backup monitoring — heartbeat, freshness doctor, `OnFailure=` alerts,
  all-clear on recovery. Verified end to end.
- `shelflife.py` — when cupboard staples are actually due again.
- `pricecontrol.py` — prefer the price-controlled staple where one exists.
- `ask.py` — one-off price questions across all three chains.
- `multibuy.py` — whether "הוסף וחסוך" is genuinely worth taking.
- `compare.py` + `selfpoint.py` — cross-chain comparison on EAN barcode.

## 3. Blocked, and on what

- **behatsdaa login is blocked by an Incapsula fingerprint check, not
  timing.** Diagnosed 2026-09-03 by probing the block directly (network +
  fingerprint capture, no evasion): the homepage returns 200 and earns an
  `incap_ses` cookie, so the IP is not rate-blocked — waiting does
  nothing. `navigator.webdriver === true` and a self-contradictory
  fingerprint (iPhone UA on `platform: Linux x86_64`, 0 plugins, 0 touch
  points) get the automation caught; Incapsula returns 403 on
  `configuration.json`, the SPA throws "שגיאה כללית", and the OTP request
  is never actually fired — the first script's "OTP_SENT" was false,
  read off a click rather than a verified result (now fixed to check the
  code field actually appears). **In-policy fix: a human/real-browser
  login**, same noVNC pattern as the Self-Point chains — blocked on the
  same phone-access problem as Victory below. Automated evasion (hiding
  `webdriver`, spoofing the fingerprint) is out of policy; the safety
  classifier blocked even a plain headed-browser test framed as "just a
  real browser." Full writeup: `docs/BENEFITS.md`.
- **Tiv Taam's search is unreliable, and that is now the weakest part of
  the chain.** Not blocked on anything external — it is step 2 above.
  Symptoms seen live on 2026-09-02, same account, same afternoon: the
  same query returned 4, then 0, then 5, then 1 candidate; some rows
  carry no add button at all (a real "out of stock", not a bug). Cart
  reading, adding and clearing are all solid now; the search under them
  is not.

- **Victory storefront is Cloudflare-blocked** from this exit as of
  2026-09-01 12:42 — `victoryonline.co.il` returns 403 while Tiv Taam and
  `api.self-point.com` stay fine. Likely provoked by my own automated
  loads. **Victory prices still work**, because they come from the API.
  Credentials are stored; retry the login later or from another exit.
  **Rate-based WAF blocks are per-destination-site, not per-source-IP.**
  Confirmed 2026-09-02: the `portfolio-strategy` session hit
  `behatsdaa.org.il` ~10× through the *shared* exit node and earned a 403
  there, while at that same moment Shufersal (`/online/he/login`, 200,
  230KB) and Tiv Taam (200) stayed clean through the same IP. So another
  project's load on a different domain does not collaterally block the
  stores — but hammering one store *does* block that store (this is the
  Victory case above, same-site). The exit is shared by three projects;
  pace store loads accordingly.
- **Victory account login, and now behatsdaa's too, both need the same
  fix: noVNC reachable from the phone.** Victory needs the manual noVNC
  flow (checkbox reCAPTCHA) like Tiv Taam; behatsdaa needs a real/headed
  browser for the same reason (see above). The user could not reach
  `http://localhost:6080/vnc.html` from the phone — the stack runs and
  serves locally, so it is the SSH port-forward of 6080. Solving this
  once unblocks two sites. **Victory price comparison does not depend on
  this** and already works; an account would add only order history and
  cart filling.

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
  **location** ("near me / next door"), which is the real gap — no
  coordinates, no home/work, 77% of merchants with no address. If benefits
  become a priority again, closing branch-address coverage + a location
  source is the highest-leverage work; see `docs/BENEFITS.md`.
- **One small open question I asked Ishay:** whether frozen (קפואים)
  should join the *cross-chain* bulk-hoard list. It's already covered at
  the contextual hand-off level; I lean against the cross-chain add
  (freezer space is finite, unlike a diaper closet). Awaiting his call.
- **Isolated `gdrive-grocery:` rclone remote** — Ishay deferred it
  ("בהזדמנות אחרת"). The DB is backed up meanwhile via the shared
  `gdrive:`; the isolated token is a nicety, not a risk.
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
