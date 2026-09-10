# Review and publication

## The lifecycle

```
   Draft ──submit──> In clinical review ──approve──> Approved ──publish──> Published
     ^                       │                                                 │
     │                       │ request changes (reason required)               │
     └───────── Changes requested <─────────────────────────────────┘ edit ────┘
```

| State | Who sees it | Who can move it on |
| --- | --- | --- |
| **Draft** | Editors, reviewers, admins | Editor submits for review |
| **In clinical review** | Editors, reviewers, admins | Reviewer approves or requests changes |
| **Changes requested** | Editors, reviewers, admins | Editor revises and resubmits |
| **Approved** | Editors, reviewers, admins | Admin publishes |
| **Published** | **Everyone signed in** | Editor opens a new draft; admin archives |
| **Archived** | Editors, reviewers, admins | — |

Only **Published** content reaches physicians. That is enforced in the API:
search, the detail page and the comparison view all filter by state, and a
physician who asks for a draft directly is refused rather than served.

## Why approving and publishing are separate

A clinical reviewer judges whether the content is medically correct. An
administrator decides when it goes live. Publication is refused unless a
reviewer has approved *that exact version*, so splitting the two divides
responsibility without weakening the guarantee.

An editor can do neither: they cannot approve their own work, and they cannot
publish.

## What blocks publication

Publication is refused, with **every** blocker listed at once rather than one
at a time, while any of these hold:

| Blocker | Why |
| --- | --- |
| **Not clinically approved** | No reviewer has approved this version. |
| **Missing review date** | The content carries no date it was reviewed against a source. |
| **Open high-severity findings** | An unresolved data-quality question about this record. |
| **Missing citation** | A clinical claim with no source attached. |

Citations are mandatory before publication for the fields where an unsourced
statement is most dangerous: adult and paediatric indications, adult and
paediatric QTc, contraindications, and maximum dose.

## Reviewing a record

1. **Review → Awaiting a decision** lists everything in flight, with the count
   of findings blocking each.
2. Open the record. It shows the full content, its validation status, its
   source, and the citations attached to each claim.
3. Compare against the authoritative source.
4. Either **Approve**, or **Request changes** with a reason. The reason is
   mandatory and is recorded — a rejection without an explanation is not a
   review.

## Closing a data-quality finding

Findings raised during import stay open until a reviewer decides. Each records
the evidence and a recommended action.

Closing one requires a note explaining how it was resolved. This is a clinical
decision recorded against your account, so it is answerable later:

- **Resolved** — the underlying issue has been addressed.
- **Won't fix** — the flagged text is correct as it stands, and the note says
  why.
- **Acknowledged** — seen, work in progress. Still blocks publication.

## Attaching a source

Each citation carries the title, an optional document reference and URL, a
page or section, the jurisdiction it applies in, an approval status
(**approved**, **off-label**, **unknown**, **not applicable**) and the date it
was reviewed.

Recording approval status per claim and per jurisdiction is what makes it
possible to state that a use is off-label in one country without asserting it
everywhere.

## Editing something already published

Published content is never edited in place. Opening a draft creates a **new
version** branched from the published one, carrying its citations. The live
record stays exactly as it is until the replacement completes review and is
published, at which point the old version is archived. Exactly one version of
a medication is published at any moment, enforced by the database rather than
by convention.

## Revision history

Every record keeps a complete trail: who acted, when, what changed field by
field with before and after in each language, which state it moved between,
and any reason given.

It is **append-only, enforced in the database**, so it cannot be rewritten —
including by an administrator.

Open it from a medication's page, or under **Review**.

## Before declaring the catalogue ready for clinical use

Two conditions, and neither is a formality:

1. **A restore has actually been performed** from an encrypted backup, and
   verified. See [backup-and-restore.md](backup-and-restore.md).
2. **Every published record has been approved by a clinical reviewer** against
   the authoritative Excel — not the website snapshot.

Until then, records carry their validation status openly, and anything marked
*Unvalidated* shows a warning above its content telling the reader not to rely
on it.
