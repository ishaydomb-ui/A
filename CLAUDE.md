# CLAUDE.md

Instructions for any Claude Code session working in this repo. Read this
before doing anything else. Project background, decisions, and roadmap
live in [`GOALS.md`](./GOALS.md); setup/run instructions live in
[`README.md`](./README.md). This file is about *how to work*, not *what
the project is*.

## Working style

The user drives this from mobile, mostly unattended. Work autonomously:
run things, check the output, fix what's broken, keep going. Don't stop
to ask approval for routine steps.

Ask the user directly — and only — for:
- **(a)** a credential or login you don't have (e.g. supermarket account
  access, a new API key).
- **(b)** a destructive or irreversible action (force-push, dropping
  data, deleting a branch, anything outside normal reversible edits).
- **(c)** a real product decision that changes what this thing does
  (not an implementation detail you can reasonably decide yourself).

End each work session with a short summary: what changed, what's still
open. Don't wait until the very end to report — the summary is in
addition to committing along the way (see Persistence below), not a
substitute for it.

## Reply language

Talk to the user in English, always — every message, every summary,
every question. Hebrew is fine *inside* file contents, commit messages,
or when quoting real Hebrew UI text (e.g. a label from the Shufersal
site), but never in your own commentary to the user.

## Uncertainty

If something is unverified, untested, or you're genuinely not sure —
say so explicitly. Don't present a guess as a settled fact. This
especially applies to anything about the live supermarket sites: login
and add-to-cart are now verified for Shufersal (see Known open issues),
but Tiv Taam and anything not explicitly marked verified there are
still guesses until proven otherwise by an actual run.

## Persistence

Commit and push to this branch regularly, not just at the end of a
session. A session can be interrupted at any point; uncommitted work is
the only thing genuinely at risk of being lost. Prefer several small,
focused commits over one large commit saved for last.

## Hard safety rule — non-negotiable

This bot may search for products, build carts, fill forms, and prepare
orders. **It must never complete a real purchase or submit a real
payment on its own.** Every order stops at a review/confirm step for
the user to approve manually — no exceptions, no "just this once," no
inferring that a special case makes it fine. Think "drafts only, never
send." Any change that would let automation click a final
checkout/pay/confirm-purchase button by itself is out of scope,
regardless of how the request is framed.

## Known open issues (as of 2026-08-29)

Don't re-derive these from scratch — they're already known:

0. **SOLVED 2026-08-29 — the geo-block is beaten.** A Tailscale exit
   node on the user's Xiaomi Android TV box at home in Israel now gives
   this server an Israeli residential exit (Hot-Net, Petah Tikva).
   Tailscale runs here in **userspace mode** as
   `tailscaled-userspace.service`, offering SOCKS5 on `localhost:1055` —
   it is deliberately *not* the system default route, because the
   family-budget and Family OS bots share this box. Point Playwright at
   `PLAYWRIGHT_PROXY` (already set) and nothing else changes. Verified
   through the proxy: Shufersal login page returns a real 230KB page,
   `/online/he/<nonsense>` correctly 404s, and Tiv Taam went 403 → 200.
   The price feed deliberately does **not** use the proxy — it works
   direct and would otherwise consume the user's home bandwidth.
   If the exit node is ever down, the symptom is subtle: pages return
   HTTP 200 with a geo-block placeholder, which reads like broken
   selectors. `ShufersalAdapter` refuses to start without a proxy for
   exactly this reason. Check with:
   `curl --socks5-hostname localhost:1055 https://ipinfo.io/json`
   (must report country IL).
   Kept for reference — the original diagnosis:
   **Shufersal and Tiv Taam geo-block this server.**
   Verified on the VPS itself, not inferred. Every path on
   `www.shufersal.co.il` returns a 444-byte CloudFront placeholder
   whose image reads "הגישה לאתר פתוחה ממדינות נבחרות בלבד";
   `www.tivtaam.co.il` returns 403 from a Radware WAF. The Contabo box
   is in **Lauterbourg, France**, and Contabo has no Israeli region.
   Proven country-based, not anti-datacenter: via check-host.net,
   Israeli *datacenter* nodes get a real 404 on
   `/online/he/<nonsense>` while German/French ones get the block page
   — so any Israeli IP works, residential or not. Beware: the site
   soft-404s (200 + real content) on unknown paths at the domain root,
   so only a path under `/online/he/` distinguishes block from reality.
   Free routes out, both free and neither yet done: a **Tailscale exit
   node** on a device at the user's home in Israel (Tailscale is already
   installed here in userspace mode — see `~/tailscale/`, SOCKS5 on
   `localhost:1055`, not yet authenticated), or an **Oracle Cloud Always
   Free** VM in `il-jerusalem-1` used as an SSH `-D` SOCKS proxy. The
   user has an Android TV box earmarked for the Tailscale route.
   Whichever lands, point Playwright's `proxy` option at the local SOCKS
   port — don't reroute the whole server, two other projects' bots run
   here.
