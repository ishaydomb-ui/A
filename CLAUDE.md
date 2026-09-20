# Instructions for Claude Code sessions working in this repository

## `apps/bot` is off-limits by default

`apps/bot` is a Telegram bot that talks autonomously to Liran Kor (a
clinical reviewer on this project) using a Claude API call per message. It
is not just another app in this monorepo: Ishay (the repo owner) has
explicitly asked that Claude Code sessions **not modify, redeploy, or
change the behaviour of `apps/bot` unless he asks for that specific change
in that specific request.**

This means, concretely:

- Do not edit any file under `apps/bot/` — including its system prompt in
  `apps/bot/src/claude.ts`, its context-file list in `apps/bot/src/config.ts`,
  or its Docker/compose wiring — as a side effect of other work, a
  refactor, a lint/type fix, or your own initiative, even if you notice
  something there that looks wrong or improvable.
- Do not run deploy/redeploy commands for the `bot` service.
- If a task genuinely requires touching `apps/bot` (Ishay asks for a bug
  fix, a new capability, a prompt change), that is fine — but treat it as
  a deliberate, separate, explicitly-requested change, not something to
  fold into unrelated work.
- Everything else in the repo (`apps/api`, `apps/web`, `packages/shared`,
  `docs`, `ops`, compose files outside the `bot` service block) is normal
  working territory — this restriction is scoped to the bot only.

This is a soft, honor-system guardrail (a written instruction, not a
technical access restriction) — see `docs/telegram-bot.md` for what the bot
actually does and why this boundary exists.
