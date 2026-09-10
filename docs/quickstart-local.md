# Running it on your own machine

No server, no domain, no cost. Everything binds to `127.0.0.1`, so nothing is
reachable from another machine and there is no firewall rule to set.

This is the right shape for evaluating the catalogue on your own before
deciding whether to put it on a server.

## What you need

Docker Desktop (macOS or Windows) or Docker Engine (Linux). Nothing else.

## Start it

```bash
git clone <repository-url> medcatalogue
cd medcatalogue

# One secret. Keep it: losing it signs everyone out and voids enrolled
# authenticators.
echo "APP_SECRET=$(openssl rand -base64 48)" > .env
echo "POSTGRES_PASSWORD=$(openssl rand -base64 24)" >> .env

docker compose -f docker-compose.local.yml up -d --build
```

Apply the schema:

```bash
docker compose -f docker-compose.local.yml exec api node dist/db/migrate.js up
```

Create your account:

```bash
docker compose -f docker-compose.local.yml exec \
  -e BOOTSTRAP_EMAIL='you@example.org' \
  -e BOOTSTRAP_NAME='Your Name' \
  -e BOOTSTRAP_PASSWORD='<a long passphrase>' \
  api node dist/scripts/create-admin.js
```

Load the catalogue from a workbook:

```bash
docker compose -f docker-compose.local.yml cp workbook.xlsx api:/tmp/
docker compose -f docker-compose.local.yml exec api node dist/scripts/seed.js /tmp/workbook.xlsx
```

Open **http://localhost:8080**.

As an administrator you will be asked to set up two-factor authentication
before you get a session — any authenticator app will do. **Save the ten
recovery codes.**

## Publishing before clinical review

Imported records are drafts, so the catalogue starts empty from a reader's
point of view. To browse it during evaluation, publish them in preview mode:

```bash
docker compose -f docker-compose.local.yml exec api \
  node dist/scripts/publish-for-evaluation.js
```

This is deliberate and visible, not a bypass. Every record is flagged as not
clinically reviewed, each one lists the checks it did not pass, and a banner
across every screen says the catalogue must not be used for clinical
decisions.

Switch it off once real review begins:

```bash
docker compose -f docker-compose.local.yml exec db \
  psql -U medapp -d medcat -c \
  "UPDATE settings SET value = 'false' WHERE key = 'publication.allow_unvalidated';"
```

Records that then fail their gates stay flagged until they pass.

## Backups

Nothing is scheduled in the local stack, so take one when it matters:

```bash
docker compose -f docker-compose.local.yml exec db \
  pg_dump -U medapp --format=custom medcat > backup-$(date +%Y%m%d).dump
```

For encryption, retention and a tested restore, use the full stack's backup
service — see [backup-and-restore.md](backup-and-restore.md).

## Everyday commands

```bash
docker compose -f docker-compose.local.yml logs -f api    # logs
docker compose -f docker-compose.local.yml ps             # status
docker compose -f docker-compose.local.yml down           # stop, keep data
docker compose -f docker-compose.local.yml down -v        # stop and DELETE data
```

## Reaching it from another device

The local stack is deliberately unreachable from the network. Two ways to
change that, without paying for a server:

**Cloudflare Tunnel (free tier).** Gives a public HTTPS hostname pointing at
the machine, with no inbound port opened and no fixed IP needed. Suitable for
letting a colleague try it; it does mean the catalogue is reachable from the
internet, so do not do it before you are content with the access controls.

**On the same network.** Change the port bindings in
`docker-compose.local.yml` from `127.0.0.1:8080` to `8080`, and set
`PUBLIC_URL` to the machine's LAN address. Note the trade-off: the API refuses
to run without TLS on anything other than a loopback address, so you would
need `COOKIE_SECURE` handling and a certificate. For anything beyond one
machine, the full stack in [deployment.md](deployment.md) is the honest
answer.

## When to move to a server

Move when any of these becomes true:

- More than one person needs access.
- It must be reachable when your machine is off.
- Real clinical content is being published.
- You need the nightly encrypted backups and retention.

[migration.md](migration.md) covers moving the data across. Nothing is tied to
this machine: it is a repository, an `.env` file and a database dump.
