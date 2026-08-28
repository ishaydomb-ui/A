#!/usr/bin/env bash
# Tears down everything started by setup_remote_desktop.sh.
set -euo pipefail

RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.remote_desktop"

for name in novnc x11vnc fluxbox xvfb; do
  pid_file="$RUN_DIR/$name.pid"
  if [ -f "$pid_file" ]; then
    pid="$(cat "$pid_file")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid"
      echo "Stopped $name (pid $pid)."
    fi
    rm -f "$pid_file"
  fi
done

echo "Remote desktop stopped."
