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

ADMIN_EMAIL='you@example.org' ADMIN_NAME='Your Name' ./ops/local-up.sh
```

That is the whole thing. The script generates the secrets, builds and starts
the containers, waits for the API, creates your account, loads the catalogue
and publishes it for evaluation. It prints the URL and the password at the
end, and is safe to re-run.

Open **http://localhost:8080**.

If it fails it shows the container logs and status, which normally says why.

### Doing it by hand

If you would rather run the steps yourself:

```bash
echo "APP_SECRET=$(openssl rand -base64 48)" >> .env
echo "POSTGRES_PASSWORD=$(openssl rand -base64 24)" >> .env

docker compose -f docker-compose.local.yml up -d --build
# The schema is applied automatically at start-up in this stack.

docker compose -f docker-compose.local.yml exec \
  -e BOOTSTRAP_EMAIL='you@example.org' \
  -e BOOTSTRAP_NAME='Your Name' \
  -e BOOTSTRAP_PASSWORD='<a long passphrase>' \
  api node dist/scripts/create-admin.js

# The website-snapshot workbook is mounted at /seed.
docker compose -f docker-compose.local.yml exec api \
  node dist/scripts/seed.js /seed/medication_catalogue_central_export.xlsx
```

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

## Checking on it

```bash
./ops/local-status.sh
```

Shows the containers, whether the site answers, the accounts that exist, how
many records are published, the open findings, and the last of the API log.

## If it does not come up

```bash
docker compose -f docker-compose.local.yml ps          # what is running
docker compose -f docker-compose.local.yml logs api    # nearly always says why
curl -i http://localhost:8080/readyz                   # expect {"status":"ready"...}
```

## If you lost the generated password

`create-admin` updates an existing account, so run it again with a password
you choose:

```bash
docker compose -f docker-compose.local.yml exec \
  -e BOOTSTRAP_EMAIL='you@example.org' \
  -e BOOTSTRAP_NAME='Your Name' \
  -e BOOTSTRAP_PASSWORD='a-long-passphrase-you-pick' \
  api node dist/scripts/create-admin.js
```

Common causes:

| Symptom | Cause |
| --- | --- |
| `Cannot connect to the Docker daemon` | Docker Desktop is not running |
| `docker-compose: command not found` | This needs Compose v2 — `docker compose`, two words |
| API exits at start-up | `APP_SECRET` missing or under 32 characters — check `.env` |
| `port is already allocated` | Something else holds 8080; change the port mapping |
| Site loads, but empty catalogue | The seed and publish steps have not been run |

## Everyday commands

```bash
docker compose -f docker-compose.local.yml logs -f api    # logs
docker compose -f docker-compose.local.yml ps             # status
docker compose -f docker-compose.local.yml down           # stop, keep data
docker compose -f docker-compose.local.yml down -v        # stop and DELETE data
```

## Reaching it from another device

The stack binds to `127.0.0.1`, which means **the machine it runs on**. If that
machine is a server you reach over SSH, then `http://localhost:8080` in your
own browser points at your own computer or phone, not at the server, and
nothing will answer. That is the binding doing its job, not a fault.

Three ways to change it, in order of how exposed they leave you.

### 1. SSH tunnel — nothing is exposed

On the server:

```bash
./ops/ssh-tunnel.sh
```

It closes any public tunnel, makes sure the app is configured for loopback
access, checks that it answers, and prints the exact command to run — with
your username and the server's address already filled in.

Then, **on your own machine and not inside the SSH session**:

```bash
ssh -N -L 8080:127.0.0.1:8080 user@your-server
```

Leave that window open and browse to `http://localhost:8080`. The traffic runs
inside your SSH connection; nothing is opened to the internet, and closing the
window ends the access. `-N` just means "no remote shell, only the forward".

If 8080 is already taken on your machine, forward a different local port —
`-L 8090:127.0.0.1:8080` — and browse to `http://localhost:8090`.

Phone SSH clients such as Termius offer the same thing under **Port
Forwarding**: local port 8080, destination `127.0.0.1`, remote port 8080.

> One thing worth knowing: over a tunnel the browser sees plain
> `http://localhost`, and some browsers — Safari among them — refuse to store a
> cookie marked `Secure` on a plain connection. Sign-in would then appear to
> succeed while leaving you signed out. `ops/ssh-tunnel.sh` sets
> `COOKIE_SECURE=false` and `PUBLIC_URL=http://localhost:8080` for you, which
> is safe precisely because loopback traffic never touches a network.

### 2. A temporary public address — for a phone

```bash
./ops/expose-tunnel.sh
```

Downloads the Cloudflare tunnel client, opens a quick tunnel, points the app
at the resulting HTTPS address, turns the session cookie's Secure flag back
on, and prints the address. Stop it with `./ops/expose-tunnel.sh --stop`.

The address is temporary and changes whenever the tunnel restarts.

**While it is running the catalogue is reachable from the internet.** It is
behind a login wall and administrators need two-factor authentication, but do
not leave it open unattended, and do not use it for anything but evaluation.

### 3. Your own hostname with real certificates

The proper answer for a server. Use the full stack rather than the local one:

```bash
# .env
SITE_HOSTNAME=catalogue.example.org
ACME_EMAIL=you@example.org
PUBLIC_URL=https://catalogue.example.org
COOKIE_SECURE=true

docker compose up -d --build
```

Caddy obtains and renews the certificate automatically. A free subdomain from
a dynamic-DNS provider works fine if you do not want to buy a domain. See
[deployment.md](deployment.md).

### What will not work

Binding the port to `0.0.0.0` and browsing to `http://server-ip:8080`. The API
refuses to run with a non-secure cookie on anything but a loopback address,
because that would put the session cookie on the open internet in clear. Use
one of the three above instead.

## Giving a colleague access

Two different links are involved, and sending the wrong one is the usual
mistake.

- **The address of the site.** `http://localhost:8080` is not it. That address
  exists only inside *your* SSH tunnel, on *your* device; it means nothing to
  anyone else. A colleague needs either the temporary public address from
  `./ops/expose-tunnel.sh` or a real hostname (options 2 and 3 above).
- **Their invitation link.** Created by **Users → Invite a clinician** and
  shown on screen. It is single-use and expires in 72 hours.

Order matters. The invitation link is built from `PUBLIC_URL`, so open the
tunnel *first* and create the invitation *afterwards* — otherwise the link
points at `localhost` and will not open on their phone. If that has already
happened, **Users** → re-invite the person; the earlier link is revoked and the
new one carries the current address.

## When to move to a server

Move when any of these becomes true:

- More than one person needs access.
- It must be reachable when your machine is off.
- Real clinical content is being published.
- You need the nightly encrypted backups and retention.

[migration.md](migration.md) covers moving the data across. Nothing is tied to
this machine: it is a repository, an `.env` file and a database dump.
