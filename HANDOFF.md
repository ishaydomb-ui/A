# HANDOFF — current state of grocery-automation

Living document. Rewritten on every **עוגן**, not appended to: a handover
note that grows into a diary stops being read. History lives in git and
in the progress log in [`GOALS.md`](./GOALS.md); this file answers one
question only — *if someone picked this up right now, what would they
need to know?*

**Last anchored:** 2026-09-02 11:40 (Asia/Jerusalem host time)
**Conversation id:** `df559a44-e7ec-4e8f-9462-046d0a364d36`
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
  Account, orders, coupons, smart list all readable.
- **Victory** — prices readable with **no login at all**; account login
  still outstanding (see §3).

**Everything needs the Israeli exit** (`PLAYWRIGHT_PROXY`, Tailscale
SOCKS5 on `localhost:1055`). Without it the chains return block pages
with HTTP 200, which reads like broken selectors.

## 2. In flight

Nothing is half-built. The last completed pieces, newest first:

- Eight chains priced by barcode; five new ones from the transparency
  portal. `whereto` answers "where should the whole shop go this week".
- `nudge` — the six-day message: reminder, free-text reply, card
  question, ten deals and a link to twenty more.
- `threshold` — the ₪599 gift and one-short multi-buys, checked before
  the hand-off to pay.
- `habits` — one consumption rate per product across chains.
- `waste`, `shelflife`, `pricecontrol`, `smartlist`, `hotdeals`.
- `adapters/tivtaam.py` — Tiv Taam cart filling, verified against the real
  account (search, add, verify, clear). ENABLED_STORES now has both
  chains. No checkout method exists and a test enforces that.
- Backup monitoring — heartbeat, freshness doctor, `OnFailure=` alerts,
  all-clear on recovery. Verified end to end.
- `shelflife.py` — when cupboard staples are actually due again.
- `pricecontrol.py` — prefer the price-controlled staple where one exists.
- `ask.py` — one-off price questions across all three chains.
- `multibuy.py` — whether "הוסף וחסוך" is genuinely worth taking.
- `compare.py` + `selfpoint.py` — cross-chain comparison on EAN barcode.

## 3. Blocked, and on what

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

- **Which Victory branch do they actually shop at?** Prices are per
  branch; Ramat Gan (קניון איילון, id 2447) is pinned as a guess and
  there are four Tel Aviv stores.
- **What TivCoins balance does the app show?** To reconcile against the
  computed 3% accrual.
- **Waste reporting** — design agreed (free text anytime; one targeted
  question at the end of a hand-off; never a checklist), not yet built.
- **Miri does not call most of this CLI, and the user is specifying
  that side.** `docs/MIRI_INTEGRATION.md` is the contract, written
  2026-09-02 for the user to take to the familyos session. Today Miri
  routes free text to `add-item` and card replies to `confirm-card`;
  `meal-plan`, `recipe`, `recipe-text`, `price` and `deals` all work,
  are token-free, and are called by nobody. Concretely: "תכנן לי תפריט
  שבועי" said to Miri lands as a list item with that name. **Waiting on
  the user / the familyos session — nothing to do here.**

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