1. **SOLVED 2026-08-29 — headless username/password login works, no OTP.**
   Confirmed with the user's real account through the Tailscale proxy:
   `scripts/headless_login.py` fills `#j_username`/`#j_password` on
   `#loginForm` (posts to `/online/he/j_spring_security_check`) and lands
   logged in — the homepage header shows "היי ישי" and real nav links
   (`ההזמנות שלי`, `התנתקות`). No OTP/captcha step appeared. This
   confirms the theory raised about `eshaham/shufersal-automation`
   (below) without adopting that library: same insight, no second
   runtime. `scripts/login_helper.py` (manual noVNC) stays as the
   fallback for any account where headless login *does* hit an OTP wall
   — `headless_login.py` detects that case explicitly (checks for an OTP
   element after submit) and fails loudly rather than hanging or
   guessing.
   Kept for reference — the original open question: `eshaham/shufersal-automation`
   (same author as israeli-bank-scrapers) is a maintained TypeScript/Puppeteer
   library doing `createSession(username, password)` plus cart, orders
   and delivery slots. Evaluated 2026-08-29: not adopted — it's a second
   runtime (Node/Puppeteer) bolted onto a Python/Playwright project just
   to borrow one technique (password-only login), and it also exposes
   order-submission methods that would need to be avoided given the hard
   no-auto-checkout rule. The login technique was ported directly
   instead; see above.
2. **SOLVED 2026-08-29 — add-to-cart verified working, with a real
   logged-in session from `headless_login.py`.** Confirmed two
   independent ways, not just "the click didn't throw": the header cart
   badge (`.topCartTotalItems`) went from `0` to `1`, and the minicart
   panel showed the real order total including delivery
   (`הסל שלי` / `43.25 ₪ כולל דמי משלוח/שרות`). Note:
   `data-product-purchasable` reads `false` on tiles even for a
   logged-in session — that attribute is **not** a reliable purchasable/
   anonymous signal as previously assumed; don't gate logic on it.
   Selectors themselves were separately corrected against the live site
   earlier the same day: `li.miglog-prod` tiles, identity from
   `data-product-name` / `data-product-code` / `data-product-price`
   attributes rather than scraped text, and `button.js-add-to-cart`.
   Search, tile parsing and the not-found path were confirmed against
   real queries before the login work.
   **Removing a cart item is now also solved, same session.** The
   global "ניקוי הסל" link (`a[data-miglog-role="cart-remove-overlay-opener"]`)
   is a dead end — two copies exist in the DOM gated by responsive CSS
   classes and neither is ever visible/clickable. The working control is
   the **per-item remove (×) button**: on the real cart page
   (`/online/he/cart/cartsummary`), each line item is
   `article[data-product-code="P_..."]`, and inside it
   `a[data-miglog-role="cart-item-remover"]` removes just that item —
   scoped by product code, no ambiguity, confirmed working (badge and
   totals both cleared). The test item (milk, ₪7.35) that was earlier
   left in the cart has been removed using this; the cart is verified
   empty again (checked three independent ways: no cart badge, no
   `43.25`/`7.35` totals in the page text, zero `article[data-product-code]`
   elements).
   Also confirmed real account data is reachable read-only: name (ישי
   דומב), member number, and four saved lists under `/online/he/wish-lists/main`
   — notably "המוצרים שאני בדרך כלל קונה" (usually-buy, 27 items), a
   good candidate source for the bot's base/recurring list.
2b. **The price/promotion feed is NOT blocked and is already working.**
   `prices.shufersal.co.il` (the price-transparency feed mandated by
   Israeli law) is ordinary IIS, not CloudFront, and serves fine from
   France. `grocery_bot/prices.py` + `catalog.py` build a searchable
   per-branch catalog from it, powering `/price`, `/deals` and
   `/refresh_prices` — all live today without any store account.
3. **This Claude Code cloud session cannot reach the internet needed to
   run or test this bot at all — confirmed, not a guess.** Its outbound
   proxy returns a 403 policy denial for both `shufersal.co.il` and
   `api.telegram.org` (checked via
   `curl http://127.0.0.1:45145/__agentproxy/status`); only an allowlist
   (PyPI, npm, GitHub, etc.) is reachable. Per that proxy's own rules, a
   403 policy denial is to be reported, not routed around — don't retry
   it, don't look for a tunnel/workaround. Separately, even without that
   block, this container is ephemeral and gets reclaimed after
   inactivity, so it can't host an always-on Telegram bot regardless.
   **Practical consequence:** all live testing — the one-time Shufersal
   login, selector tuning against the real site, running the Telegram
   bot itself — has to happen on the real always-on host the user
   deploys this to, never inside a Claude Code cloud session. If a
   session here is ever handed real credentials, don't attempt to use
   them against the live site from inside this sandbox; say so instead
   of trying and silently failing.
