# Administrator guide

## Roles

Permissions are capabilities, not a ladder. Every one of them is checked in
the API on every request; the interface only uses them to decide which
controls to show, so nothing is gained by tampering with the browser.

| | Physician | Clinical Reviewer | Editor | Admin |
| --- | :-: | :-: | :-: | :-: |
| Read published catalogue | ✓ | ✓ | ✓ | ✓ |
| Read drafts and unpublished content | | ✓ | ✓ | ✓ |
| Create and edit drafts | | | ✓ | ✓ |
| Submit for clinical review | | | ✓ | ✓ |
| Approve or reject clinically | | ✓ | | ✓ |
| Publish | | | | ✓ |
| Prepare an import | | | ✓ | ✓ |
| Commit an import to drafts | | | | ✓ |
| Read the review report | | ✓ | ✓ | ✓ |
| Close a review finding | | ✓ | | ✓ |
| Manage users | | | | ✓ |
| Read the audit log | | | | ✓ |

Two separations are deliberate:

- **An editor cannot approve their own work.** Approval requires the clinical
  review capability, which editors do not have.
- **Approving and publishing are different acts.** A clinical reviewer decides
  the content is correct; an administrator decides it goes live. Publication
  is refused unless a reviewer has approved the exact version being published,
  so this splits responsibility without weakening the guarantee.

## Two-factor authentication

Mandatory for administrators. An administrator gets **no session at all**
until enrolment is complete — signing in with the correct password only
produces a challenge.

No other role is asked for it. There is no way for them to turn it on either:
enrolment is offered only where the policy demands it, so a physician or a
clinical reviewer signs in with a password alone.

Ten single-use recovery codes are issued at enrolment and shown once.

**Remove**, beside the MFA column in the accounts table, clears an enrolled
authenticator. Use it when someone has lost both their authenticator and their
recovery codes, or when an account that no longer needs a second factor is
still being challenged for one — enrolment challenges an account at every
sign-in whatever its role, so an account enrolled while it was an
administrator keeps being challenged after it is moved to another role.
Removing it signs the person out everywhere. It is not a way around the
policy: a role that requires a second factor simply enrols again at its next
sign-in.

## Adding people

There is no public registration and no self-service path. Accounts exist only
because an administrator created them.

1. **Users → Invite a clinician.** Enter the address, full name and role.
2. The system creates the account in an `invited` state — it has no password
   and nothing can sign in as it.
3. An invitation email is sent if SMTP is configured. If it is not, the screen
   shows a single-use link to pass on yourself. Nothing is silently lost
   either way.
4. The person opens the link, sets a password, and the account becomes
   `active`. Opening that link proves control of the mailbox, so it also
   verifies the address.

Invitation links expire (72 hours by default) and work once. **Re-invite**, in
the accounts table beside anyone still `invited`, issues a fresh one and
revokes the earlier link.

A link also carries the address the system is configured with, so one created
before the site had an address a colleague can open points at `localhost` and
will not work for them. Re-invite once the real address is in place.

### Choosing a role

Give people the least they need. Most clinicians want **Physician** — read
access to published content, nothing more. Reserve **Admin** for the small
number of people who genuinely administer the system, since it carries user
management and publication.

### Correcting a name

Names get entered in a hurry, and a reviewer's name appears on the approvals
they sign. **Users → Rename**, next to the name in the accounts table, changes
it; Escape or **Cancel** leaves it as it was. The previous name and the new one
are both written to the audit log.

Renaming is not a change of privilege, so it does not sign the person out — a
reviewer part-way through a record keeps their session. Re-running the
bootstrap script will *not* rename an existing account; it deliberately leaves
the display name alone.

## Suspending and removing access

From the Users screen:

- **Suspend** — blocks sign-in and revokes every active session immediately.
  Reversible.
- **Deactivate** — for people who have left.

Changing someone's role also revokes their sessions, so a demotion takes
effect at once rather than at the next natural expiry.

Accounts are not deleted. The audit log and revision history reference them,
and those records are append-only by design.

The system refuses to leave itself unadministrable: the last active
administrator cannot be demoted or disabled, and nobody can disable their own
account.

## The audit log

Every security-relevant event is recorded with the actor, action, entity, IP
address and time: sign-ins and failures, lockouts, MFA changes, password
resets, invitations, role changes, and every content transition.

It is **append-only, enforced in the database**. A trigger blocks `UPDATE` and
`DELETE`, and the `TRUNCATE` privilege is revoked — this holds against the
table owner, not merely against the application. The same protection applies
to the revision history.

Read it under **Users → Audit log**, filtered by action or actor.

## Monitoring

| Endpoint | Access | Purpose |
| --- | --- | --- |
| `/healthz` | open | Liveness. Never touches the database. |
| `/readyz` | open | Readiness: database reachable and schema applied. 503 otherwise. |
| `/api/admin/status` | admin | Editorial workload, open findings, active sessions, last import. |

Point an uptime monitor at `/readyz`. Logs are structured JSON on stdout, with
credentials, tokens and MFA secrets redacted; collect them with whatever you
already use.

Watch for:

- `auth.account_locked` in bursts — someone is guessing passwords.
- Rising `openFindings.high` — imported data is waiting on clinical review.
- `readyz` failing — the database is unreachable or a migration has not run.

## What must never be entered

**No patient-identifiable information, anywhere.** There is no field for it,
search terms are not retained against an account, and the audit log is
designed to record who did what — never to whom. Make this explicit when you
brief users.

## Routine tasks

| Task | Where |
| --- | --- |
| Invite or suspend a user | Users |
| Reset someone's second factor | Users → Reset MFA |
| See what is awaiting review | Review → Awaiting a decision |
| See unresolved data-quality findings | Review → Data quality findings |
| Import a new workbook | Imports — see [import-guide.md](import-guide.md) |
| Publish approved content | Review queue → open the record → Publish |
| Export every data-quality finding | Review → Download all findings (.xlsx) |
| Turn preview publication on or off | `PUT /api/settings/publication.allow_unvalidated` |
| Check backups ran | `docker compose logs backup` |
| Practise a restore | [backup-and-restore.md](backup-and-restore.md) |
