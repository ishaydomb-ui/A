"""One-time interactive login capture.

This is the one step that genuinely needs a real display. On a headless
server (no monitor attached — e.g. the Contabo VPS), the way to get one is
scripts/setup_remote_desktop.sh: it starts a virtual display + noVNC you can
view from your phone's browser over an SSH tunnel, then you run this script
with DISPLAY set to that virtual display so the browser window shows up
there for you to log into by hand:

    ./scripts/setup_remote_desktop.sh
    DISPLAY=:99 python3 scripts/login_helper.py shufersal data/sessions/shufersal_storage_state.json

If you instead have a machine with a real monitor handy (laptop, etc.), you
can run this directly there (no DISPLAY override needed, no remote desktop
setup) and copy the resulting storage_state.json onto the server at the
path configured in SHUFERSAL_STORAGE_STATE_PATH / TIVTAAM_STORAGE_STATE_PATH.

Usage:
    python scripts/login_helper.py shufersal ./shufersal_storage_state.json
    python scripts/login_helper.py tiv_taam ./tivtaam_storage_state.json

The script opens a real (headed) browser window pointed at the store's
login page, waits for you to log in by hand (including any OTP/captcha),
and saves the resulting cookies/local storage once you press Enter in this
terminal. After that, the server-side adapters reuse this file indefinitely
without asking you to log in again, until the store invalidates the
session.
"""
from __future__ import annotations

import sys

LOGIN_URLS = {
    "shufersal": "https://www.shufersal.co.il/online/he/login",
    "tiv_taam": "https://www.tivtaam.co.il/",  # TODO(Phase 2): confirm actual login URL
}


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in LOGIN_URLS:
        print(f"Usage: python {sys.argv[0]} <{'|'.join(LOGIN_URLS)}> <output_path.json>")
        raise SystemExit(1)

    store, output_path = sys.argv[1], sys.argv[2]
    import os

    from playwright.sync_api import sync_playwright

    # Both chains refuse non-Israeli IPs. Without this the login page is a
    # geo-block placeholder served with HTTP 200, so the window would just
    # look broken rather than blocked. The saved session must also be
    # captured through the same exit the bot will later use.
    proxy = os.environ.get("PLAYWRIGHT_PROXY", "")
    launch_kwargs = {"headless": False}
    if proxy:
        launch_kwargs["proxy"] = {"server": proxy}
        print(f"Using proxy {proxy} (required from outside Israel).")
    else:
        print(
            "WARNING: PLAYWRIGHT_PROXY is not set. If this machine is not in "
            "Israel the store will show a geo-block page instead of the login form."
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context()
        page = context.new_page()
        page.goto(LOGIN_URLS[store])
        input(
            f"A browser window opened at the {store} login page.\n"
            "Log in by hand (including OTP if asked), make sure you land on "
            "your logged-in account page, then press Enter here to save the session..."
        )
        context.storage_state(path=output_path)
        browser.close()
    print(f"Saved session to {output_path}. Copy this file to the server at the configured path.")


if __name__ == "__main__":
    main()
