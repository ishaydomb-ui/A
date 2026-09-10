# Decisions taken, and what still needs you

## Part 1 — Decisions you have made

Recorded here so the reasoning behind the current configuration is not lost.
Answered 2026-09-10.

### Settled

| # | Question | Your decision | What was built |
| --- | --- | --- | --- |
| 1 | Authoritative Excel | Not blocking. Publish and test first; initially a single user. | Preview publication mode — see below |
| 2 | Clinical Reviewer | Not needed for MVP. Placeholder for later. | Role kept and enforced; appointment deferred |
| 3 | 140 data-quality findings | Consolidate them; you will fix them in the authoritative Excel | Excel export of every finding |
| 4 | First release | Approved | Catalogue published for evaluation |
| 5 | First account | `ishaydomb@gmail.com` | Created as administrator |
| 6 | Legal wording | The factual draft suffices for MVP | Unchanged, still marked as a draft |
| 7 | Institution name and branding | Leave open | Renders as "(pending approval)" |
| 8 | Contact details | Leave open | Renders as "(pending approval)" |
| 9 | Hosting | A no-cost option | Local Docker stack, no domain or server |
| 10 | Firewall | Your recommendation | Local-only: nothing exposed at all |
| 11 | SMTP | Default (none) | Invitation links shown on screen |
| 12 | Backup destination | Default | `backups/` on the machine; copy off yourself |
| 13 | Retention | Default | 30 days |
| 14 | Product name | Default | "Medication Catalogue" placeholder |

### What decision 1 and 4 required

You asked to publish before any clinical review. That is a reasonable thing to
want for a solo evaluation, but publishing unreviewed clinical content is
precisely what the workflow exists to prevent, so it could not simply be
allowed to happen quietly.

Instead there is now an explicit **preview publication mode**:

- It is a system setting, off by default, changeable only by an administrator.
- Publishing past the clinical gates additionally requires the request to say
  so. Neither the setting alone nor the acknowledgement alone is enough.
- Every record published that way is flagged permanently, and the specific
  blockers that were overridden are stored on the row itself.
- The revision trail records it as `published_unvalidated` with the list of
  what was overridden. That trail cannot be rewritten.
- A banner appears on **every screen** while the mode is on. Each affected
  record carries a "Not clinically reviewed" badge in search results, and a
  warning above its content listing exactly which checks are outstanding.

All 44 records are published this way. When real review begins, switch the
setting off; records that then fail their gates stay flagged until they pass.

Placeholder citations created during development were deleted rather than
left in place — a fabricated source is worse than no source, because it makes
an unchecked claim look sourced. Eighteen were removed and eight findings that
had been closed with a meaningless note were reopened, which is why all 44
records are flagged rather than only 39. The one-off script that did it was
then removed: a tool that deletes citations by matching their title is not
something to leave lying around.

### The consolidated gaps (decision 3)

`data-quality-findings-<date>.xlsx`, downloadable from the Review screen:

- **Summary** — totals by severity and by detector.
- **Findings** — one row per finding: medication, field, the issue, **the
  value the catalogue currently holds**, what is wrong, the recommended
  action, and a blank *Correction* column for you to fill in.
- **By medication** — which drugs have the most problems, worst first.

132 open findings across 44 medications. Fix them in the authoritative Excel
and re-import; the change report will show exactly what your corrections
altered before anything is staged.

### Still outstanding

Nothing blocks you today. These return when the system moves beyond your own
evaluation:

- The authoritative Excel, and re-importing from it.
- Appointing a Clinical Reviewer, and switching preview mode off.
- Binding legal wording, institution name and contact details.
- A server and domain, if you want access from outside your own machine.

## Part 2 — Decisions I took, and why

Documented so you can overturn any of them.

### Clinical-safety decisions

**Blank ≠ not applicable.** A blank cell records *"not supplied"*, which is a
statement about the source document. *"Not applicable"* is a clinical
assertion and can only be set by a person. The interface writes the state out,
so "nobody recorded this" never looks like "this does not apply".

