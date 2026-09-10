#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# What state is the local stack in?
#
#   ./ops/local-status.sh
# ---------------------------------------------------------------------------
set -uo pipefail

cd "$(dirname "$0")/.."
COMPOSE=(docker compose -f docker-compose.local.yml)
URL="http://localhost:8080"

hdr() { printf '\n%s\n' "== $* =="; }

hdr "Containers"
"${COMPOSE[@]}" ps

hdr "Reachability"
if curl -fsS -m 5 "$URL/readyz" 2>/dev/null; then
    printf '\n%s\n' "The site is up: $URL"
else
    printf '%s\n' "No answer on $URL — see the API logs below."
fi

hdr "Accounts"
"${COMPOSE[@]}" exec -T db psql -U "${POSTGRES_USER:-medapp}" -d "${POSTGRES_DB:-medcat}" \
    -c "SELECT email, role, status, (mfa_enabled_at IS NOT NULL) AS mfa FROM users ORDER BY created_at;" \
    2>/dev/null || printf '%s\n' "Could not read the database."

hdr "Catalogue"
"${COMPOSE[@]}" exec -T db psql -U "${POSTGRES_USER:-medapp}" -d "${POSTGRES_DB:-medcat}" \
    -c "SELECT state, count(*) AS records,
               count(*) FILTER (WHERE published_unvalidated) AS not_reviewed
          FROM medication_versions GROUP BY state ORDER BY state;" \
    2>/dev/null || true

hdr "Open findings"
"${COMPOSE[@]}" exec -T db psql -U "${POSTGRES_USER:-medapp}" -d "${POSTGRES_DB:-medcat}" \
    -c "SELECT severity, count(*) FROM review_findings WHERE status = 'open' GROUP BY severity ORDER BY 1;" \
    2>/dev/null || true

hdr "Recent API log"
"${COMPOSE[@]}" logs --tail=15 api 2>/dev/null || true

printf '\n%s\n' "To set a known password:"
printf '%s\n' "  docker compose -f docker-compose.local.yml exec \\"
printf '%s\n' "    -e BOOTSTRAP_EMAIL='you@example.org' -e BOOTSTRAP_NAME='Your Name' \\"
printf '%s\n' "    -e BOOTSTRAP_PASSWORD='a-long-passphrase' \\"
printf '%s\n\n' "    api node dist/scripts/create-admin.js"
