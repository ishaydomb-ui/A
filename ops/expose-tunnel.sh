#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Publishes the local stack on a temporary public HTTPS address, using a
# Cloudflare quick tunnel.
#
#   ./ops/expose-tunnel.sh          start, print the address
#   ./ops/expose-tunnel.sh --stop   stop the tunnel
#
# Use this to reach the catalogue from a phone or another machine when it is
# running on a server you only have a shell on.
#
# READ THIS FIRST: while the tunnel is running, the catalogue is reachable from
# the public internet. It is behind a login wall, administrators must use
# two-factor authentication, and the address is unguessable — but it is
# exposed. Stop the tunnel when you are done.
#
# The address is temporary and changes every time the tunnel restarts. For
# something permanent, use the full stack with your own hostname; see
# docs/deployment.md.
# ---------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE=(docker compose -f docker-compose.local.yml)
BIN="./.cloudflared"
LOG="./.cloudflared.log"
PIDFILE="./.cloudflared.pid"
LOCAL_URL="http://localhost:8080"

ok()   { printf '  \033[32m/\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31mx %s\033[0m\n' "$*" >&2; exit 1; }
say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }

# --- Stop ------------------------------------------------------------------
if [ "${1:-}" = "--stop" ]; then
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        kill "$(cat "$PIDFILE")"
        rm -f "$PIDFILE"
        ok "tunnel stopped - the catalogue is no longer reachable from the internet"
    else
        warn "no tunnel appears to be running"
        rm -f "$PIDFILE"
    fi
    exit 0
fi

# --- Preconditions ---------------------------------------------------------
say "1/4  Checking the stack"
curl -fsS -m 5 "$LOCAL_URL/readyz" >/dev/null 2>&1 \
    || die "The catalogue is not answering on $LOCAL_URL. Start it first with ./ops/local-up.sh"
ok "stack is up"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    die "A tunnel is already running. Stop it with: $0 --stop"
fi

# --- cloudflared -----------------------------------------------------------
say "2/4  Cloudflare tunnel client"
if [ -x "$BIN" ]; then
    ok "already downloaded"
else
    case "$(uname -m)" in
        x86_64|amd64) ARCH=amd64 ;;
        aarch64|arm64) ARCH=arm64 ;;
        *) die "Unsupported architecture: $(uname -m)" ;;
    esac
    URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$ARCH"
    curl -fsSL -o "$BIN" "$URL" || die "Could not download cloudflared from $URL"
    chmod +x "$BIN"
    ok "downloaded"
fi

# --- Start -----------------------------------------------------------------
say "3/4  Opening the tunnel"
: > "$LOG"
nohup "$BIN" tunnel --no-autoupdate --url "$LOCAL_URL" >>"$LOG" 2>&1 &
echo $! > "$PIDFILE"

PUBLIC=""
for _ in $(seq 1 45); do
    PUBLIC="$(grep -oE 'https://[a-zA-Z0-9.-]+\.trycloudflare\.com' "$LOG" 2>/dev/null | head -1 || true)"
    [ -n "$PUBLIC" ] && break
    sleep 2
done

if [ -z "$PUBLIC" ]; then
    warn "No address appeared. Last of the tunnel log:"
    tail -20 "$LOG" || true
    kill "$(cat "$PIDFILE")" 2>/dev/null || true
    rm -f "$PIDFILE"
    die "Could not open a tunnel."
fi
ok "address: $PUBLIC"

# --- Point the app at it ---------------------------------------------------
say "4/4  Reconfiguring the app for that address"
# The session cookie is marked Secure now that the app is served over HTTPS,
# so it is never sent over a plain connection.
grep -v -E '^(PUBLIC_URL|COOKIE_SECURE)=' .env > .env.tmp 2>/dev/null || true
{
    echo "PUBLIC_URL=$PUBLIC"
    echo "COOKIE_SECURE=true"
} >> .env.tmp
mv .env.tmp .env
chmod 600 .env

"${COMPOSE[@]}" up -d api >/dev/null 2>&1
for _ in $(seq 1 30); do
    curl -fsS -m 3 "$LOCAL_URL/readyz" >/dev/null 2>&1 && break
    sleep 2
done
ok "app restarted"

rule() { printf '%s\n' "------------------------------------------------------------"; }
printf '\n'
rule
printf '%s\n\n' "  Open on any device:  $PUBLIC"
printf '%s\n' "  This address is temporary and changes if the tunnel restarts."
printf '%s\n' "  Stop it with:  ./ops/expose-tunnel.sh --stop"
printf '\n'
printf '  \033[33m%s\033[0m\n' "While it runs, the catalogue is reachable from the internet."
printf '  \033[33m%s\033[0m\n' "It is behind a login wall, but do not leave it open unattended."
rule
printf '\n'
