#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Prepares the stack for access over an SSH tunnel, and prints the exact
# command to run on your own machine.
#
#   ./ops/ssh-tunnel.sh
#
# Nothing is exposed to the internet: the traffic travels inside your existing
# SSH connection, and the containers stay bound to loopback.
# ---------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE=(docker compose -f docker-compose.local.yml)
LOCAL_URL="http://localhost:8080"

ok()   { printf '  \033[32m/\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
rule() { printf '%s\n' "------------------------------------------------------------"; }

# --- Close any public tunnel ----------------------------------------------
if [ -f .cloudflared.pid ] && kill -0 "$(cat .cloudflared.pid)" 2>/dev/null; then
    kill "$(cat .cloudflared.pid)" 2>/dev/null || true
    rm -f .cloudflared.pid
    ok "closed the public tunnel - nothing is exposed to the internet now"
fi

# --- Configure for loopback access ----------------------------------------
say "1/3  Configuration"
NEEDS_RESTART=""

# A Secure cookie is not sent over plain http by some browsers, Safari among
# them. Over an SSH tunnel the browser sees http://localhost, so the app has to
# be configured for that or sign-in silently fails: the request succeeds and
# the session is dropped on the way back.
if grep -qE '^PUBLIC_URL=' .env 2>/dev/null && ! grep -qE '^PUBLIC_URL=http://localhost:8080$' .env; then
    NEEDS_RESTART=yes
fi
if grep -qE '^COOKIE_SECURE=true$' .env 2>/dev/null; then
    NEEDS_RESTART=yes
fi

if [ -n "$NEEDS_RESTART" ]; then
    grep -v -E '^(PUBLIC_URL|COOKIE_SECURE)=' .env > .env.tmp 2>/dev/null || true
    {
        echo "PUBLIC_URL=http://localhost:8080"
        echo "COOKIE_SECURE=false"
    } >> .env.tmp
    mv .env.tmp .env
    chmod 600 .env
    ok "set for loopback access"
    "${COMPOSE[@]}" up -d api >/dev/null 2>&1
    ok "API restarted"
else
    ok "already set for loopback access"
fi

# --- Check it answers ------------------------------------------------------
say "2/3  Checking the stack"
ready=""
for _ in $(seq 1 30); do
    if curl -fsS -m 3 "$LOCAL_URL/readyz" >/dev/null 2>&1; then ready=yes; break; fi
    sleep 2
done

if [ -z "$ready" ]; then
    warn "The catalogue is not answering on this server."
    warn "Start it with ./ops/local-up.sh, then run this again."
    exit 1
fi
ok "answering on $LOCAL_URL (from inside this server)"

# --- Work out the command --------------------------------------------------
say "3/3  The command for your own machine"

SSH_USER="$(id -un)"
# SSH_CONNECTION is "client-ip client-port server-ip server-port", so field 3
# is the address you actually connected to - better than guessing.
SERVER_ADDR="$(printf '%s' "${SSH_CONNECTION:-}" | awk '{print $3}')"
[ -n "$SERVER_ADDR" ] || SERVER_ADDR="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "$SERVER_ADDR" ] || SERVER_ADDR="<your-server-address>"

printf '\n'
rule
printf '%s\n\n' "  On YOUR OWN computer - not in this SSH session - run:"
printf '      \033[1mssh -N -L 8080:127.0.0.1:8080 %s@%s\033[0m\n\n' "$SSH_USER" "$SERVER_ADDR"
printf '%s\n' "  Leave that window open, then browse to:"
printf '      \033[1m%s\033[0m\n\n' "$LOCAL_URL"
printf '%s\n' "  Nothing is exposed to the internet. The traffic runs inside"
printf '%s\n' "  your SSH connection, and closing that window ends the access."
printf '\n'
printf '%s\n' "  If port 8080 is already taken on your computer, use another:"
printf '%s\n' "      ssh -N -L 8090:127.0.0.1:8080 ..."
printf '%s\n' "  and browse to http://localhost:8090 instead."
rule
printf '\n'
