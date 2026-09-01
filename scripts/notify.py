#!/usr/bin/env python3
"""Send one Telegram message to the household, for alerts.

Shared by the systemd OnFailure= handler and the backup doctor. Kept
separate from the bot itself on purpose: an alert about the bot being
down cannot be delivered by the bot.

Reads TELEGRAM_BOT_TOKEN and ALLOWED_TELEGRAM_USER_IDS from the
environment, which systemd supplies through the same EnvironmentFile the
other units already use.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.telegram.org/bot{token}/sendMessage"


def recipients() -> list[str]:
    raw = os.environ.get("ALLOWED_TELEGRAM_USER_IDS", "")
    return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]


def send(text: str) -> int:
    """Send to every allowed user. Returns how many sends succeeded."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("TELEGRAM_BOT_TOKEN is unset; cannot notify", file=sys.stderr)
        return 0

    sent = 0
    for chat_id in recipients():
        payload = json.dumps(
            {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        ).encode()
        request = urllib.request.Request(
            API.format(token=token),
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                if response.status == 200:
                    sent += 1
        except (urllib.error.URLError, OSError) as exc:
            # A failed alert must not crash the caller — the caller is
            # usually already handling a failure of its own.
            print(f"notify failed for {chat_id}: {exc}", file=sys.stderr)
    return sent


def main() -> int:
    text = " ".join(sys.argv[1:]).strip() or sys.stdin.read().strip()
    if not text:
        print("nothing to send", file=sys.stderr)
        return 2
    return 0 if send(text) else 1


if __name__ == "__main__":
    raise SystemExit(main())
