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
especially applies to anything about the live supermarket sites: we
don't have a verified login or verified selectors yet (see Known open
issues), so claims about what works there are guesses until proven
otherwise by an actual run.

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

## Known open issues (as of 2026-08-28)

Don't re-derive these from scratch — they're already known:

1. **No headless login exists.** The supermarket sites require a
   one-time *interactive* device/browser login (including any OTP) —
   there is no way to authenticate headlessly with just a
   username/password. Automation needs a real, already-authenticated
   browser session (see `scripts/login_helper.py` and the README
   section "שלב חד-פעמי 2"). If a lower-friction way to get that session
   onto the server still doesn't exist when you pick this up, a
   noVNC-based remote desktop on the server (reachable from a phone
   browser) is the fallback option to build — it has not been built yet.
2. **Selectors are unverified guesses.** The CSS selectors and URLs in
   `grocery_bot/adapters/shufersal.py` were written without access to
   the real site. Treat them as *wrong until proven otherwise* by a live
   run with `PLAYWRIGHT_HEADLESS=false`, not as "probably fine." Fixing
   them against the real site is expected, planned work — not a sign
   something else broke.
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
