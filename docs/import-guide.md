# Importing and updating the Excel workbook

The Excel workbook is the source of truth. This is how its contents reach the
catalogue.

## The rule that shapes everything here

**Uploading a workbook never changes what a clinician sees.** Rows are staged,
compared against what is already published, and committed as *drafts*. Those
drafts still have to pass clinical review before anything is published. There
is no path from "file uploaded" to "live" that does not cross a human
decision.

A second rule follows from the first: **nothing is silently corrected**. The
importer does not fix spelling, normalise units, fill in blanks, or infer what
a field probably meant. Anything questionable becomes a finding in the review
report for a person to decide.

## The four steps

### 1. Upload

**Imports → Upload a workbook.** The file is stored verbatim and addressed by
its own SHA-256 hash, so the audit trail can always point at exactly what was
imported. Uploading the same file twice is refused.

The system reads the workbook's structure and suggests which sheet holds the
catalogue.

### 2. Confirm the column mapping

A mapping is suggested from the column headers, in Hebrew or English. **It is
only a suggestion, and it is never applied on its own.** Check every row of
the mapping table before continuing: a mis-titled column mapped to the wrong
field would put one drug's text into another's clinical record.

Columns you do not want are set to *Do not import*. A field can be fed by only
one column; if two columns match the same field, the second is left unmapped
for you to resolve.

`generic_name` must be mapped — a row without a name cannot be identified.

### 3. Review the change report

This is the important screen. It shows, for every row:

| Action | Meaning |
| --- | --- |
| **new** | No such medication in the catalogue yet. |
| **changed** | Exists, and this workbook differs. Expand to see field-by-field before and after. |
| **unchanged** | Exists and matches. Nothing will happen. |
| **duplicate** | Another row in *this workbook* names the same medication. |
| **error** | The row has no usable name and cannot be imported. |

Alongside it are the data-quality findings, graded high, medium and low.
Twelve detectors run, among them:

- **Probable label inversion** — a drug class abbreviation sitting in the
  generic-name column while the trade-names column holds something that reads
  like a generic name.
- **Incomplete threshold** — a sentence that states a threshold without a
  quantity, such as "above requires 29 gimel" where a dose was dropped.
- **Claims lack citations** — a QTc or indication statement with no source.
- **Ambiguous wording** — a bare parameter such as "Blood pressure" listed as
  a side effect, with no direction.
- **Repeated class text** — identical text on several drugs, which may be
  intentional class-level content or may be a copy-paste error.
- **Mixed hierarchy** — a label used as both a therapeutic group and a drug
  class.
- **High missingness** — a field left blank across most of the workbook.
- **Duplicate rows**, **mixed-language values**, **unit formatting**, **missing
  values**, **unstructured indications**.

None of these change anything. They are questions for a person.

**Download them all as a workbook.** The Review screen offers *Download all
findings (.xlsx)*: one row per finding with the medication, the field, what is
wrong, **the value the catalogue currently holds**, the recommended action and
a blank *Correction* column. Work through it, fix the underlying data in the
authoritative Excel, and re-import — the change report will then show exactly
what your corrections altered.

### 4. Stage as drafts

Only an administrator can commit. If the batch raised high-severity findings,
you must confirm you have seen them first — and they keep blocking
publication until a clinical reviewer closes each one.

Committing:

- creates a draft version for every new and changed row,
- links each version to the batch it came from,
- attaches findings to the records they concern,
- leaves the published catalogue exactly as it was.

## Updating with a newer workbook

The same four steps. On the third, the change report is the point: it tells
you precisely what a newer workbook would alter before anything is staged.

Updates **merge rather than overwrite**. A field the new workbook does not
supply does not erase what the catalogue already holds — a shorter export
cannot quietly delete clinical content.

Changed records become drafts branched from the published version. The
published version stays live and untouched until the new one is approved and
published in its place.

## How blank cells are treated

A blank cell records **"not supplied"** — a factual statement about the source
document. It is *not* read as "not applicable", which is a clinical assertion
only a reviewer may make. The four states are:

| State | Meaning |
| --- | --- |
| *(value)* | The source supplied this text. Stored verbatim. |
| **Not supplied** | The source had nothing here. |
| **Unknown** | Genuinely undetermined; recorded deliberately. |
| **Not applicable** | A reviewer has asserted this does not apply. |

Clinicians see the state written out, so "nobody recorded this" never looks
like "this does not apply".

## Two languages

Each import loads one language. Hebrew and English are stored as independent
records of what a source said — one is never machine-translated from the
other, because that would be inventing clinical content. To load both, import
the workbook twice, choosing the language each time.

Where only one language has content, it is shown with a label saying which
language it is in, rather than appearing as a translation.

## The website snapshot

The supplied workbook is an extract from a public website, and its own
Overview sheet says it has not been clinically validated. It is loaded as
**seed data only**, with every record marked *"Unvalidated website snapshot"*
and left as a draft.

```bash
pnpm --filter @med/api exec tsx src/scripts/seed.ts path/to/workbook.xlsx
```

Loading it produced 44 draft records and 140 findings — 68 high, 50 medium, 22
low. None of it is visible to clinicians, and none of it should be published
until it has been reconciled against the authoritative Excel and approved.

## Discarding a batch

An uploaded or validated batch that has not been committed can be discarded.
Committed batches cannot: the drafts they created and the trail linking them
are part of the record.

## If an import goes wrong

Nothing published is at risk — imports only create drafts. To back out:

1. Leave the drafts unpublished, or archive them.
2. The published version is unchanged and still live.
3. The batch, its rows and its findings stay in the audit trail.
