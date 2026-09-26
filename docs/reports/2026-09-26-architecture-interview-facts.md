# Gordon — facts for Architecture's product interview (2026-09-26)

Read-only answer to בוס (boss-42). Every line has a source; "unknown"
means no evidence was found, not "no". Numbers from the live DB were read
with a read-only connection on 2026-09-26.

## 1. Mini Apps / web views

**None.** No `WebAppInfo`/web_app code in `grocery_bot/` (grep, 26.09).
A grocery Mini App was discussed and **deferred on 2026-08-30 to ~mid-Oct
2026** (memory `miniapp_revisit.md`): if built, it would be one route in
the existing `~/family-tools` hub (Vercel, `verifyTelegramUser(initData)`),
analysis screens only (price-history, cross-chain basket, spend) — never
a list/checklist UI that competes with the store apps. Blocker: data is
SQLite on the VPS; Vercel cannot reach it → would need a Sheet snapshot.
The only "web" surface today is a Telegram URL button ("עבור לתשלום")
that opens the chain's own cart page (HANDOFF §2j, Phase 2b).

## 2. User-facing flows and pain points

Main flows (HANDOFF §1, §2j):
- **Standing cart**: `/done` ("סיימתי") records a shop and refills both
  chains' carts from the `everything` list + deals (~2.5 h); Ishay only
  deletes. Chosen by Ishay 2026-09-07.
- **Ad-hoc list**: Liran/Ishay add items in free Hebrew (directly, or via
  Miri's `add-item` CLI seam); `watch_list` job adds them to the cart.
- **vNext assisted flow** (`/plan` → review → compare → confirm), live
  since 2026-09-21; first real run by Ishay 21.09 (run 16).
- Prices/deals: `/price`, `/deals`, `/alldeals`, `/basket` (list priced
  at every chain), `/stockup`, `/cheaper`, `/digest`.
- Never checks out: the user pays in the store app (CLAUDE.md hard rule).

Measured (DB `cart_runs`, `run_items`, `cart_failures`, 17.09–24.09):
- 32 cart runs, **0 crashed; 15 of 32 ended `completed_with_exceptions`
  or `_with_unverified`**.
- Item outcomes: Tiv Taam 734 attempts → 295 verified, 96 ambiguous,
  50 unverified, 38 failed, 255 skipped (already in cart / guard).
  Shufersal 185 → 50 verified, 15 ambiguous, 2 failed, 118 skipped.

Top 3 failure modes:
1. **Tiv Taam autocomplete add times out** — 29× `Timeout 45000ms`,
   27 Playwright `TimeoutError`s in the bot journal since 21.09;
   last batch 24.09 18:37 UTC (`cart_failures` 119–123). The dropdown is
   the weak link; the fix (Self-Point API `localBarcode` filter) is known
   but not built (HANDOFF §3).
2. **Ambiguity** — 111 lines `unresolved_ambiguity` (Tiv Taam 96,
   Shufersal 15); 0 human-confirmed products when vNext started, so every
   choice was inference (HANDOFF §2j: shower gel for "יוגורט Pro וניל").
3. **Exit-node / network** — Israeli exit is Ishay's iPhone (TV box
   offline since ~01.09): 1.7–8.1 s per page; `ERR_SOCKS_CONNECTION_FAILED`
   killed all 9 Shufersal removals on 22.09 (HANDOFF §2, §3).
Plus a process one: **concurrent use of the Shufersal account**
(Gordon + Miri SMS reset + Ishay by hand, 22.09) made lines vanish —
cause stated as likely, not proven (HANDOFF §2).

## 3. Auth inventory

| Site | Method | Human step | Last evidence |
|---|---|---|---|
| Telegram (@ClaudeGroceriesBot) | own token, `GROCERY_TELEGRAM_BOT_TOKEN` in `.env` | none | startup 25.09 23:00 CEST, commit `8f5bfc2` |
| Shufersal | username/password, **from Bitwarden** (`bw.env` → vault), headless form login; no OTP seen | none so far | verified adds 24.09 15:12 UTC |
| Tiv Taam | GordonChrome over CDP (`100.64.121.81:9224`, liran-aba-pc), logged in once 21.09 from vault item "טיב טעם"; no captcha that day | one-time login in the persistent profile; re-login when it expires (frequency unknown yet) | verified adds 24.09 18:37 UTC |
| Tiv Taam order API | Self-Point API, same credentials | none | 20-order backfill 20.09 (CLAUDE.md §3) |
| Price feeds (Shufersal + publishedprices portal, 7 chains) | public/portal usernames, no secret | none | daily timer |
| behatsdaa | human login on noVNC | **every use**: JWT is ~30 min; session expired | last login 09.09 (HANDOFF §3) |
| Victory | not done — needs noVNC from phone | yes | never |
| Google Drive (backup) | `rclone` `gdrive:` remote (shared) | none | backup timer; verified md5 05.09 |

Saved-session files `data/sessions/*_storage_state.json` are from 29.08
(Shufersal) and 31.08 (Tiv Taam) — the live path no longer depends on
them (Bitwarden login / CDP profile).

## 6. "Very good deal" — as implemented

There is **no single definition written anywhere except the code**, and
the code has three different bars in use.

**A. What actually goes into the cart automatically:** `dealfill.picks_for`
(`grocery_bot/dealfill.py`, created in `4641035` 06.09, last changed in
`08e8491` 11.09). It is called by the order cycle and by the standing-cart
refill. These picks go into the cart with no approval step; the design
rule is "would he rather delete it than never have seen it".
- Discount = `1 − deal/shelf`. It is measured against the **current
  shelf price only, not price history**.
