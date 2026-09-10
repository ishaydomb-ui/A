# Tests

335 automated tests. All pass.

| Suite | Count | What it runs against |
| --- | --- | --- |
| Domain unit tests | 17 | Pure functions, no I/O |
| Unit tests | 19 | The import quality detectors and the source lookup |
| API integration | 142 | A real PostgreSQL database |
| End-to-end | 157 | The real stack, in a real browser |

## Running them

```bash
# Unit and integration. Needs PostgreSQL; creates medcat_test.
pnpm test

# End-to-end. Starts the API and client itself.
pnpm --filter @med/web exec playwright test

# One project only.
pnpm --filter @med/web exec playwright test --project=mobile
```

The end-to-end suite manages its own servers and reseeds its database from
the supplied workbook through the same service layer the application uses, so
a fixture cannot drift from real behaviour.

## What is covered

### Authentication and accounts — 41 tests

That no registration endpoint exists; that a non-administrator cannot create
accounts; the full invitation lifecycle including expiry, reuse and weak
passwords; that unknown addresses and wrong passwords are indistinguishable;
account lockout; that administrators get **no session** until MFA enrolment
completes; TOTP verification; single-use recovery codes; that MFA secrets are
never stored in the clear; session expiry both idle and absolute; revocation
on logout, password change and suspension; password reset including
single-use tokens and session invalidation; rate limiting per account, and
that exhausting one account's budget does **not** lock out colleagues sharing
an IP.

Account administration: that a display name can be corrected and both the old
and the new one reach the audit log; that a rename changes neither the role
nor the session; that an invitation can be reissued and the earlier link stops
working; that an enrolled authenticator can be removed so the account signs in
with a password alone, that removing it signs the person out everywhere, and
that it is not a way around the policy — a role that requires a second factor
enrols again at its next sign-in. Each of these is refused without the
`users:manage` capability.

### Editorial workflow — 22 tests

Every transition and every refusal: that an editor cannot approve their own
work or publish; that review cannot be skipped; that rejecting requires a
reason; that a version under review cannot be edited. Publication gates:
missing citations, missing review date, open high-severity findings, each
reported together rather than one at a time. That editing published content
opens a new version and leaves the live one untouched, and that exactly one
version stays published. Revision history content, and that it cannot be
rewritten. That drafts are invisible to physicians even when requested
directly.

### Excel import — 21 tests

Against the actual supplied workbook. Column mapping; duplicate upload
rejection; that all 44 rows preview as new **without touching the catalogue**;
that blank cells become `not_supplied` and never `not_applicable`; that
`2mg/kg/day` survives verbatim. Each detector: the NRI/Atomoxetine label
inversion, the "29 gimel" threshold with no quantity, uncited claims, "Blood
pressure" with no direction, repeated class text, high missingness. That
committing stages drafts only and shows nothing to physicians; that high
findings must be acknowledged; that the source file and batch link are kept;
that a batch cannot be committed twice.

### Search — 30 tests

By generic name, trade name, group, class, mechanism, indication and alias.
Typo tolerance: `sertrline` → Sertraline, `flouxetine` → Fluoxetine,
`ritaline` → Methylphenidate, and that nonsense still returns nothing.
Normalisation, Hebrew including final-letter forms, locale fallback with the
displayed language labelled. Highlighting, facets, filters, pagination, and
that unpublished content is unreachable for physicians even when explicitly
requested.

### Preview publication — 12 tests

That publishing past the clinical gates needs **both** the system-wide
allowance and an explicit acknowledgement on the request — neither alone is
enough; that the overridden blockers are stored on the record and appear in
the revision trail, which cannot be rewritten; that the record is flagged in
search results and on its own page; that a record which passed every gate is
*not* flagged; that every client is told the catalogue is in evaluation; that
only an administrator can switch the allowance on; and that the banner appears
on every screen, disappears when switched off, and passes the accessibility
checks.

### Backup and restore — 12 tests

The shipped scripts, against a live database. That the dump is genuinely
encrypted (not merely compressed) and no plaintext is left behind; that every
table returns with matching counts; that clinical text is unchanged; that the
append-only guarantee still holds after a restore; that the extensions and
trigram indexes search depends on come back; that overwriting a populated
database is refused unless forced; that a tampered or missing backup is
rejected.

### End-to-end — 132 tests

Desktop and phone profiles, Hebrew and English. The login wall, sign-out,
MFA enrolment, invitation acceptance and reuse, role visibility and
server-side refusal, search and autocomplete by keyboard, the compact result
list, medication detail, citations, comparison, the review report, the import
screens.

**Accessibility** is tested rather than asserted: axe runs against every major
page for WCAG 2.1 A and AA, plus explicit checks that the skip link is the
first tab stop and moves focus, that the suggestion list is arrow-key operable
and closes on Escape, that headings do not skip levels, that result counts are
announced in a live region, that repeated controls carry per-row accessible
names, that focus indicators are visible, that touch targets are at least
44px, and that nothing scrolls sideways at 360px.

## Defects these tests found

They earned their keep. Each of these was a real bug, fixed:

- Starting MFA enrolment twice generated a second secret, so a QR code the
  user had already scanned no longer matched what was stored and their first
  correct code was rejected.
- The login rate limit applied 10 attempts per 15 minutes **per IP**, which
  would have locked out an entire hospital site behind one NAT gateway.
- Focus moved to the main region on first load as well as on navigation, so
  the very first Tab landed past the skip link.
- A long validation-status badge forced horizontal scrolling at phone width.
- Small buttons were 36px on touch devices.
- Reaching an editorial page without the capability rendered the whole
  interface with every action failing.
- ExcelJS could not read the supplied workbook at all, because its
  SpreadsheetML uses namespace-prefixed elements.

## Notes

**The end-to-end suite raises the rate limits.** It compresses many users'
activity into a handful of accounts, which no real clinician would match. The
limiter itself is covered thoroughly by the API integration tests; raising it
end-to-end stops it masking genuine failures.

**Integration tests share one database** and run serially. They reset between
files, which for the append-only tables requires the same deliberate,
privileged sequence a restore does — the guarantee has no quiet back door,
including for tests.

## Still worth doing before wide rollout

- Manual screen-reader passes (NVDA or JAWS on Windows, VoiceOver on iOS).
  Automated checks catch a real but limited class of problems.
- Testing on physical devices, particularly older Android handsets.
- Usability sessions with clinicians who did not build this.
- A penetration test, if the deployment context calls for one.
- Load testing, once the expected number of concurrent users is known.
