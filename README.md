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

## Getting started

```bash
npm install
cp .env.example .env      # fill in ANTHROPIC_API_KEY at minimum
npm run db:seed           # people, budget categories, starter skills & trackers
npm run dev               # http://localhost:3000
```

Verify the data layer independently of the model:

```bash
npx tsx scripts/smoke.ts
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

## Still to wire up

These need accounts or credentials rather than code:

- **Google OAuth** — Calendar/Gmail/Drive sync for a deployed app both of you sign into
- **Hosting** — anywhere with a persistent volume (Railway, Fly)
- **STT endpoint** — for voice notes
- **SMTP** — the executor preserves approved email drafts but doesn't send yet
- **Store credentials** — then set `BROWSER_WORKER_ENABLED=1`
- **Live price sync** — `discover()` per chain will likely need adjusting on first real run
