# Beitenu

A shared household dashboard and AI agent for Ishay and Liran — one place for the
schedule, tasks, money, documents, food and everything else, with an assistant that
reads and writes all of it.

The AI is the front door. The widgets are just the readable state it maintains.

---

## The idea in one paragraph

Most "AI assistants" answer from memory and get things subtly wrong. This one can't:
every factual question — *which coupons are still available*, *which days am I picking
up the kids*, *how's the budget* — is answered by a tool call against real rows in the
database. If there's no row, it says so. On top of that sit **Skills** (fixed
procedures so the same job is done the same way every time) and **Trackers**
(user-defined rubrics so a new thing to track is a row, not a deploy).

---

## Deploying

**[DEPLOY.md](./DEPLOY.md)** — Railway, from a phone browser, no computer needed.
The app seeds itself on first boot, so there are no setup commands to run.

## Getting started (local)

```bash
npm install
cp .env.example .env      # fill in ANTHROPIC_API_KEY at minimum
npm run db:seed           # people, budget categories, starter skills & trackers
npm run dev               # http://localhost:3000
```

Run the tests — none of them need an API key or a network:

```bash
npm test                  # event classification + data layer
npx tsx scripts/test-session.ts
```

### Turning on sign-in

Until `GOOGLE_CLIENT_ID` is set the app runs **open**, with every action attributed
to Ishay. That's fine locally and wrong in production. To close it:

1. Google Cloud Console → **APIs & Services → Credentials → Create OAuth client ID →
   Web application**.
2. Add `GOOGLE_REDIRECT_URI` to *Authorised redirect URIs*.
3. Enable the **Google Calendar API** for the project.
4. Set `AUTH_SECRET` to a random 32+ char string.

Sign-in is an **allowlist**: only emails on an adult row in `people` can get in.
Everyone else is refused at the callback before a session is issued. One consent
covers both signing in and reading the calendar.

```bash
npm run calendar:sync     # first sync, after someone has signed in
```

---

## Architecture

```
Web chat ─┐
WhatsApp ─┼─▶ intake ─▶ agent loop ─▶ tools ─▶ SQLite
Voice   ──┘                 │                    │
                            ▼                    ▼
                     approval queue ─▶ executor (browser worker, email)
```

| Piece | File | What it does |
|---|---|---|
| Schema | `db/schema.sql` | The whole data model, commented |
| Session primitives | `src/lib/session.ts` | Edge-safe cookie signing — no DB import, so middleware can use it |
| Auth | `src/lib/auth.ts` | Allowlist and current-person lookup |
| Google OAuth | `src/lib/google/oauth.ts` | Consent, token refresh, encrypted storage |
| Calendar sync | `src/lib/google/calendar.ts` | Incremental sync with syncToken |
| Event classification | `src/lib/google/classify.ts` | Turns titles into answerable facts |
| Agent loop | `src/lib/agent/index.ts` | One loop for every channel |
| Tools | `src/lib/agent/tools.ts` | Everything the agent can read or do |
| System prompt | `src/lib/agent/prompt.ts` | Assembled live from the DB, not hardcoded |
| Trackers | `src/lib/trackers.ts` | The extensibility primitive |
| Schedule | `src/lib/schedule.ts` | Pickup/on-call/conflict resolution |
| Approvals | `src/lib/approvals.ts` | Nothing risky happens without a human |
| Executor | `src/lib/executor.ts` | The only code that reaches the outside world |
| Automations | `src/lib/automations.ts` | Trigger → skill, stored as rows |
| Store adapters | `src/lib/grocery/adapters/` | Basket filling, per chain |

### Why SQLite

Two adults, one household. A single file is trivially backed up, has no ops, and
every query in the app is sub-millisecond. All access goes through `src/lib/db.ts`,
so moving to Postgres later is one file.

---

## Skills

A skill is a named, versioned procedure stored in the database and injected into the
system prompt. When a request matches a skill's description, the agent follows it step
by step instead of improvising — and says which skill it used.

Seeded skills:

