# Medication Catalogue — Technical Capabilities Report

**Purpose of this document.** A factual, code-verified description of the system as it exists in the repository `ishaydomb-ui/A` (branch `claude/medical-drug-catalog-app-flqwce`, HEAD `2029880`, September 2026), written for an external technical/clinical-safety reviewer. Every statement below was checked against source, migrations, configuration or test output; where something is a deliberate non-feature it is listed as such rather than omitted. Nothing here describes planned work.

**What the system is.** A private, invitation-only, bilingual (Hebrew/English, full RTL) web reference for psychiatric medications, with an editorial workflow (draft → clinical review → approved → published), a provenance/citation model, an Excel import pipeline with automated data-quality findings, and a search-and-discovery front end. It is a *reference aid*, not a decision-support or prescribing tool.

**What it is not (by design).** No dose calculator, no AI recommender, no drug-interaction checker, no patient data of any kind, no analytics/third-party scripts, no public registration.

---

## 1. Architecture and stack

```
Browser ──HTTPS──▶ Caddy (TLS via Let's Encrypt, security headers)
                     ├─ /api/*, /healthz, /readyz ──▶ Fastify API (Node 22, TypeScript)
                     └─ everything else ────────────▶ nginx serving the React bundle
                                                          │
                                              PostgreSQL 16 (pg_trgm, unaccent)
                                                          │
                                              backup container — nightly encrypted dumps
```

Client and API are same-origin behind the proxy; the session cookie needs no cross-site relaxation.

| Layer | Technology | Notes |
|---|---|---|
| Monorepo | pnpm workspaces (`pnpm@10`) | `apps/api`, `apps/web`, `packages/shared` |
| API | Fastify 5, TypeScript 5.9, `zod` validation, `pino` logging | plain SQL via `pg`, **no ORM** |
| Auth crypto | `@node-rs/argon2` (Argon2id), `otplib` (TOTP), `qrcode` | |
| Excel | `exceljs`, `jszip` | jszip used to rewrite namespace-prefixed SpreadsheetML in memory |
| Mail | `nodemailer` (SMTP optional; falls back to a file outbox) | only invitations and password resets are ever mailed |
| Web | React 18, React Router 6, Vite 6 | no UI framework; single `styles.css` with design tokens |
| Shared | `@med/shared` — roles/capabilities, field registry, workflow state machine, value states, text normalisation | used by both API and client so they cannot drift |
| DB | PostgreSQL 16 with `pg_trgm`, `unaccent` | search lives in Postgres; no external search engine |
| Migrations | dependency-free runner: numbered `.sql` files, applied in a transaction, checksum-recorded (an edited applied file is refused) | 10 migrations |
| Tests | Vitest (API/shared), Playwright + axe-core (E2E) | see §15 |
| Deployment | Docker Compose (three files, see §14); Caddy `2-alpine`; `postgres:16-alpine` | |

### Repository layout

```
apps/api/        Fastify API, migrations/, scripts (seed, create-admin, e2e-reset), tests
apps/web/        React client, e2e/ (Playwright), .auth/ (test session states)
packages/shared/ domain package (see above)
ops/             Caddyfile, backup.sh, restore.sh, local-up.sh, expose-tunnel.sh, ssh-tunnel.sh, nginx.local.conf
docs/            architecture, admin-guide, backup-and-restore, decisions, deployment, import-guide, migration, quickstart-local, review-and-publish, testing
seed/, db/       seed workbook and DB init assets
```

---

## 2. Domain model

### 2.1 Clinical field registry (`packages/shared/src/fields.ts`)

A single registry defines every field a medication record contains, its bilingual label, its display group, and whether it is comparable/searchable. 30 fields in 9 groups:

