# Backup and restore

A backup that has never been restored is not a backup. This document
describes both, and the restore procedure is exercised automatically by the
test suite — twelve tests run these exact scripts against a live database.

## What is backed up

| | How |
| --- | --- |
| **The database** | `pg_dump` custom format, compressed, encrypted with AES-256 |
| **Uploaded workbooks** | Archived alongside — they are the audit trail for every import |
| **Secrets** | **Not backed up.** Keep `.env` somewhere safe yourself. |
| **Certificates** | Not backed up; reissued automatically |

## The schedule

The `backup` service runs nightly at 02:00 UTC by default
(`BACKUP_SCHEDULE`). Each run:

1. Dumps the database, and fails loudly if the result is implausibly small.
2. Encrypts it with `BACKUP_PASSPHRASE` (AES-256, SHA-512 key derivation).
   The passphrase is passed on a file descriptor, never as a command-line
   argument where it would be visible in the process list.
3. **Verifies the encrypted file decrypts and parses as a valid dump.** A
   backup that fails this is kept for inspection and the run reports failure.
4. Writes a SHA-256 checksum alongside it.
5. Archives the uploaded workbooks.
6. Deletes backups older than `BACKUP_RETENTION_DAYS` (30 by default).

Nothing is pruned until the new backup has verified.

## Taking one now

```bash
docker compose exec backup /usr/local/bin/backup.sh
ls -la backups/
```

## Copy them off the server

The scripts write to `backups/` on the host. A backup that lives only on the
machine it protects does not protect against losing that machine. Copy them
somewhere else on a schedule:

```bash
# Example: pull to another machine over SSH.
rsync -az --delete server:/opt/medcatalogue/backups/ /secure/offsite/medcat/
```

Because the files are already encrypted, the destination does not need to be
trusted with the contents — but it must not hold `BACKUP_PASSPHRASE`.

## Restoring

```bash
docker compose exec backup /usr/local/bin/restore.sh \
  /backups/medcat-20260910T020000Z.dump.gpg  medcat
```

The script:

- verifies the checksum before touching anything,
- **refuses** to overwrite a database that already contains tables, unless
  `RESTORE_FORCE=1` is set explicitly,
- decrypts and restores,
- reports what came back: medications, users, revisions, audit entries and
  the schema version.

Refusing by default is deliberate. An accidental restore over a live
catalogue would destroy the revision history the system exists to protect.

### Restoring over a live database

```bash
docker compose stop api
docker compose exec -e RESTORE_FORCE=1 backup \
  /usr/local/bin/restore.sh /backups/<file>.dump.gpg medcat
docker compose start api
curl -fsS https://<hostname>/readyz
```

## The restore drill

**Do this before going live, and repeat it quarterly.** It restores into a
scratch database, so it cannot harm production.

```bash
# 1. Take a fresh backup.
docker compose exec backup /usr/local/bin/backup.sh

# 2. Restore it into a database that is not the live one.
docker compose exec backup /usr/local/bin/restore.sh \
  /backups/<newest>.dump.gpg  medcat_drill

# 3. Check the data is really there.
docker compose exec db psql -U medapp -d medcat_drill -c \
  "SELECT count(*) AS medications FROM medications;
   SELECT count(*) AS published FROM medication_versions WHERE state = 'published';
   SELECT count(*) AS revisions FROM revisions;
   SELECT count(*) AS audit FROM audit_log;"

# 4. Check the append-only guarantee survived. Both must fail.
docker compose exec db psql -U medapp -d medcat_drill -c "UPDATE audit_log SET action='x';"
docker compose exec db psql -U medapp -d medcat_drill -c "DELETE FROM revisions;"

# 5. Clean up.
docker compose exec db dropdb -U medapp medcat_drill
```

Record the date, the backup used, the counts and who ran it. A drill nobody
wrote down did not happen.

### Result of the drill run during development

Restoring a 44-medication catalogue into a fresh database:

```
[restore] checking the file has not been altered
[restore] checksum ok
[restore] creating database 'medcat_restore_test'
[restore] decrypting and restoring into 'medcat_restore_test'
[restore] verifying the restored data
[restore] restored: 44 medications, 10 users, 59 revisions, 10 audit entries, 7 migrations
[restore] done
```

Also verified: the append-only triggers still rejected `UPDATE` and `DELETE`
afterwards; `pg_trgm` and `unaccent` and the trigram indexes came back; the
API started against the restored database and still required authentication;
overwriting a populated database was refused; and a deliberately altered
backup was rejected on its checksum.

## If the passphrase is lost

The backups cannot be decrypted. There is no recovery path — that is what
encryption means. Keep `BACKUP_PASSPHRASE` in a password manager or a sealed
envelope, separate from the server.

The same applies to `APP_SECRET`: losing it invalidates every session and
every enrolled authenticator, though the data itself survives.

## Retention

Thirty days by default. Choose a figure with whoever is accountable for the
data, and consider whether a longer-term archive is needed — the audit log and
revision history are permanent records, and a backup is the only way to
recover them if the database is lost.
