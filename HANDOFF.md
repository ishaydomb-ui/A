# HANDOFF — current state of grocery-automation

Living document. Rewritten on every **עוגן**, not appended to: a handover
note that grows into a diary stops being read. History lives in git and
in the progress log in [`GOALS.md`](./GOALS.md); this file answers one
question only — *if someone picked this up right now, what would they
need to know?*

**Last anchored:** 2026-09-02 17:02 (host time, CEST)
**Conversation id:** `eb6175a8-1890-4712-98a2-cd9a24f82ed2`
**Session:** https://claude.ai/code/session_01BR6ULKQXHnkwAme1Hk4z9G
**Branch:** `claude/online-grocery-automation-b7pq4g`

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
- **Victory account login.** Needs the same manual noVNC flow as Tiv Taam
  (checkbox reCAPTCHA). The user could not reach
  `http://localhost:6080/vnc.html` from the phone — the stack is running
  and serving locally, so it is the SSH port-forward of 6080. **Victory
  price comparison does not depend on this** and already works; an
  account would add only order history and cart filling.

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

## 4c. Two traps this session kept falling into

Both cost the user real time, and both are the same shape: **a check that
returns the same result whether or not the thing is true is not
evidence.**

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

## 5. Open questions for the user

- **Did you make this project the owner of a "benefits harvesting"
  project?** The `portfolio-strategy` session (Rob) sent a full design
  brief on 2026-09-02 17:1x — `plan`/`check`/`quote` commands, a
  merchant-alias table, exit code 3 for expired wallets — opening with
  "the benefits harvest Ishay assigned to you as owner", and pointing at
  `~/portfolio-strategy/BENEFITS-DESIGN.md`.
  **Nothing was started, and nothing here records such an assignment.**
  Checked before replying: `ClubHub`, `בהצדעה`, `Terminal X` and `שילב`
  appear in no file in this repo, in no instruction file, and in none of
  this project's transcripts under
  `~/.claude/projects/-home-codex-grocery-automation/` — only in this
  session's own log, which is the incoming message itself. `benefits.py`
  here is TivCoins and the ₪700 Tiv Taam card, nothing wider.
  Per SESSION-COMMON, an ownership assignment comes from you through this
  project's session, not via a peer — so this is waiting on one line from
  you either way. A verbatim quote with a date is enough and it will not
  be asked again.
  branch; Ramat Gan (קניון איילון, id 2447) is pinned as a guess and
  there are four Tel Aviv stores.
- **What TivCoins balance does the app show?** To reconcile against the
  computed 3% accrual.
- **Waste reporting** — design agreed (free text anytime; one targeted
  question at the end of a hand-off; never a checklist), not yet built.
- ~~Miri does not call most of this CLI~~ **Closed 2026-09-02, and the
  claim was wrong to begin with.** `docs/MIRI_INTEGRATION.md` is the
  contract; all eleven commands are wired and reachable from Miri,
  verified by reading `familyos/actions/groceries.py`, `bot/intent.py`
  and `bot/telegram_bot.py`. The real gap was only `price`/`deals` being
  stuck behind this bot's Telegram token, fixed here and wired there
  (`familyos@9ac9538`).
  **Worth keeping, because it will happen again:** the wrong claim came
  from reading §4a of *this* file — a note here about the other project —
  instead of that project's code. Per SESSION-COMMON, facts about another
  project are not ours to assert; a note about someone else's system is
  evidence of what was true when it was written, and nothing more.

## 6. Things that will bite a new session

Full list in [`docs/ADDING_A_STORE.md`](./docs/ADDING_A_STORE.md). The
three that cost the most time:

- **A geo-block returns HTTP 200**, so it looks like broken selectors.
- **"Forbidden" can mean "you forgot a parameter"** — Self-Point's
  products endpoint wants an Elasticsearch-shaped `filters` argument and
  needs no login at all.
- **`xdotool` is not installed here.** It once produced a confident,
  wrong "zero windows on the display" diagnosis. Use `xwininfo` or a real
  screenshot.