| Group | Fields (key) | Comparable | Searchable | Shape |
|---|---|---|---|---|
| identity | `generic_name`, `trade_names`, `brand_names_israel`, `formulations_israel` | generic/trade only | yes | trade_names is a list; the two Israeli fields are prose, because the source writes brand names with their strengths inline |
| taxonomy | `therapeutic_group`, `drug_class`, `drug_family`, `mechanism` | yes | yes | deliberately four independent fields — the source site conflated them |
| administration | `starting_age`, `formulation`, `available_strengths` | yes | formulation only | |
| dosing | `dose_range`, `starting_dose`, `starting_dose_adults`, `starting_dose_pediatrics`, `titration`, `titration_adults`, `titration_pediatrics`, `maximum_dose` | yes | no | the workbook keeps adult and paediatric schedules in separate columns; the undivided `starting_dose`/`titration` remain for records imported before the split |
| pharmacokinetics | `onset`, `duration` | yes | no | |
| indications | `adult_indications`, `pediatric_indications` | **no** (prose) | yes | |
| safety | `qtc_adults`, `qtc_pediatrics` (comparable), `side_effects`, `contraindications` (lists, not comparable), `side_effect_legend` (prose) | mixed | yes | `side_effect_legend` carries the source's own key to the graded matrix (WG, AKA, PKN…), which is unreadable without it |
| monitoring | `monitoring_tests` | yes | yes | list |
| notes | `clinical_notes`, `comments` | no (prose) | yes | `comments` is the workbook's own Comments column; `clinical_notes` is the app's editorial note. They are not the same thing |

Max 3 medications per comparison (`MAX_COMPARE = 3`). Long prose is excluded from comparison by policy (side-by-side prose invites false equivalence between differently worded sources).

### 2.1.1 Per-class presentation profiles

The registry is a **superset**: it holds every field any source sheet can fill. The authoritative workbook, however, is four genuinely different tables — antipsychotics, antidepressants, mood stabilisers and ADHD each have their own columns — and rendering one universal schema over all of them was wrong in both directions. It grouped fields under headings the source never had, and it displayed fields a sheet has no column for as "not supplied", so `QTc — adults: not supplied` on an ADHD drug read as missing clinical data rather than as a column that does not exist there.

`CLASS_PROFILES` therefore maps a record's therapeutic group to an ordered list of fields mirroring its sheet: its columns, in its order, under its own headings (`Dosage`, `Max dose and titration`, `Tests at baseline / during treatment`). Matching is case-insensitive, since the website snapshot writes "Mood Stabilizers" and the workbook "Mood stabilizers".

Two rules keep this honest for records that predate it:

- A profile entry may name **fallback keys** — older fields holding the same content. A record imported before the adult/paediatric split shows its undivided `titration` rather than an empty `Titration — adults`.
- A value shown from a fallback is captioned with **that field's own label**. An older record's `trade_names` are not known to be the Israeli brand names, so they are never headed as though they were.

A therapeutic group with no profile keeps the grouped view, so nothing is lost for records outside these four classes.

### 2.2 Value states (`packages/shared/src/values.ts`)

Every field value is `{ state, text }` with `state ∈ {provided, unknown, not_supplied, not_applicable}`.

- A blank imported cell becomes **`not_supplied`** — a statement about the source document, never a clinical claim.
- **`not_applicable`** can only be asserted by a human reviewer.
- Import trims edge whitespace and does **nothing else**: no unit normalisation, no spelling correction, no inference. `2mg/kg/day` is stored and shown verbatim (covered by tests).
- Hebrew and English are stored independently; one is never machine-translated from the other. When only one language has text, the UI shows it labelled as such (`fallback: true`, "Shown in English/Hebrew").

### 2.3 Versions, not rows

- `medications` is the stable identity (slug); content lives in `medication_versions`. Editing a published record opens a **new version**; the live one is never mutated.
- Two partial unique indexes enforce: at most one **published** version per medication; at most one **open editorial** version per medication.
- `revisions` records who changed what, when and why; `audit_log` records every significant action. Both are **append-only in the database** (trigger + `REVOKE UPDATE, DELETE, TRUNCATE`, holding even against the application role — verified by tests, including after a restore).
- `medication_aliases` supports alternative names for search.

### 2.4 Workflow state machine (`packages/shared/src/workflow.ts`)

States: `draft`, `in_clinical_review`, `changes_requested`, `approved`, `published`, `archived`. Only `published` is public.

| From | To | Capability required | Reason required |
|---|---|---|---|
| draft | in_clinical_review | `catalogue:submit_for_review` | no |
| changes_requested | in_clinical_review | `catalogue:submit_for_review` | no |
| in_clinical_review | approved | `catalogue:clinical_review` | no |
| in_clinical_review | changes_requested | `catalogue:clinical_review` | **yes** |
| approved | published | `catalogue:publish` | no |
| approved | in_clinical_review | `catalogue:clinical_review` | yes |
| published | draft | `catalogue:edit_draft` | yes |
| published / draft | archived | `catalogue:publish` | yes |