- Products he has bought before: discount ≥ **25%** (`radar.MIN_DISCOUNT`,
  radar.py:33). Three exclusions:
  - promos at or above the shelf price;
  - names that look perishable;
  - promos that pay only on a second unit.
- On Shufersal, candidates also come only from pantry-type departments.
- Products never bought: discount ≥ **40%**, price ≤ ₪30, at most 4
  (dealfill.py:75-92).
- Cap: 8 deal items per chain (dealfill.py:70). Shufersal and Tiv Taam
  only.

**B. Shown only (`/deals`, digest):** `hotdeals.py`, last changed in
`9d90eb5` 17.09. These are reported and never added to the cart.
- Worth reporting: ≥ **20%** and saving ≥ ₪2, or a stockable item saving
  ≥ ₪12.
- "Exceptional": ≥ 40% and saving ≥ ₪8.
- ≥ 90% off is treated as a feed error.

**C. vNext stock-up:** `vnext_economics.assess`, created in `7267a78`
20.09. This is the **only one that uses history**.
- Reference price is the 90-day median, or the median whenever the shelf
  price is above 1.15× the median.
- Worthwhile needs all of these:
  - real discount ≥ 25%;
  - ≥ 2 units, where units = horizon ÷ cadence × usual quantity, with a
    60-day horizon for shelf-stable items and 7 days for perishables;
  - saving ≥ ₪5;
  - the promo is not "routine" (≤ 1.02× the deal price on ≥ 50% of days);
  - promo trust ≥ 0.8.
- **"מבצע טוב — לאגור N?"** (telegram_vnext_view.py:139) is shown when a
  stock-up is ≥ 4 units or ≥ ₪60. That turns it into a question to
  Ishay, not an automatic add.

**Gap:** the Phase 1.5 report says stock-ups are now decided by history.
That is true only in vNext. The path that fills the cart (A) still uses a
flat 25% off today's shelf price. So a promo that runs half the year
still gets added automatically.

**Cart-edit data: none usable.** The mechanism exists in
`standingcart.py`:
- after each fill, a manifest of what Gordon added is saved;
- `removals()` and `removals_from_order()` compare it with the live cart
  or the real order;
- a monthly report is sent;
- deletions are "reported, never learned from" (Ishay, 07.09).

But `standing_cart_removal_log` does not exist in the DB (checked
26.09). **No deletion has ever been recorded and no report has ever been
sent.** Reasons:
- the 22.09 snapshot was dropped deliberately, because it would have
  logged about 150 phantom removals;
- `mark_shopped` was called outside `/done`, which skipped that path;
- the 17.09 cart runs started after that day's order.

The only proxy is sizes: the refill fills the `everything` list, 146
lines on Tiv Taam (manifest from 24.09), while real orders are 37–48
items (`order_log`: Tiv Taam 17.09, Shufersal 22.09). That points to heavy
trimming, but the size of it is unmeasured.

## 8. Decisions waiting on Ishay (HANDOFF §2g-bis, §2j, §3, §5)

1. Route "מה חסר"/"צריך קניות" to the vNext plan (`nlu.py` change).
2. Withhold cart tools in a turn that ran a price/deals query.
3. Move NLU onto the Python Agent SDK (my lean: yes, as its own work).
4. Which real runs count for the live execution benchmark.
5. 14 stale ad-hoc rows from 16.09 — mark consumed or leave.
6. Share Work `CURRENT.json` Drive file with the service account.
7. Frozen items in the cross-chain bulk list (I lean no).
8. Nationwide benefit geocoding, ~$30 (spend — Tel Aviv only today).
9. Which Victory branch; TivCoins balance shown in the app.
10. Liran's clubs (הר"י, Leumi Bonus) — needs her login and consent.
11. Tiv Taam doubled lines (two salts/oils/silans/granolas) — his call.

## 9. Everything else

- **Built but off / unused:** Agent-SDK conversation backend
  (`GORDON_CONVO_BACKEND=agent`, off; less accurate than the classifier
  on a 30-message test — `docs/reports/2026-09-17-conversation-backend-benchmark.md`);
  benefits + coffee-cart CLI seams ready but **not wired on Miri's side**
  (`docs/MIRI_INTEGRATION.md`); waste reporting built, waiting for real
  reports; `/propose` retired.
- **Half-built:** Markdown→HTML migration paused 17.09 (cost); vNext
  Phase 2b live in assisted mode only; GordonChrome covers Tiv Taam
  only — Shufersal still local browser + exit node.
- **Dependencies on other agents:** Bob (liran-aba-pc: GordonChrome 9224
  and the second exit node 100.64.121.81); Miri (calls Gordon's CLI
  seam `add-item` etc.; owns `grocery-nudge.service`, which runs
  `~/familyos/scripts/grocery_nudge.py` and still loads
  `familyos/secrets.env`); Tailscale exit on Ishay's iPhone.
- **Recurring problems:** Tiv Taam dropdown timeouts; exit-node latency;
  the TV-box exit offline since ~01.09 (needs someone at the box);
  `refill` on `everything` (147–155 lines, ₪1,823 on 22.09) vs a normal
  ~41-item order — lesson: manual rebuild should use `core`/`full`.
- **Docs vs reality gaps found and fixed recently:** Gordon ran on Miri's
  Telegram token 19.09–21.09 (shared EnvironmentFile; fixed, token
  renamed 25.09); "Tiv Taam has no price feed" was wrong for a week;
  the DB backup had likely never succeeded via the timer until 05.09;
  `refresh_all_portal_chains` had no caller.
- **Trap:** calling `mark_shopped` outside `/done` silently disables the
  nightly refill backstop (22.09; refill runs recorded again 24.09).
- **Still untested:** restoring the Drive DB backup on a fresh machine.
