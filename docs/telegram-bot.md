# Telegram bot

A private Telegram bot (`apps/bot`, `@med/bot`) that lets a clinical
reviewer discuss the project directly with Claude, without going through
Ishay. It is optional — nothing else in the stack depends on it — and it is
built and started only when you explicitly opt in.

## What it is, and what it is not

- It runs as its own container, long-polling Telegram (no inbound port, no
  reverse-proxy route needed).
- On every message it makes a fresh call to the Anthropic API, with a system
  prompt built from real project documents — `docs/technical-capabilities.md`,
  `docs/roadmap-proposals.md`, and a few more (see [Context](#context) below).
  It only knows what is in those files.
- **It is a separate Claude conversation from Claude Code.** It cannot read
  this repository beyond the docs it is given, cannot edit files, run
  commands, or deploy anything. It is a knowledgeable discussion partner, not
  an extra set of hands.
- Every exchange is appended to a plain-text log (`/data/conversation-log.md`
  inside the container, on the `bot-data` volume) so Ishay — or a future
  Claude Code session working on this repo — can read back what was
  discussed. There is currently no automatic path from that log into a
  Claude Code session; pulling it in is a manual step (`docker compose cp` or
  `docker compose exec bot cat /data/conversation-log.md`).
- Access is a plain allowlist of Telegram chat ids (`BOT_ALLOWED_CHAT_IDS`).
  Anyone else who finds the bot gets a one-line reply telling them their
  chat id and nothing else — no API call is made, so a stranger cannot run
  up the Anthropic bill.

## Setup

### 1. Create the bot and get a token

Open **[@BotFather](https://t.me/BotFather)** in Telegram (this requires your
own approval — a Telegram account is not something this session can create),
send `/newbot`, and follow the prompts (a display name, then a username
ending in `bot`). BotFather replies with a token that looks like
`123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`. Keep it secret — anyone with
it can operate the bot as you.

### 2. Get an Anthropic API key

Open **[console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys)**
(sign-in and billing on that console are yours to set up — this session has
no access to your Anthropic account), create a key, and copy it. This key is
billed per message the bot answers — a separate cost from your Claude Code
usage.

### 3. Add both secrets to `.env` on the server

```bash
cd /home/codex/medcat
nano .env
```

Add (or edit) these lines:

```bash
TELEGRAM_BOT_TOKEN=<the token from BotFather>
ANTHROPIC_API_KEY=<the key from the Anthropic console>
```

`BOT_ALLOWED_CHAT_IDS` can stay unset for now — the next step gets you the
right value.

### 4. Build and start just the bot

The bot lives behind a Compose **profile** so it never affects your normal
deploy. Bring it up on its own:

```bash
cd /home/codex/medcat
docker compose -f docker-compose.local.yml -f docker-compose.permanent.yml --profile bot up -d --build bot
docker compose -f docker-compose.local.yml -f docker-compose.permanent.yml logs -f bot
```

You should see a `starting bot (long polling)` log line. If it instead exits
immediately, the log line explains which variable is missing — see
[Troubleshooting](#troubleshooting).

### 5. Get Liran's chat id

Have Liran open the bot in Telegram (search for the username you gave it in
step 1, or send her the `t.me/<your-bot-username>` link) and send `/start`.
It replies with her chat's numeric id — no Claude call is made yet, since her
id is not on the allowlist. Send that number back to whoever administers the
server.

### 6. Allow her chat id and restart

```bash
cd /home/codex/medcat
nano .env   # set BOT_ALLOWED_CHAT_IDS=<the number from step 5>
docker compose -f docker-compose.local.yml -f docker-compose.permanent.yml --profile bot up -d bot
```

From here on, anything she sends the bot gets a real Claude reply, grounded
in the project docs.

To add more people later (yourself, another reviewer), use a comma-separated
list: `BOT_ALLOWED_CHAT_IDS=111111111,222222222`.

## Context

The bot's system prompt is built at start-up from `docs/technical-capabilities.md`,
`docs/roadmap-proposals.md`, `docs/decisions.md`, `docs/review-and-publish.md`,
`docs/import-guide.md` and `docs/architecture.md` (see `BOT_CONTEXT_FILES` in
`apps/bot/src/config.ts` for the exact default). Editing any of those files
and redeploying the bot (`--profile bot up -d --build bot`) refreshes what it
knows — nothing needs to change in the bot's own code for a documentation
update to reach it.

## Reading the conversation log

```bash
cd /home/codex/medcat
docker compose -f docker-compose.local.yml -f docker-compose.permanent.yml exec bot cat /data/conversation-log.md
```

or copy it out to read locally:

```bash
docker compose -f docker-compose.local.yml -f docker-compose.permanent.yml cp bot:/data/conversation-log.md ./liran-bot-log.md
```

## Revoking access

Remove the chat id from `BOT_ALLOWED_CHAT_IDS` in `.env` and redeploy the
`bot` service (same command as step 6). To shut the bot down entirely:

```bash
docker compose -f docker-compose.local.yml -f docker-compose.permanent.yml --profile bot stop bot
```

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| Container exits immediately, log says `TELEGRAM_BOT_TOKEN is not set` | `.env` is missing the value, or you ran `up` without `--profile bot` after adding it |
| Bot never replies, even to `/start` | Wrong token, or the container isn't running — check `docker compose ... ps` |
| Bot replies to `/start` but not to normal messages | The sender's chat id isn't in `BOT_ALLOWED_CHAT_IDS` yet |
| Replies come back generic or wrong about the app | A context file failed to load — check the `loaded bot context` log line at start-up for the file count |