Additional enforced rules (tests exist for each): an editor cannot approve their own work; an editor cannot publish; skipping review (draft → published) is rejected; a version under review cannot be edited.

### 2.5 Publication gates (`services/catalogue.ts`)

Publishing is refused, listing **all** blockers at once, if any of:

- `not_clinically_approved` — no reviewer approved this exact version
- `open_high_findings` — a high-severity data-quality finding attached to the record is unresolved
- `missing_citation` — an unsourced claim in a field where that is most dangerous: adult/paediatric indications, adult/paediatric QTc, contraindications, maximum dose
- `missing_review_date`

**Evaluation ("preview") mode.** A global setting `publication.allow_unvalidated` (admin-only, default off) permits publishing with gates outstanding. This is never quiet: the version is flagged `published_unvalidated`, the overridden blockers are stored on the row (`overridden_blockers`), search results carry the flag, the record page shows "Not clinically reviewed", and every screen carries a persistent "Evaluation copy" banner. Only administrators can switch it on (403 for others; tested).

### 2.6 Provenance

`citations` attach a source to a specific field of a specific version: title, document reference, URL, page/section, **jurisdiction**, `approval_status ∈ {approved, off_label, unknown, not_applicable}`, review date. The UI warns when a document's jurisdiction differs from the one being claimed. Attaching a citation is recorded against the acting account.

---

## 3. Identity, access and security

### 3.1 Roles and capabilities (`packages/shared/src/roles.ts`)

Authorisation is capability-based; the API enforces capabilities on every route, the UI only uses them to hide controls.

| Capability | physician | clinical_reviewer | editor | admin |
|---|:-:|:-:|:-:|:-:|
| catalogue:read_published | ✓ | ✓ | ✓ | ✓ |
| catalogue:read_unpublished | | ✓ | ✓ | ✓ |
| catalogue:edit_draft | | | ✓ | ✓ |
| catalogue:submit_for_review | | | ✓ | ✓ |
| catalogue:clinical_review | | ✓ | | ✓ |
| catalogue:publish | | | | ✓ |
| import:create | | | ✓ | ✓ |
| import:commit | | | | ✓ |
| review:read | | ✓ | ✓ | ✓ |
| review:resolve | | ✓ | | ✓ |
| users:manage | | | | ✓ |
| audit:read | | | | ✓ |

### 3.2 Authentication

- **Invitation-only.** No `POST /register` exists. An administrator creates an account and gets a single-use invitation link (72 h default); accepting it sets the password and verifies the address. Re-inviting an unaccepted account updates it in place; reissuing revokes the previous link. Links are shareable from a phone (Web Share API / clipboard).
- **Passwords:** Argon2id; length-first policy per NIST SP 800-63B (≥12 characters, no forced composition). Timing-equalised "unknown user vs wrong password". Lockout after 8 failed attempts for 15 min (configurable).
- **Sessions:** opaque token, hashed at rest, `HttpOnly; SameSite=Lax; Secure` (production) cookie, persistent with an explicit `expires`. **Idle timeout 30 min (sliding), absolute 12 h** — both configurable (`SESSION_IDLE_MINUTES`, `SESSION_ABSOLUTE_HOURS`). No "remember me" by design. Role change or suspension revokes all sessions immediately. `logout-all` endpoint exists.
- **MFA (TOTP):** 30-second step, ±1 window; secrets encrypted at rest with `APP_SECRET`; 10 single-use recovery codes; enrolment via QR *and* an `otpauth://` one-tap link (a phone cannot scan its own screen). Enrolment is idempotent (a reload does not rotate the secret behind an already-scanned QR).
  - Mandatory for the `admin` role **when the setting `security.mfa_required_for_admin` is on** (default on; admin-toggleable in Users → Security, migration `0010`). Voluntary for other roles.
  - An already-enrolled account is always challenged regardless of the setting; an administrator can remove another account's authenticator (`POST /users/:id/mfa/reset`), which also revokes that account's sessions.
- **Password reset:** emailed single-use token, 60 min default; identical response for unknown addresses.

### 3.3 Rate limiting and lockout (DB-backed counters)