| Skill | Autonomy | Purpose |
|---|---|---|
| `budget-intake` | auto | Categorise spending by the household's own rules |
| `meal-planning` | ask | Plan the week around on-call days and expiring food |
| `grocery-run` | ask | Price a list and fill a basket (never checks out) |
| `bureaucracy-escalation` | ask | Draft the next formal letter on a stalled case |
| `trip-packing` | auto | Generate a packing list in the established structure |
| `document-filing` | auto | Classify, file as a pointer, extract deadlines |
| `daily-brief` | auto | The morning summary |

Edit one on the Skills page and behaviour changes on the next message. No deploy.

---

## Trackers

Coupons, watchlist, gift ideas, home maintenance ship seeded. To add a new one, just
ask — *"start tracking books I want to read"* — and the agent creates it with inferred
fields. One table component renders all of them.

`available_only` means active **and** not past its expiry, which is what makes
*"which coupons are still available"* exactly right rather than approximately right.

---

## Groceries

**Pricing and product data** come from the price files every chain with 3+ stores is
legally required to publish daily (2014 Food Act, price transparency). Public, no
account, and it covers both chains. `npm run prices:sync -- --chain=shufersal`.

**Basket filling** is a separate, gated action:

| Chain | Prices & lists | Basket automation |
|---|---|---|
| שופרסל Shufersal | ✅ | ✅ supported |
| טיב טעם Tiv Taam | ✅ | ❌ not implemented — app-first platform, no confirmed web flow |

The browser worker stops at a **filled basket**. It does not check out, choose a
delivery slot, or pay — by design, there is no method on the adapter interface to do
so. If a CAPTCHA or OTP appears it stops and hands back to a human rather than trying
to defeat it. Shufersal locks baskets the evening before delivery (robotic fulfilment),
so runs should be scheduled well ahead of a slot.

Credentials are AES-256-GCM encrypted at rest and are never placed in a model prompt —
only the executor decrypts, at the moment it drives the login form.

---

## Intake channels

| Channel | Endpoint | Notes |
|---|---|---|
| Web chat | `/api/chat` | Same-origin |
| Voice (in app) | `/api/voice` | Hold the mic button on any screen |
| WhatsApp | `/api/whatsapp` | Cloud API webhook; text and voice notes |
| Shortcuts / external | `/api/intake` | Shared-token guarded |
| Scheduler | `/api/cron` | Point a platform cron at it every 15 min |

Voice needs a speech-to-text step (the Claude API takes text and images, not audio).
Set `STT_URL` to any OpenAI-compatible `/audio/transcriptions` endpoint. Hebrew/English
code-switching is the norm here, so pick a model that handles both.

Only phone numbers and emails belonging to household adults are accepted; anything else
is dropped.

---

## Safety model

Acts directly (reversible, routine): tasks, reminders, tracker items, expense logging,
document filing, meal plans, grocery lists, remembering facts.

Requires approval (every time): sending anything outside the household, spending money
or filling a basket, booking or submitting official forms, anything hard to undo.

Every state change is written to `activity`. The approval card shows the exact payload
before you say yes.

---

## Event classification

A Google event is just a title and a time. *"Which days am I picking up the kids"*
is only answerable because something decided, once at sync time, that
`לאסוף את ינאי וברי` is a **pickup** owned by whoever created it. Every synced event
gets a `kind`, a `subject` (which child) and an `owner` (which parent).

Rules are explicit rather than model-inferred: classification runs over every event
on every sync, it must be deterministic, and a wrong label silently corrupts every
answer built on top of it. `scripts/test-classify.ts` covers 21 real titles from the
household calendar.

One trap worth knowing: JavaScript's `\b` is ASCII-only, so `/\bחוג\b/` never matches
a Hebrew word. The `he()` helper matches on real delimiters and allows the
single-letter prefixes Hebrew glues onto words.

---

## Still to wire up

These need accounts or credentials rather than code:

- **Hosting** — anywhere with a persistent volume (Railway, Fly)
- **STT endpoint** — for voice notes
- **SMTP** — the executor preserves approved email drafts but doesn't send yet
- **Store credentials** — then set `BROWSER_WORKER_ENABLED=1`
- **Live price sync** — `discover()` per chain will likely need adjusting on first real run
- **Gmail and Drive** — the scopes are anticipated in `oauth.ts`; adding them later
  forces a re-consent
