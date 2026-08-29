"""Create a Shufersal session from username/password, no display needed.

Thin CLI wrapper around grocery_bot.login, which is also what the adapter
calls to renew an expired session on its own. Use this to create the
first session, or to check credentials after changing them.

Verified working against the real site on 2026-08-29: no OTP step. If an
account ever does get an OTP challenge, this reports it explicitly and
you fall back to scripts/login_helper.py (manual login over noVNC).

Usage (reads credentials and proxy from the environment / .env):
    python3 scripts/headless_login.py shufersal data/sessions/shufersal_storage_state.json
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grocery_bot.login import LoginFailed, OtpRequired, login_and_save_session  # noqa: E402

SUPPORTED_STORES = ("shufersal",)


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in SUPPORTED_STORES:
        print(f"Usage: python {sys.argv[0]} <{'|'.join(SUPPORTED_STORES)}> <output_path.json>")
        raise SystemExit(1)

    _, _store, output_path = sys.argv

    try:
        login_and_save_session(
            username=os.environ.get("SHUFERSAL_USERNAME", ""),
            password=os.environ.get("SHUFERSAL_PASSWORD", ""),
            output_path=output_path,
            proxy=os.environ.get("PLAYWRIGHT_PROXY", ""),
            headless=True,
        )
    except OtpRequired as exc:
        print(f"FAILED (OTP): {exc}")
        raise SystemExit(1)
    except LoginFailed as exc:
        print(f"FAILED: {exc}")
        raise SystemExit(1)

    print(f"Logged in and saved session to {output_path}.")


if __name__ == "__main__":
    main()