| Scope | Window | Default max |
|---|---|---|
| login per account | 15 min | 10 |
| login per IP | 15 min | 200 |
| MFA verify per IP | 15 min | 200 |
| password reset per account / per IP | — | 5 / 50 |
| invitation accept per IP | 60 min | 20 |
| search per minute | 1 min | 120 |

### 3.4 Transport and headers

- Caddy: HSTS (1 y, includeSubDomains), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, restrictive `Permissions-Policy`, COOP `same-origin`, `X-Robots-Tag: noindex, nofollow`, `Server` header removed; any other hostname gets 404.
- API (`@fastify/helmet`): CSP `default-src 'self'`, `frame-ancestors 'none'`, `object-src 'none'`; CORS restricted to `PUBLIC_URL` in production; CORP `same-site`.
- Uploads: 25 MB max, one file, via `@fastify/multipart`.

### 3.5 Audit

`audit_log` (append-only): logins/failures/lockouts, invitations, MFA events, settings changes, user edits, workflow transitions, imports. Records **who did what — never to whom**: no patient data exists anywhere in the system, and search terms are not retained against an account. Readable via `GET /api/users/audit-log` (`audit:read`).

---

## 4. API surface

All routes are JSON, zod-validated, capability-gated as noted. Prefixes: `/api/auth`, `/api/users`, `/api/search`, `/api/medications`, `/api/imports`, `/api/review`, plus meta routes.

| Prefix | Endpoints |
|---|---|
| `/api/auth` | `POST login`, `POST mfa/verify`, `POST mfa/enroll/start`, `POST mfa/enroll/complete`, `POST mfa/setup`, `POST mfa/setup/confirm`, `POST invitations/accept`, `GET invitations/:token`, `POST password/forgot`, `POST password/reset`, `POST password/change`, `GET me`, `POST logout`, `POST logout-all` |
| `/api/users` (admin) | `GET /`, `POST invitations`, `POST :id/invitations/resend`, `PATCH :id` (display name, role, status), `POST :id/mfa/reset`, `GET audit-log` |
| `/api/search` | `GET /` (q, locale, filters, limit/offset, includeUnpublished), `GET suggest`, `GET facets` |
| `/api/medications` | `GET :slug`, `GET export.xlsx`, `POST compare`, `GET :slug/revisions`, `POST /` (create draft), `POST :slug/draft` (new version of published), `PATCH versions/:id`, `POST versions/:id/transition`, `GET versions/:id/workflow`, `POST/DELETE versions/:id/citations[/:citationId]`, `GET :slug/source-suggestions`, `POST :slug/aliases` |
| `/api/imports` | `GET /`, `POST /` (upload), `POST :batchId/validate`, `GET :batchId`, `POST :batchId/commit`, `POST :batchId/discard` |
| `/api/review` | `GET findings`, `GET findings/export.xlsx`, `POST findings`, `PATCH findings/:id` (resolve, reason required), `GET queue` |
| meta | `GET /healthz`, `GET /readyz` (reports migration count), `GET /api/admin/status`, `GET /api/settings/public`, `GET /api/settings` (admin), `PUT /api/settings/:key` (admin) |

There is **no** user-delete endpoint (accounts are deactivated/suspended, never removed).

---

## 5. Search

- Index tables `search_documents` (tsvector + trigram GIN indexes on name and body) and `search_facets`, maintained by an indexer from the current published/editorial versions.
- Query text and indexed text are normalised by the **same shared function** (`normalizeText`: case, accents/`unaccent`, Hebrew/Latin handling, tokenisation), so indexer and parser cannot disagree.
- **Pass 1:** full-text with prefix completion, plus exact/prefix alias matching. **Pass 2 (only if pass 1 is empty and query ≥ 3 chars):** trigram word-similarity with a floor of **0.4** — admits a one-character slip or transposition (`flouxetine` → 0.47) while rejecting unrelated input. Each hit reports `matchKind ∈ {exact, prefix, text, fuzzy}` and a "did you mean" so the UI can say it is showing approximate results.
- Highlighting of matched segments in generic and trade names.
- Facets and filters: therapeutic group, drug class, drug family, formulation (multi-value, URL-persisted so a search is shareable).
- Drafts are invisible to physicians in results, suggestions and facets (`includeUnpublished` requires `catalogue:read_unpublished`); direct navigation to an unpublished slug returns 404 for them.
- No server-side sort parameter exists (relevance/name only).

