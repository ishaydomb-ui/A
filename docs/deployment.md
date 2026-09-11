# Deployment

This describes a first deployment on a Contabo VPS. Nothing in it is specific
to Contabo: the same steps work on any host that can run Docker.

## Before you start

You need:

- A server with at least 2 vCPU, 4 GB RAM and 40 GB disk, running a current
  Debian or Ubuntu.
- A hostname (for example `catalogue.example.org`) whose DNS **A record**
  already points at the server's IP address. Certificates cannot be issued
  until it does.
- Ports 80 and 443 reachable from the internet.
- Optionally, SMTP credentials. Without them the system still works —
  invitations are written to an on-disk outbox and handed over manually — see
  [Mail](#mail).

## 1. Install Docker

```bash
curl -fsSL https://get.docker.com | sh
docker --version && docker compose version
```

## 2. Get the code

```bash
sudo mkdir -p /opt/medcatalogue && sudo chown "$USER" /opt/medcatalogue
git clone <repository-url> /opt/medcatalogue
cd /opt/medcatalogue
```

## 3. Create the configuration

```bash
cp .env.example .env
chmod 600 .env
```

Generate real secrets — do not reuse the examples:

```bash
echo "POSTGRES_PASSWORD=$(openssl rand -base64 32)"
echo "APP_SECRET=$(openssl rand -base64 48)"
echo "BACKUP_PASSPHRASE=$(openssl rand -base64 32)"
```

Edit `.env` and set at least:

| Variable | Value |
| --- | --- |
| `POSTGRES_PASSWORD` | the generated value |
| `APP_SECRET` | the generated value (min 32 chars) |
| `BACKUP_PASSPHRASE` | the generated value |
| `SITE_HOSTNAME` | `catalogue.example.org` |
| `PUBLIC_URL` | `https://catalogue.example.org` |
| `ACME_EMAIL` | an address that should receive certificate warnings |
| `DATABASE_URL` | `postgres://medapp:<POSTGRES_PASSWORD>@db:5432/medcat` |

`APP_SECRET` protects session tokens and encrypts stored MFA secrets. If it is
lost, every session is invalidated and every enrolled authenticator must be
re-enrolled. Keep a copy somewhere safe and separate from the server.

> **Keep `BACKUP_PASSPHRASE` off the server as well.** A backup encrypted with
> a passphrase that only exists on the machine you are restoring *from* is not
> a backup.

The application refuses to start in production if `APP_SECRET` still contains
a placeholder, or if `COOKIE_SECURE` is not true.

## 4. Start the stack

```bash
docker compose build
docker compose up -d
docker compose ps
```

Caddy requests a certificate on first start. Watch it complete:

```bash
docker compose logs -f proxy
```

If certificate issuance fails, the usual cause is DNS not yet pointing at the
server, or port 80 being blocked. While testing, uncomment the `acme_ca`
staging line in `ops/Caddyfile` to avoid Let's Encrypt's rate limits.

## 5. Apply the schema

```bash
docker compose exec api node dist/db/migrate.js up
docker compose exec api node dist/db/migrate.js status
```

## 6. Create the first administrator

There is no public registration, so the first account is created on the
server. The password is read from the environment rather than an argument, so
it does not appear in the process list:

```bash
docker compose exec -e BOOTSTRAP_EMAIL='you@example.org' \
                    -e BOOTSTRAP_NAME='Your Name' \
                    -e BOOTSTRAP_PASSWORD='<a long passphrase>' \
                    api node dist/scripts/create-admin.js
```

Then clear it from your shell history (`history -d`, or prefix the command
with a space if your shell is configured to skip those).

Sign in at `https://catalogue.example.org`. As an administrator you will be
required to set up two-factor authentication before you are given a session.
**Save the ten recovery codes** — they are shown once.

## 7. Check it is healthy

```bash
curl -fsS https://catalogue.example.org/healthz    # process is alive
curl -fsS https://catalogue.example.org/readyz     # database reachable, schema applied
```

`readyz` returns 503 until migrations have been applied, so a half-deployed
instance can be kept out of a load balancer.

## 8. Confirm backups

The backup container installs a nightly schedule (02:00 UTC by default). Take
one immediately and then **practise a restore** before going live:

```bash
docker compose exec backup /usr/local/bin/backup.sh
ls -la backups/
```

Follow [backup-and-restore.md](backup-and-restore.md) for the restore drill.
Do not treat the system as production-ready until a restore has actually been
performed.

## Mail

If `SMTP_URL` is unset, no email is sent anywhere. Invitations and password
resets are written to `/data/outbox` inside the API container and logged, and
the invitation screen shows the administrator a single-use link to pass on by
hand. This is deliberate: a fresh deployment cannot surprise anyone with
unexpected mail.

To enable real delivery, set `SMTP_URL` (for example
`smtps://user:password@smtp.example.org:465`) and `MAIL_FROM`, then
`docker compose up -d api`.

## Routine operations

```bash
docker compose logs -f api            # structured JSON logs
docker compose ps                     # health of each service
docker compose restart api            # restart one service
docker compose down                   # stop everything (data volumes survive)
```

To deploy a new version:

```bash
git pull
docker compose build
docker compose up -d
docker compose exec api node dist/db/migrate.js up
```

Migrations are forward-only and checksummed: an already-applied file that has
been edited is refused rather than silently re-run.

## Hardening the host

The stack does its part — unprivileged containers, no new privileges, the
database unreachable from outside, TLS with HSTS, security headers, rate
limiting. The host still needs the usual care:

```bash
# Only SSH and the web ports need to be open.
sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
sudo ufw enable

# Key-based SSH only.
sudo sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl reload ssh

# Unattended security updates.
sudo apt install -y unattended-upgrades
```

Copy `backups/` off the server on a schedule. A backup that lives only on the
machine it protects does not protect against losing that machine.
