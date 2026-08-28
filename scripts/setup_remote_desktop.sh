#!/usr/bin/env bash
# One-time (per boot) setup of a throwaway virtual desktop you can view from
# your phone's browser, so the interactive Shufersal login can happen on a
# headless VPS with no monitor attached.
#
# Everything binds to 127.0.0.1 only — nothing here is exposed to the public
# internet. You reach it by SSH-tunneling the noVNC port to your phone (most
# SSH apps, e.g. Termius, support local port forwarding):
#
#   Forward local 6080 -> localhost:6080 on this server
#
# then open http://localhost:6080/vnc.html in your phone's browser.
#
# Assumes a Debian/Ubuntu-family VPS (adjust package names if this server is
# something else — unverified which distro the Contabo box actually runs).
#
# Usage: ./scripts/setup_remote_desktop.sh [vnc_password]
# Stop with: ./scripts/stop_remote_desktop.sh
set -euo pipefail

DISPLAY_NUM=99
RESOLUTION=1280x800x24
VNC_PORT=5900
NOVNC_PORT=6080
RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.remote_desktop"
VNC_PASSWORD="${1:-}"

mkdir -p "$RUN_DIR"

if [ -f "$RUN_DIR/xvfb.pid" ] && kill -0 "$(cat "$RUN_DIR/xvfb.pid")" 2>/dev/null; then
  echo "Remote desktop already running (display :$DISPLAY_NUM). Run stop_remote_desktop.sh first if you want to restart it."
  exit 0
fi

echo "Installing system packages (xvfb, x11vnc, fluxbox, chromium, novnc, websockify)..."
sudo apt-get update -qq
sudo apt-get install -y -qq xvfb x11vnc fluxbox chromium novnc websockify >/dev/null

echo "Starting Xvfb on display :$DISPLAY_NUM..."
Xvfb ":$DISPLAY_NUM" -screen 0 "$RESOLUTION" >"$RUN_DIR/xvfb.log" 2>&1 &
echo $! > "$RUN_DIR/xvfb.pid"
sleep 1

echo "Starting fluxbox window manager..."
DISPLAY=":$DISPLAY_NUM" fluxbox >"$RUN_DIR/fluxbox.log" 2>&1 &
echo $! > "$RUN_DIR/fluxbox.pid"

echo "Starting x11vnc (localhost only, port $VNC_PORT)..."
if [ -n "$VNC_PASSWORD" ]; then
  x11vnc -display ":$DISPLAY_NUM" -localhost -rfbport "$VNC_PORT" -passwd "$VNC_PASSWORD" -forever -shared \
    >"$RUN_DIR/x11vnc.log" 2>&1 &
else
  echo "WARNING: no VNC password given, running with -nopw (fine since it's localhost-only + SSH-tunneled)."
  x11vnc -display ":$DISPLAY_NUM" -localhost -rfbport "$VNC_PORT" -nopw -forever -shared \
    >"$RUN_DIR/x11vnc.log" 2>&1 &
fi
echo $! > "$RUN_DIR/x11vnc.pid"

echo "Starting noVNC (localhost only, port $NOVNC_PORT)..."
websockify --web=/usr/share/novnc/ "127.0.0.1:$NOVNC_PORT" "127.0.0.1:$VNC_PORT" \
  >"$RUN_DIR/novnc.log" 2>&1 &
echo $! > "$RUN_DIR/novnc.pid"

sleep 1
echo ""
echo "Remote desktop is up. From your phone:"
echo "  1. SSH-tunnel local port $NOVNC_PORT to this server's port $NOVNC_PORT."
echo "  2. Open http://localhost:$NOVNC_PORT/vnc.html in your phone browser."
echo ""
echo "Then in another SSH session on this server, run:"
echo "  DISPLAY=:$DISPLAY_NUM python3 scripts/login_helper.py shufersal data/sessions/shufersal_storage_state.json"
echo ""
echo "When done, run ./scripts/stop_remote_desktop.sh to tear this down."