---

## 6. Import pipeline (`services/import/*`)

1. **Upload** an `.xlsx` (editor+). The original file is stored byte-identical for the audit trail. Duplicate uploads (same content hash) are refused.
2. **Sheet/column mapping** finds the catalogue sheet and maps headers to field keys (`mapping.ts`). The pipeline works around a real incompatibility: namespace-prefixed SpreadsheetML (`<x:workbook>`) is rewritten in memory so `exceljs` can read it.
3. **Validate/preview** (`POST :batchId/validate`): every row becomes a diff (new / changed / unchanged) against the current catalogue **without touching it**; blank cells are recorded as `not_supplied`.
4. **Data-quality detectors** (`detectors.ts`) run per row and per batch and attach findings with severity high/medium/low:
   - `suspectedLabelInversion` (e.g. the NRI/atomoxetine label swap in the source data)
   - `incompleteThreshold` (a clinical note whose threshold has no number)
   - `ambiguousSideEffect` (e.g. "blood pressure" listed with no direction)
   - `mixedScript`, `unitFormatting`, `missingExpectedField`
   - `uncitedClaim`, `unstructuredIndication`
   - batch-level: `duplicateRows`, `repeatedClassText` (class-level text pasted across rows), `mixedTaxonomy`, `highMissingness`
5. **Commit** (admin only, `import:commit`): refused while any high-severity finding is unacknowledged; refused twice for the same batch. Rows are staged as **drafts** — nothing goes live from an import. Every record is linked to its batch and marked unvalidated until reviewed; findings are attached to the records they concern so they block publication.
6. Findings and the whole catalogue can be exported to `.xlsx`.

---

## 7. External source lookup

- Provider interface (`services/sources/`) with one implementation: **DailyMed (US FDA labels)**, jurisdiction `US`. Off by default (`SOURCE_LOOKUP_ENABLED=false`); timeout 8 s; results cached briefly; failures are reported per provider as "could not be checked", never silently.
- Suggestions are shown to editors on a record; **nothing is attached** until the person reads the document and confirms field, section/page, jurisdiction and approval status. The UI warns when the document's jurisdiction is not the one being claimed.

---

## 8. Web client

### Routes

| Route | Purpose | Access |
|---|---|---|
| `/` | **Explore** — search hero, quick-search chips and "clinical area" tiles derived from live facet counts (no hard-coded taxonomy), recently viewed (device-local), link to full catalogue | signed in |
| `/catalogue` | full list: search, type-ahead, filters (sidebar ≥900 px, bottom-sheet `<dialog>` below), active-filter chips, compact scannable rows, compare checkboxes, load-more, skeleton/empty/error states | signed in |
| `/medications/:slug` | full record laid out as its own source table where the class has a profile (§2.1.1), otherwise grouped by clinical area; value states, citations per field, validation status in the footer, bookmark toggle; `?draft=1` for reviewers/editors shows outstanding checks and source suggestions | signed in |
| `/compare?slugs=a,b,c` | side-by-side table of comparable fields only (max 3) | signed in |
| `/saved` | device-local bookmarks | signed in |
| `/review` | data-quality findings, resolution with mandatory reason | review:read / review:resolve |
| `/imports` | upload, validate, preview, commit/discard, history | import:create / import:commit |
| `/users` | invite, rename, role/status, reissue/share invitation, remove MFA, MFA-requirement toggle, audit | users:manage |
| `/account` | password change, voluntary MFA | signed in |
| `/sign-in`, `/accept-invitation`, `/forgot-password`, `/reset-password`, `/privacy`, `/terms` | public | — |

### Cross-cutting

