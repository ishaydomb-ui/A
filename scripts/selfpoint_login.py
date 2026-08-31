#!/usr/bin/env python3
"""One-time human-assisted login to a Self-Point chain (Tiv Taam, Victory).

Login is behind a checkbox reCAPTCHA, which is a deliberate human-only
gate — there is nothing to automate here and no point retrying headlessly.
So a browser is opened on the noVNC desktop, a person logs in, and the
session is captured.

Two things here are not optional, both learned the hard way:

1. **A persistent on-disk profile.** An ordinary ``new_context()`` keeps
   its storage in memory, so killing the process throws the login away
   with it. Four captures were lost that way before this was understood.

2. **Saving on a timer, unconditionally.** Earlier attempts saved only
   when a guessed localStorage key appeared. The guesses were wrong — the
   token lives *inside* the ``frontend`` key, under ``session``, not at
   the top level — so real logins were watched and never written out.
   This script does not look for a marker at all; it snapshots every 15
   seconds and reports what it sees.

Usage:
    DISPLAY=:99 python3 scripts/selfpoint_login.py victory
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from grocery_bot.adapters.selfpoint import RETAILERS  # noqa: E402

SITES = {
    "tivtaam": "https://www.tivtaam.co.il/",
    "victory": "https://www.victoryonline.co.il/",
}

SESSION_DIR = Path("data/sessions")
POLL_SECONDS = 15
DEFAULT_MINUTES = 45

# Cookie banners sit over the login button. Clicking one is cosmetic, but
# it removes a step from a flow the user has to do by hand on a slow site.
CONSENT_LABELS = ("קבל את כל", "אישור", "מאשר", "אני מסכים")


def session_token(state_path: Path) -> dict | None:
    """The account session inside the captured state, if a login happened."""
    if not state_path.exists():
        return None
    state = json.loads(state_path.read_text())
    for origin in state.get("origins", []):
        for item in origin.get("localStorage", []):
            if item.get("name") != "frontend":
                continue
            try:
                session = (json.loads(item["value"]) or {}).get("session") or {}
            except (ValueError, TypeError):
                continue
            if session.get("token"):
                return session
    return None


def main(store: str, minutes: int = DEFAULT_MINUTES) -> int:
    if store not in SITES:
        print(f"unknown store {store!r}; expected one of {sorted(SITES)}")
        return 2

    proxy = os.environ.get("PLAYWRIGHT_PROXY")
    if not proxy:
        # Without the Israeli exit the site answers with a block page that
        # looks like a working site, and the login would fail confusingly.
        print("PLAYWRIGHT_PROXY is unset — refusing to start")
        return 2

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    profile = SESSION_DIR / f"{store}_profile"
    state_path = SESSION_DIR / f"{store}_storage_state.json"

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(profile),
            headless=False,
            proxy={"server": proxy},
            args=["--no-sandbox"],
            viewport={"width": 1260, "height": 780},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(SITES[store], wait_until="domcontentloaded", timeout=90000)
        # Leave exactly one tab open and focused. A stray about:blank on top
        # is how a previous attempt had the user typing into a window nobody
        # was watching.
        for other in context.pages:
            if other is not page:
                try:
                    other.close()
                except Exception:
                    pass
        page.bring_to_front()
        for label in CONSENT_LABELS:
            try:
                page.get_by_text(label, exact=False).first.click(timeout=3000)
                break
            except Exception:
                continue

        retailer = RETAILERS[store]
        print(f"READY — {retailer.name} is open on the desktop. Log in there.", flush=True)
        print(f"profile: {profile}", flush=True)

        deadline = time.time() + minutes * 60
        while time.time() < deadline:
            time.sleep(POLL_SECONDS)
            try:
                context.storage_state(path=str(state_path))
            except Exception as exc:  # the window was closed, or navigated mid-save
                print("  snapshot failed:", repr(exc), flush=True)
                continue
            session = session_token(state_path)
            if session:
                print(
                    f"LOGGED IN as {session.get('username')} "
                    f"(user {session.get('userId')}) — session saved to {state_path}",
                    flush=True,
                )
                # Keep snapshotting briefly: the app finishes writing club
                # and branch details a few seconds after the token appears.
                for _ in range(4):
                    time.sleep(POLL_SECONDS)
                    context.storage_state(path=str(state_path))
                context.close()
                return 0
            print("  waiting for login...", flush=True)

        print("TIMED OUT — no login seen. The captured state has no session.", flush=True)
        context.close()
        return 1


if __name__ == "__main__":
    store = sys.argv[1] if len(sys.argv) > 1 else "victory"
    minutes = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_MINUTES
    raise SystemExit(main(store, minutes))
