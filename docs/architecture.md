# Architecture

## Shape

```
                    ┌─────────────────────────────────┐
   Browser ──HTTPS──▶│ Caddy — TLS, security headers  │
                    └────────┬──────────────┬─────────┘
                             │ /api/*       │ everything else
                    ┌────────▼──────┐  ┌────▼─────────────┐
                    │ Fastify API   │  │ nginx — static   │
                    │ TypeScript    │  │ React bundle     │
                    └────────┬──────┘  └──────────────────┘
                             │
                    ┌────────▼──────────┐    ┌──────────────────┐
                    │ PostgreSQL 16     │◀───│ backup — nightly │
                    │ pg_trgm, unaccent │    │ encrypted dumps  │
                    └───────────────────┘    └──────────────────┘
```

The client and API are same-origin behind the proxy, so the session cookie
needs no cross-site relaxation.

## Choices, and why

**PostgreSQL, no ORM.** Plain SQL through `pg`. The interesting logic here is
in the schema — partial unique indexes, append-only triggers, trigram indexes
— and an ORM would obscure it while adding a dependency to migrate away from
later. Queries are parameterised without exception.

**A dependency-free migration runner.** Numbered `.sql` files applied in order
inside a transaction, with a checksum recorded so an applied file that has
been edited is refused rather than silently re-run. Portable to any Postgres
host, with no framework to keep current.

**A shared domain package.** Roles and capabilities, the clinical field
registry, the workflow state machine, value states and text normalisation live
in `packages/shared`, used by both sides. The point is that the two cannot
drift: the search indexer and the query parser normalise text with the same
function, and the interface offers exactly the transitions the API will
accept.

**Search in Postgres.** Full-text with prefix completion for the common case,
trigram similarity as a second pass for misspellings. A separate search engine
would add an operational component, a second copy of clinical data, and a way
for the two to disagree.

## The data model

### Versions, not rows

A `medication` is a stable identity. Its content lives in
`medication_versions`, and an edit creates a new version rather than mutating
the old one. Two partial unique indexes enforce the invariants in the database
rather than in application code:

```sql
-- At most one published version per medication.
CREATE UNIQUE INDEX ... ON medication_versions (medication_id) WHERE state = 'published';
-- At most one open editorial version per medication.
CREATE UNIQUE INDEX ... ON medication_versions (medication_id)
  WHERE state IN ('draft','in_clinical_review','changes_requested','approved');
```

### Field values carry a state

Clinical content is stored as `{ state, text }` per field per language:

```jsonc
{ "maximum_dose": { "en": { "state": "provided", "text": "2mg/kg/day" },
                    "he": { "state": "not_supplied", "text": null } } }
```

A blank cell becomes `not_supplied` — a statement about the source document.
`not_applicable` is a clinical assertion and can only be set by a person.
`unknown` means genuinely undetermined. The reader always sees which.

Text is stored exactly as supplied. `2mg/kg/day` stays `2mg/kg/day`;
normalising it would be editing a clinical value.

### Taxonomy is split

The source website conflated therapeutic group, drug class and mechanism —
its own audit flagged this. They are four independent fields here:
`therapeutic_group`, `drug_class`, `drug_family`, `mechanism`.

### Provenance per claim

`citations` attach a source, page, jurisdiction, approval status and review
date to an individual field, not to a record. That is what allows a use to be
approved in one jurisdiction and off-label in another without asserting either
everywhere.

### Append-only history

`audit_log` and `revisions` are protected two ways: `TRUNCATE` is revoked, and
a `BEFORE UPDATE OR DELETE` trigger raises. Triggers apply to the table owner
too, so this holds even against a compromised application role — verified by
tests, including after a restore.

## Authentication

- **Invite-only.** No registration endpoint exists anywhere in the API.
- **Argon2id** at OWASP parameters (19 MiB, t=2, p=1).
- **Server-side sessions** with both idle and absolute expiry, revocable
  immediately. The cookie is HttpOnly, SameSite=Lax and Secure in production;
  the browser never holds a token it could leak.
- **Tokens are stored as keyed HMACs**, so a database dump does not yield
  usable session or invitation tokens without `APP_SECRET`.
- **TOTP MFA**, mandatory for administrators — no session is issued until
  enrolment completes. Secrets are AES-256-GCM encrypted at rest; recovery
  codes are Argon2id hashes.
- **No account enumeration.** Login and password-reset answer identically for
  known and unknown addresses, with a dummy verification to equalise timing.

### Rate limiting

Per-account limits are strict — that is what stops guessing at one account.
Per-IP limits are a generous backstop, because a hospital site behind one NAT
gateway presents a single address for every clinician on it. A low per-IP
limit would lock out the building; this was found by the test suite and fixed.

## Authorisation

Capabilities, not a role ladder. `roleHasCapability` is the single definition,
checked in the API on every route. The interface uses the same function only
to decide what to render, and the tests assert that a user who navigates
directly to a screen their role does not hold is refused by the server, not
merely hidden from.

## The import pipeline

```
upload ──▶ confirm mapping ──▶ validate & preview ──▶ commit to DRAFT
  │              │                    │                      │
stored       never applied      detectors raise        review still
verbatim     automatically      findings, never        required before
by SHA-256                      corrections            anything publishes
```

Twelve detectors run over each batch. Every one of them reports; none of them
edits. Committing merges rather than overwrites, so a field the workbook omits
does not erase what the catalogue holds.

## Search

Two passes:

1. **Full text** — `tsvector` with weights (names A, classification B, body C)
   and prefix completion on the final term.
2. **Trigram** — only when the first returns nothing. Word similarity with a
   0.4 floor, applied with `SET LOCAL` so a pooled connection never carries the
   setting into another query. This admits a single slip or transposition
   (0.47 for `flouxetine`) while rejecting unrelated input (0.1).

One normalisation routine serves both the indexer and the query parser:
Hebrew niqqud and final-letter folding, Latin diacritic folding, case and
punctuation. A locale's index falls back to the other language, so a clinician
working in Hebrew still finds records written in English — which is most of
them, since drug names are routinely written in Latin script.

Match highlighting is computed server-side against the original text, so what
is emphasised always agrees with what was matched, and display text is never
rewritten.

## The client

React and Vite, no UI framework. The whole interface is a few hundred lines of
CSS built on logical properties (`inline-start`, `block-end`), which is what
makes Hebrew RTL a direction change rather than a second stylesheet.

Accessibility is structural rather than retrofitted: a skip link, focus moved
to the main region on route change only, an ARIA combobox for autocomplete,
live regions for result counts, per-row accessible names on repeated controls,
and colour tokens that meet WCAG 2.1 AA in both light and dark themes.

Results are a compact list. The source site rendered 44 tall cards on one page
— unusable on a phone, and its own audit said so.

## Security summary

| Concern | Measure |
| --- | --- |
| Unauthenticated access | Every route requires a session; the catalogue has no public surface |
| Privilege escalation | Capabilities checked server-side on every request |
| Password attacks | Argon2id, per-account rate limiting, lockout |
| Session theft | HttpOnly + SameSite + Secure cookies, HMAC-stored tokens, idle and absolute expiry |
| Database theft | Password hashes, HMAC token digests, encrypted MFA secrets, encrypted backups |
| History tampering | Append-only tables enforced by trigger and revoked privileges |
| Injection | Parameterised queries without exception; field keys validated against a registry |
| XSS | React escaping, CSP, no `dangerouslySetInnerHTML` anywhere |
| Transport | TLS with automatic renewal, HSTS, security headers |
| Data minimisation | No patient data field exists; logs redact credentials and tokens |
