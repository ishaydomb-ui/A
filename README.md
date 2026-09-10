# Medication Catalogue

A private, bilingual medication reference for clinicians. It turns an
authoritative Excel workbook into a fast, searchable catalogue — designed for
use on a phone — behind an invite-only login, with an editorial workflow that
keeps unverified clinical content away from the people who would act on it.

> **This is a professional reference aid.** It does not replace clinical
> judgement or current, approved prescribing information. It holds no patient
> data of any kind and must never be given any.

## Documentation

| Document | What it covers |
| --- | --- |
| [docs/quickstart-local.md](docs/quickstart-local.md) | **Start here** — running it on your own machine, at no cost |
| [docs/deployment.md](docs/deployment.md) | Deployment on Contabo or any other host |
| [docs/migration.md](docs/migration.md) | Moving the system to a different server or cloud |
| [docs/admin-guide.md](docs/admin-guide.md) | Accounts, roles, day-to-day administration |
| [docs/import-guide.md](docs/import-guide.md) | Importing and updating the Excel workbook |
| [docs/review-and-publish.md](docs/review-and-publish.md) | The Draft → Review → Approved → Published process |
| [docs/backup-and-restore.md](docs/backup-and-restore.md) | Backups, retention and the tested restore procedure |
| [docs/architecture.md](docs/architecture.md) | How the system is built and why |
| [docs/testing.md](docs/testing.md) | Test suites, what they cover, how to run them |
| [docs/decisions.md](docs/decisions.md) | Design decisions taken, and what still needs your approval |
| [docs/technical-capabilities.md](docs/technical-capabilities.md) | Code-verified capabilities report, for external/clinical-safety review |
| [docs/roadmap-proposals.md](docs/roadmap-proposals.md) | Unified list of proposed future capabilities, staged and prioritised |
| [docs/telegram-bot.md](docs/telegram-bot.md) | Optional Telegram bot for project discussion with Claude |

## What it does

**For clinicians.** Search by generic name, trade name, therapeutic group,
indication, mechanism or keyword, in Hebrew or English, with typo tolerance —
a misspelling still finds the drug. Results are a compact, scannable list;
opening one shows the full record grouped by clinical area, with the source
behind each claim. Up to three medications can be compared side by side.

**For editors and reviewers.** An Excel workbook is imported through a staged
pipeline that never touches the live catalogue: rows are mapped, validated,
diffed against what is already published, and committed as drafts. Anything
questionable becomes a review finding rather than a silent correction. A
record reaches clinicians only after a clinical reviewer has approved it.

**For administrators.** Accounts are created by invitation only. Roles are
enforced in the API, not the interface. Every security-relevant event and
every content change is recorded in an append-only log.

## Repository layout

```
apps/api          Fastify + TypeScript API, SQL migrations, ops scripts entry points
apps/web          React + Vite client, Playwright end-to-end tests
apps/bot          Optional Telegram bot for project discussion (see
                  docs/telegram-bot.md) — not part of the main stack
packages/shared   Domain model shared by both: roles, field registry,
                  workflow, value states, text normalisation
ops               Reverse proxy config, backup and restore scripts
docs              The documentation listed above
```

## Running it

The fastest path, needing only Docker:

```bash
ADMIN_EMAIL='you@example.org' ./ops/local-up.sh
```

It generates the secrets, builds and starts everything, applies the schema,
creates your account, loads the catalogue and prints the URL. See
[docs/quickstart-local.md](docs/quickstart-local.md).

## Running it from source

Requires Node 22, pnpm and PostgreSQL 16.

```bash
pnpm install
cp .env.example .env          # then edit DATABASE_URL and APP_SECRET

createdb medcat_dev
pnpm --filter @med/shared build
pnpm migrate
pnpm --filter @med/api create-admin   # first administrator

pnpm dev                      # API on :4000, client on :5173
```

To load the supplied website snapshot as drafts for review:

```bash
pnpm --filter @med/api exec tsx src/scripts/seed.ts path/to/workbook.xlsx
```

## Tests

```bash
pnpm test                          # unit and integration (needs PostgreSQL)
pnpm --filter @med/web exec playwright test    # end-to-end, all browsers
```

See [docs/testing.md](docs/testing.md) for what each suite covers.