**Nothing is silently corrected.** No spelling fixes, no unit normalisation,
no filled-in blanks, no inferred meanings. `2mg/kg/day` is stored and displayed
exactly as written. Twelve detectors flag suspicious content into a review
report instead.

**Citations are mandatory before publication** for the fields where an
unsourced claim is most dangerous: adult and paediatric indications, adult and
paediatric QTc, contraindications, maximum dose. Change the list if you
disagree with it.

**Approving and publishing are separate acts.** A clinical reviewer judges
correctness; an administrator decides when it goes live. Publication is
refused unless a reviewer approved that exact version, so this divides
responsibility without weakening the guarantee. If you would rather reviewers
publish directly, that is a one-line change to the capability table.

**Comparison excludes long prose.** Indications, side effects and clinical
notes are not shown in the comparison table. Side by side in narrow columns
they are unreadable and invite false equivalence between differently-worded
sources. Dose ranges, onset, QTc and the like are compared.

**No dose calculator and no AI recommender**, as instructed. The source
website's public AI beta is not reproduced; it would need separate clinical
safety, privacy and regulatory review.

**No patient data anywhere.** No field exists for it, search terms are not
retained against an account, and the audit log records who did what — never to
whom.

### Technical decisions

**PostgreSQL with plain SQL, no ORM.** The interesting logic is in the schema;
an ORM would obscure it and add something to migrate away from.

**Search inside Postgres** rather than a separate engine — no second component
to operate, no second copy of clinical data, no way for the two to disagree.

**Fuzzy-match floor of 0.4 word similarity.** Admits a single slip or
transposition (0.47 for `flouxetine`) while rejecting unrelated input (0.1).

**Hebrew and English stored independently.** One is never machine-translated
from the other; that would be inventing clinical content. Where only one
language has text it is shown labelled as such.

**Caddy rather than nginx + certbot** for the proxy — automatic certificate
renewal with no cron job to forget.

**Append-only enforced in the database**, not just the application: a trigger
plus revoked `TRUNCATE`. This holds against the table owner, so a compromised
application role still cannot rewrite history. Verified by tests, including
after a restore.

**Excel reading works around a real incompatibility.** The supplied workbook
uses namespace-prefixed SpreadsheetML (`<x:workbook>`), which the parser reads
as an empty file. The parts are rewritten in memory; the stored original stays
byte-identical for the audit trail.

### Deliberate omissions

- **No analytics or third-party scripts.** A clinical tool behind a login
  should not phone anyone.
- **No "remember me".** Sessions expire on both idle and absolute deadlines.
- **No password complexity rules.** Length-first, per NIST SP 800-63B:
  12 characters minimum, no forced symbols.
- **No email on every event.** Only invitations and password resets.

## Part 3 — Status

**Ready for your evaluation. Not ready for clinical use**, and the interface
says so on every screen.

| Condition | Status |
| --- | --- |
| Runs end to end, at no cost | **Done** — local Docker stack |
| Restore test performed and verified | **Done** — see [backup-and-restore.md](backup-and-restore.md) |
| Catalogue populated and browsable | **Done** — 44 records published for evaluation |
| Data gaps consolidated for correction | **Done** — 132 findings exported to Excel |
| Content checked against an authoritative source | **Not done** — awaiting the official Excel |
| Content approved by a Clinical Reviewer | **Not done** — deferred by decision, no reviewer appointed |

287 automated tests pass, covering authentication, permissions, search, Excel
import, review, publication, preview mode, revision history, backup and
restore, and WCAG 2.1 AA accessibility on desktop and phone in both
languages.

Because the last two rows are outstanding, every record shows a warning that
it has not been clinically reviewed, and a banner across the top of every
screen says the catalogue must not be used for clinical decisions. Those
disappear on their own once records pass their gates and preview mode is
switched off — nothing has to be remembered.