- **Navigation:** four universal destinations (Explore / Catalogue / Compare / Saved) — fixed bottom nav ≤640 px, header nav above; administrative screens, language, theme, account and sign-out live in a user menu (capability-gated "Administration" section). Compare selection is app-global with a badge count.
- **i18n / RTL:** all UI strings in `i18n.ts` (EN is the typed source of truth; HE is checked against it at compile time). Direction is a pure `dir` flip — CSS uses logical properties throughout; no RTL stylesheet. Clinical content is never translated by the app.
- **Theme:** light by default regardless of OS setting; explicit dark toggle persisted per device; `color-scheme` declared so native controls match.
- **Accessibility (verified by axe-core in CI on every page, desktop and Pixel-5 profiles):** WCAG 2.1 AA colour contrast on all token pairs (documented ratios in `styles.css`), skip link, ARIA combobox for type-ahead (arrow keys/Escape), live region for result counts, labelled compare controls, ≥44 px touch targets, no horizontal scroll at 320–430 px, heading hierarchy, visible focus rings, `prefers-reduced-motion` respected.
- **Evaluation banner:** compact, collapsible, never dismissible, on every screen while evaluation mode is on.
- Session expiry is surfaced app-wide (401 broadcast → sign-in).
- Device-local only (localStorage, not clinical data): saved list, recently viewed, language, theme.

---

## 9. Settings (DB-backed, admin-editable via `PUT /api/settings/:key`)

| Key | Default | Meaning |
|---|---|---|
| `institution_name`, `contact_email` | pending approval | shown only once approved by the owner |
| `legal.privacy_policy`, `legal.terms` | placeholder, pending | until approved, the pages show a factual description marked as draft |
| `publication.allow_unvalidated` | `false` | evaluation mode (§2.5) |
| `publication.unvalidated_notice` | text | banner wording |
| `security.mfa_required_for_admin` | `true` | mandatory admin MFA switch |

Missing defaults are restored at start-up (`ensureDefaultSettings`); existing values are never overwritten.

---

## 10. Operations

### Deployment topologies

- `docker-compose.yml` — full production stack: `db`, `api`, `web`, `proxy` (Caddy), `backup`; strict required env vars.
- `docker-compose.local.yml` — zero-cost local/self-hosted stack with safe defaults, `RUN_MIGRATIONS_ON_START=true`; `ops/local-up.sh` bootstraps secrets, schema, first admin and seed in one command.
- `docker-compose.permanent.yml` — overlay adding a Caddy TLS proxy on a real hostname (`SITE_HOSTNAME`, `ACME_EMAIL`) **without migrating data**; `PUBLIC_BIND_IP` lets it coexist with another process holding :443 on a specific address (e.g. Tailscale Serve).
- Temporary exposure scripts: `ops/expose-tunnel.sh`, `ops/ssh-tunnel.sh`.

### Configuration (env, validated with zod at boot — invalid config refuses to start)

`DATABASE_URL`, `DATABASE_SSL`, `DB_POOL_MAX`, `APP_SECRET` (≥32 chars; encrypts MFA secrets), `PUBLIC_URL`, `COOKIE_DOMAIN`, `COOKIE_SECURE`, `SESSION_IDLE_MINUTES`, `SESSION_ABSOLUTE_HOURS`, `INVITE_EXPIRY_HOURS`, `RESET_EXPIRY_MINUTES`, `LOGIN_MAX_ATTEMPTS`, `LOGIN_LOCKOUT_MINUTES`, `RATE_LIMIT_*`, `IMPORT_STORAGE_DIR`, `MAX_UPLOAD_BYTES`, `SMTP_URL`, `MAIL_FROM`, `LOG_LEVEL`, `TRUST_PROXY`, `RUN_MIGRATIONS_ON_START`, `SOURCE_LOOKUP_ENABLED`, `SOURCE_LOOKUP_TIMEOUT_MS`, `DAILYMED_BASE_URL`, `BACKUP_PASSPHRASE`, `BACKUP_RETENTION_DAYS`.

### Backups, health, migrations

- Nightly `pg_dump --format=custom --compress=9`, symmetric-key encrypted (passphrase from env only, never on a command line), read-back verified, plaintext removed even on failure, retention pruning (30 d default), backup dir `chmod 700`. `ops/restore.sh` restores into a fresh database and overwrites only when explicitly forced. **Restore is covered by automated tests**, including that append-only protections survive a restore.
- `GET /healthz` (liveness) and `GET /readyz` (readiness + applied-migration count); Docker health checks on db/api.
- Migrations are checksummed and transactional; the API can apply pending ones at start.

---

## 11. Testing and quality

