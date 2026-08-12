# Deploying Beitenu

You don't need a computer for this. Railway builds from GitHub, so the whole
thing can be done in a phone browser.

The app **seeds itself on first boot** — no terminal, no setup commands. Point
Railway at the repo, give it the environment variables, attach a volume.

---

## 1. Create the service

1. [railway.app](https://railway.app) → sign in with GitHub
2. **New Project → Deploy from GitHub repo** → `ishaydomb-ui/A`
3. Settings → **Branch**: `claude/personal-dashboard-brainstorm-5vwg0o`

Railway reads `railway.json` and builds from the `Dockerfile` automatically.

## 2. Attach a volume — do this before the first successful boot

**Variables → + New Volume**, mount path exactly:

```
/data
```

This is where the database lives. Without it the app still runs, but everything
is wiped on every redeploy — every coupon, expense and synced event.

> **Keep replicas at 1.** SQLite is a single file on a single volume; two
> containers writing to it would corrupt data. `railway.json` pins this, but
> don't raise it in the dashboard.

## 3. Environment variables

**Variables → Raw Editor**, paste and fill in:

```
DATABASE_PATH=/data/beitenu.sqlite
ANTHROPIC_API_KEY=
AUTH_SECRET=
CREDENTIALS_KEY=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=https://YOUR-APP.up.railway.app/api/auth/google/callback
ENABLE_SCHEDULER=1
INTAKE_TOKEN=
```

For the three secrets, any long random string works. On a phone, use a password
manager's generator; at a terminal, `openssl rand -hex 32`. `CREDENTIALS_KEY`
must be exactly 64 hex characters.

| Variable | What it does |
|---|---|
| `AUTH_SECRET` | Signs your login cookie. Changing it signs you both out. |
| `CREDENTIALS_KEY` | Encrypts Google refresh tokens and store logins. **Lose it and those become unreadable** — keep a copy somewhere safe. |
| `ENABLE_SCHEDULER` | Runs automations in-process every 15 min. Needed for the morning brief and calendar syncing. |
| `INTAKE_TOKEN` | Shared secret for WhatsApp / Shortcuts webhooks. Set it even if unused. |

## 4. Get your URL, then close the loop with Google

Settings → **Networking → Generate Domain**. You'll get something like
`beitenu-production.up.railway.app`.

Two things must now match it:

1. Update `GOOGLE_REDIRECT_URI` above to use that exact hostname
2. Google Cloud Console → **Clients** → your OAuth client → add the same URL to
   **Authorised redirect URIs**

Character-for-character, `https`, no trailing slash. A mismatch gives
`redirect_uri_mismatch` at sign-in and is the single most common failure here.

## 5. Check it came up

Visit `https://YOUR-APP.up.railway.app/api/health`:

```json
{
  "ok": true,
  "database": "reachable",
  "seeded": true,
  "signIn": "configured",
  "agent": "configured",
  "scheduler": "on"
}
```

`"signIn": "open"` means `GOOGLE_CLIENT_ID` didn't take — **the app is
unauthenticated and anyone with the URL can read everything.** Fix before using it.

Then open the app itself. You should be bounced to a sign-in page. Sign in,
accept the "Google hasn't verified this app" warning once, and go to
**Settings → Sync now** to pull your calendar.

---

## Running it locally instead

```bash
npm install
cp .env.example .env      # fill in, use http://localhost:3000/... for the redirect
npm run db:seed
npm run dev
```

Note that with `GOOGLE_CLIENT_ID` unset the app runs **open** and attributes
everything to Ishay. Fine on your own machine, never in production.

---

## Cost and shape

One always-on container plus a small volume — a few pounds a month on Railway's
hobby plan. Fly.io is equivalent. Render's free tier sleeps when idle, which
stops the scheduler, so it would need the paid tier too.

The scheduler runs **inside** the web container rather than as separate cron
jobs: for a two-person household that's one process instead of a billed
container per firing, and the automations decide for themselves what's actually
due, so a coarse 15-minute tick is enough.

---

## When something breaks

**Build fails on `better-sqlite3`** — the build stage needs `python3 make g++`.
They're in the Dockerfile; if you switched to Nixpacks, switch back to Docker.

**Health check fails on deploy** — usually the volume isn't mounted at `/data`,
or `DATABASE_PATH` points somewhere else. The two must agree.

**Everything wiped after a redeploy** — no volume attached. Attach one at
`/data`; the app will re-seed itself, but anything you'd entered is gone.

**`redirect_uri_mismatch`** — see step 4.

**"is not a household member"** — you signed in with a Google account whose
address isn't on an adult row in `people`. The seed uses `ishaydomb@gmail.com`
and `lirikor@gmail.com`; ask the assistant to correct it, or edit `src/lib/seed.ts`.

**Calendar syncs nothing** — the Google Calendar API isn't enabled for the
project, or the `calendar.readonly` scope wasn't added under **Data Access**.

**Sync stops working after about a week** — the OAuth app went back to
"Testing". Google expires refresh tokens after 7 days in that state; the
publishing status must stay **In production**.
