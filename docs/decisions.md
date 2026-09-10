# Decisions taken, and what still needs you

## Part 1 — Things only you can decide

These are outside what I can settle from inside the project. Grouped so they
can be answered in one pass.

### A. Legal and identity

1. **Privacy Policy and Terms of Use.** Both pages currently show a *factual
   description of what the system actually does* — what it stores, why, who
   can see it, how long it is kept — clearly labelled as a draft awaiting your
   approval. That description is accurate and is what a lawyer needs in order
   to draft binding text. **I have not written binding legal wording**, and the
   pages say so. Send me the approved text and I will store it; the pages
   switch to it automatically.

2. **Institution name and branding.** The source website's footer referenced
   Sheba Medical Center, which its own audit flagged as unverified. Nothing in
   this system claims any institutional affiliation: the name renders as
   *"(pending approval)"* until you set it. Confirm in writing what name, logo
   and wording may be used, and whether you hold authorisation to use them.

3. **Contact details.** Which address should appear in the footer and on the
   legal pages.

4. **The product name.** It is currently "Medication Catalogue" / "קטלוג
   תרופות" — a placeholder, not a decision.

### B. Infrastructure and money

5. **Server and domain.** A Contabo VPS and a hostname have to be bought and
   the DNS pointed at it. Both are purchases and a DNS change, so both are
   yours. Recommended: 2 vCPU / 4 GB / 40 GB, Debian 12.

6. **Firewall.** Ports 80 and 443 open inbound; SSH restricted to known
   addresses if possible.

7. **SMTP credentials**, if you want invitations and password resets
   delivered by email. Without them the system works but writes messages to an
   outbox and shows the administrator a link to pass on by hand — deliberately,
   so a new deployment cannot surprise anyone with unexpected mail.

8. **Where backups are copied to.** They are encrypted, so the destination
   need not be trusted with the contents — but it must not hold the
   passphrase.

9. **Retention period.** 30 days by default. Whoever is accountable for the
   data should set this.

### C. Clinical

10. **The authoritative Excel.** The workbook supplied is the *website
    snapshot*, and its own Overview sheet states it has not been clinically
    validated. It is loaded as drafts only. The official Excel is still
    outstanding, and nothing should be published until records are reconciled
    against it.

11. **140 open data-quality findings** were raised by importing the snapshot —
    68 high, 50 medium, 22 low. Each needs a clinical decision. The
    high-severity ones block publication until closed. Notable ones:

    - **"NRI" appears as a generic name while "Atomoxetine" sits in the trade
      names column.** Almost certainly inverted; I have not corrected it.
    - **"In Israel below the age of 6 and above requires 29 gimel"** — a dose
      threshold is missing from the sentence. The equivalent row for
      immediate-release methylphenidate says "above 90 mg", so the number was
      probably dropped, but I have not filled it in.
    - **Guanfacine ER clinical notes contain "shor tabechnic"** and mixed
      Hebrew/English regulatory wording.
    - **QTc and indication claims have no citations at all.**
    - **"Blood pressure" is listed as a side effect with no direction.**
    - **Starting age is blank in 35 of 44 records; monitoring tests in 41 of
      44.** Each needs deciding: unknown, not applicable, or simply not
      supplied by this source?

12. **Who the Clinical Reviewer is.** Publication requires a named person with
    that role. Nothing reaches clinicians without one.

### D. Release

13. **First public release** — putting the system in front of real clinicians
    — is yours to authorise.

14. **The first invitations.** These are emails to real people, so I have sent
    none and will not without your say-so.

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

**Not yet ready for clinical use**, by the criteria in your own brief. Two
conditions remain:

| Condition | Status |
| --- | --- |
| Restore test performed and verified | **Done** — see [backup-and-restore.md](backup-and-restore.md) |
| All published clinical content approved by a Clinical Reviewer | **Not done** — no reviewer appointed, no authoritative Excel |

Everything else is built and tested: 275 automated tests pass, covering
authentication, permissions, search, Excel import, review, publication,
revision history, backup and restore, and WCAG 2.1 AA accessibility on
desktop and phone in both languages.

The system is ready to receive the authoritative Excel and a clinical
reviewer. Until both arrive, the catalogue holds unvalidated draft content
that is invisible to clinicians and marked as unvalidated wherever it appears.
