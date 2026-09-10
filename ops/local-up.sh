#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Brings the catalogue up on this machine, from nothing to a working URL.
#
#   ./ops/local-up.sh
#
# Safe to re-run: it skips whatever is already done.
# ---------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE=(docker compose -f docker-compose.local.yml)
URL="http://localhost:8080"
SEED_FILE="/seed/medication_catalogue_central_export.xlsx"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m/\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31mx %s\033[0m\n' "$*" >&2; exit 1; }

# --- Prerequisites ---------------------------------------------------------
command -v docker >/dev/null 2>&1 || die "Docker is not installed."
docker info >/dev/null 2>&1 || die "Docker is installed but not running. Start Docker Desktop and try again."
docker compose version >/dev/null 2>&1 || die "This needs Docker Compose v2 ('docker compose', not 'docker-compose')."

random_secret() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -base64 "$1"
    else
        head -c "$1" /dev/urandom | base64 | tr -d '\n'
    fi
}

# --- Secrets ---------------------------------------------------------------
say "1/6  Configuration"
if [ -f .env ] && grep -q '^APP_SECRET=' .env; then
    ok ".env already has APP_SECRET"
else
    {
        echo "APP_SECRET=$(random_secret 48)"
        echo "POSTGRES_PASSWORD=$(random_secret 24)"
    } >> .env
    chmod 600 .env
    ok "generated secrets into .env"
fi

# --- Build and start -------------------------------------------------------
say "2/6  Building and starting containers (the first run takes a few minutes)"
"${COMPOSE[@]}" up -d --build

# --- Wait for readiness ----------------------------------------------------
say "3/6  Waiting for the API"
ready=""
for _ in $(seq 1 60); do
    if curl -fsS -m 3 "$URL/readyz" >/dev/null 2>&1; then ready=yes; break; fi
    sleep 2
done

if [ -z "$ready" ]; then
    printf '\n'
    warn "The API did not become ready. Recent logs:"
    "${COMPOSE[@]}" logs --tail=40 api || true
    printf '\n'
    warn "Container status:"
    "${COMPOSE[@]}" ps || true
    die "Startup failed. The logs above usually say why."
fi
ok "API ready - schema applied automatically"

# --- Administrator ---------------------------------------------------------
say "4/6  Administrator account"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@example.org}"
ADMIN_NAME="${ADMIN_NAME:-Administrator}"

if [ -z "${ADMIN_PASSWORD:-}" ]; then
    ADMIN_PASSWORD="$(random_secret 18)"
    GENERATED_PASSWORD=yes
fi

"${COMPOSE[@]}" exec -T \
    -e BOOTSTRAP_EMAIL="$ADMIN_EMAIL" \
    -e BOOTSTRAP_NAME="$ADMIN_NAME" \
    -e BOOTSTRAP_PASSWORD="$ADMIN_PASSWORD" \
    api node dist/scripts/create-admin.js >/dev/null
ok "administrator ready: $ADMIN_EMAIL"

# --- Seed ------------------------------------------------------------------
say "5/6  Loading the catalogue"
WORKBOOK="${WORKBOOK:-$SEED_FILE}"
if "${COMPOSE[@]}" exec -T api node dist/scripts/seed.js "$WORKBOOK" >/dev/null 2>&1; then
    ok "workbook imported as drafts"
else
    warn "nothing imported - the catalogue already holds records, or the workbook was not found"
fi

# --- Publish for evaluation ------------------------------------------------
say "6/6  Publishing for evaluation"
if "${COMPOSE[@]}" exec -T api node dist/scripts/publish-for-evaluation.js >/dev/null 2>&1; then
    ok "records published, each flagged as not clinically reviewed"
else
    warn "nothing left to publish"
fi

# --- Done ------------------------------------------------------------------
printf '\n------------------------------------------------------------\n'
printf '  Open:      %s\n\n' "$URL"
printf '  Email:     %s\n' "$ADMIN_EMAIL"

if [ -n "${GENERATED_PASSWORD:-}" ]; then
    printf '  Password:  %s\n\n' "$ADMIN_PASSWORD"
    printf '  \033[33mThis password was generated. Save it now - it is not stored.\033[0m\n'
else
    printf '  Password:  (the one you set in ADMIN_PASSWORD)\n'
fi

printf '\n'
printf '  At first sign-in you will be asked to set up two-factor\n'
printf '  authentication. That is required for administrators.\n'
printf '  Save the ten recovery codes you are shown.\n\n'
printf '  The catalogue is in evaluation: every record is marked as\n'
printf '  not clinically reviewed, and must not be used clinically.\n'
printf '------------------------------------------------------------\n\n'