| Suite | Tool | Count | What it proves |
|---|---|---|---|
| `packages/shared` | Vitest | 28 | text normalisation (Hebrew/Latin, accents, tokenising); per-class profiles — every field key real, no duplicates, case-insensitive class matching, legacy fallback and its labelling, QTc absent from classes whose sheet has no QTc column |
| `apps/api` | Vitest against a real PostgreSQL | 165 | auth (invite-only, lockout, timing-equal responses, MFA enrol/verify/recovery, admin reset, MFA-requirement switch), catalogue workflow and every publication gate, evaluation mode, comparison cap, search (exact/prefix/fuzzy, drafts hidden), import pipeline end-to-end incl. all detectors, DailyMed provider, backup/restore, audit immutability, rate limiting |
| `apps/web` E2E | Playwright + axe-core, real stack (API + Vite + seeded Postgres), projects: desktop (1280 px), mobile (Pixel 5), auth-desktop, evaluation-mode | 97 tests / **179 runs** | login wall, MFA enrolment journey, role visibility, search/filters/type-ahead/Hebrew, Explore/Saved/recently-viewed, detail/compare, editorial (review, imports, invitations end-to-end), users admin, evaluation banner, WCAG audits of every page, keyboard operation, live regions, touch targets, no sideways scroll |

Status at HEAD: **all green** (typecheck clean across packages). `docs/testing.md` documents how to run each layer.

---

## 12. Clinical-safety and data-handling principles (enforced, not aspirational)

1. Blank ≠ not applicable (§2.2).
2. Nothing is silently corrected, normalised, inferred or translated; 12 detectors flag instead.
3. Citations are mandatory before publication for indications, QTc, contraindications, maximum dose.
4. Approving (clinical reviewer) and publishing (administrator) are separate acts on the *same exact version*.
5. Publishing with gates outstanding is possible only via an explicit admin setting and is permanently recorded and visibly labelled.
6. Comparison excludes prose.
7. History is append-only at the database level.
8. No patient data exists anywhere; the audit log never records "to whom".
9. Private by default: login wall, `noindex`, no analytics, no third-party scripts, no public registration.

---

## 13. Known limitations and deliberate omissions

- **No drug-interaction checker, dose calculator or AI recommender** — deliberate; would need separate clinical-safety, privacy and regulatory review.
- **Side effects, contraindications, indications, monitoring are free text** (lists/prose). They are searchable and cited but not structured, so they cannot be compared as a matrix or used to generate schedules.
- **Saved list and recently-viewed are device-local (localStorage)** — not synced across devices, not part of the clinical record.
- **Compare selection is in-memory** (lost on full reload; the URL of an opened comparison is shareable).
- **No sort control** in results (no server-side sort parameter).
- **One external source provider** (DailyMed, US); no Israeli registry integration; no health-basket/reimbursement data.
- **No pregnancy/lactation, renal/hepatic adjustment, or dose-equivalence fields** in the registry.
- **Single-language content is common** in the seeded data; the UI labels the fallback but cannot fill it.
- **No user deletion**, only deactivation/suspension.
- **No "remember me"** — 30-min idle / 12-h absolute sessions by default.
- Email is optional; without SMTP, invitations/reset links are written to a server-side outbox and must be delivered by hand (the UI supports this).
- Mandatory admin MFA is switchable off by an administrator (introduced at the owner's request); when off, it only stops *forcing enrolment* — enrolled accounts are still challenged.

---

## 14. Suggested questions for an external reviewer

1. Are the four citation-mandatory fields (indications, QTc, contraindications, maximum dose) the right minimum for this catalogue's intended use? Should side effects or monitoring join them?
2. Is the fuzzy-match floor (0.4 word similarity, second pass only) appropriate for drug names, where near-neighbours can be different molecules?
3. Session policy: is 30 min idle / 12 h absolute right for a clinic setting, given the app is behind a login wall and MFA?
4. Is the evaluation-mode design (explicit admin switch, permanent per-record flag, banner on every screen) an adequate control for pre-review publication, or should evaluation copies be isolated from the production hostname entirely?
5. The device-local Saved/Recent features hold medication names only — confirm that this raises no confidentiality concern on shared devices.
6. Import detectors: which additional clinical-plausibility checks (e.g. maximum dose below starting dose, QTc without units) would add the most value?
7. The taxonomy is split into four fields because the source conflated them; review whether the current facet values in the seeded data reflect a defensible classification.
8. Threat model: the app role is prevented from rewriting history at the DB level; confirm the remaining trust boundary (DB superuser, backup passphrase custody, `APP_SECRET` custody) is documented and acceptable.
