# Moving to another server

The system is deliberately provider-neutral. There is nothing to migrate
except three things:

1. **The repository** — the application and its configuration.
2. **The `.env` file** — the secrets.
3. **A database dump** — the data.

There is no vendor SDK, no proprietary hosting service, no managed platform
in the path. Anything that runs Docker will run this.

## What is where

| Thing | Lives in | Moves how |
| --- | --- | --- |
| Application code | Git repository | `git clone` |
| Schema | `apps/api/migrations/*.sql` | applied by the migration runner |
| Data | Postgres volume `db-data` | encrypted dump, restored |
| Uploaded workbooks | Volume `import-data` | archived with the nightly backup |
| Secrets | `.env` | copy by hand, over a secure channel |
| Certificates | Volume `caddy-data` | **do not copy** — reissued automatically |

## Procedure

### 1. On the old server: take a final backup

```bash
cd /opt/medcatalogue
docker compose exec backup /usr/local/bin/backup.sh
ls -la backups/
```

For a clean cut-over, stop the API first so nothing is written mid-dump:

```bash
docker compose stop api
docker compose exec backup /usr/local/bin/backup.sh
```

### 2. Copy what is needed

```bash
scp backups/medcat-<timestamp>.dump.gpg* newhost:/tmp/
scp backups/imports-<timestamp>.tar.gz.gpg newhost:/tmp/     # if present
scp .env newhost:/tmp/env-backup                              # secrets: use a secure channel
```

### 3. On the new server: bring up the stack

Follow [deployment.md](deployment.md) steps 1–4, reusing the **same `.env`**.

Keeping `APP_SECRET` identical means sessions and enrolled authenticators keep
working. Changing it signs everyone out and forces every user to re-enrol MFA
— occasionally what you want, but be deliberate about it.

Start only the database at first:

```bash
docker compose up -d db
```

### 4. Restore the data

```bash
docker compose cp /tmp/medcat-<timestamp>.dump.gpg backup:/backups/
docker compose cp /tmp/medcat-<timestamp>.dump.gpg.sha256 backup:/backups/
docker compose up -d backup

docker compose exec backup /usr/local/bin/restore.sh \
  /backups/medcat-<timestamp>.dump.gpg medcat
```

The restore verifies the checksum, refuses to overwrite a database that
already holds data unless `RESTORE_FORCE=1` is set, and prints what it
restored.

### 5. Restore the uploaded workbooks

These are the audit trail for every import, so they are worth keeping:

```bash
docker compose cp /tmp/imports-<timestamp>.tar.gz.gpg api:/tmp/
docker compose exec api sh -c \
  'gpg --batch --quiet --passphrase-fd 0 --pinentry-mode loopback \
       --decrypt /tmp/imports-<timestamp>.tar.gz.gpg | tar -xzf - -C /data'
```

### 6. Start everything and verify

```bash
docker compose up -d
docker compose exec api node dist/db/migrate.js up      # no-op if already current
curl -fsS https://<new-hostname>/readyz
```

Then check, as an administrator:

- the medication count matches the old server,
- revision history is intact on a record you know,
- the audit log still contains historical entries,
- search returns results, including a deliberate misspelling.

### 7. Switch DNS

Lower the record's TTL a day in advance, then repoint it. Caddy issues a
certificate for the new server automatically on first request.

Keep the old server running, but with its API stopped, until you are satisfied
— it is your rollback.

### 8. Decommission

Only once you have confirmed a good backup **from the new server**:

```bash
docker compose down -v      # -v destroys the data volumes; be certain
```

## Moving to managed PostgreSQL

To use a managed database (RDS, Cloud SQL, a provider's Postgres) instead of
the bundled container:

1. Create the database and a user that owns it.
2. Point `DATABASE_URL` at it and set `DATABASE_SSL=true`.
3. Remove the `db` service from `docker-compose.yml`, and the `depends_on: db`
   from `api` and `backup`.
4. Run the migrations.

The schema needs the `pgcrypto`, `pg_trgm` and `unaccent` extensions. Most
managed providers allow these; confirm before committing to one.

Point the backup container at the managed host by setting `PGHOST`, `PGUSER`
and `PGPASSWORD` — the scripts do not care where the database lives.

## Running the API without Docker

Nothing requires containers:

```bash
pnpm install --frozen-lockfile
pnpm --filter @med/shared build
pnpm --filter @med/api build
pnpm --filter @med/web build            # static files for any web server
node apps/api/dist/server.js            # reads the same environment variables
```

Serve `apps/web/dist` from any static host, and proxy `/api` to the API
process.
