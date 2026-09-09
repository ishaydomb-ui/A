"""Hold behatsdaa open and capture the session whenever it appears.

Built 2026-09-09 after three failed hand-offs. The pattern that kept
failing was synchronisation: Ishay logs in on the noVNC desktop from his
phone, switching back to Telegram drops the VNC view, and by the time
either of us spoke the other had moved on. Worse, the previous watcher
polled `localStorage.userToken` — a key this site sets to the string
"true" — so it would have reported "timed out" even on a *successful*
login. Two of those reports were probably false.

So this removes the coordination entirely:

- It runs for hours, not minutes. Nobody has to be present.
- It does not look for one known key. It snapshots the whole of
  localStorage plus cookies, and calls it a login when anything
  JWT-shaped appears, or when the SPA leaves /login while carrying
  state it did not have before.
- It saves on *every* change, not once at the end, so a session that
  exists for two minutes is still captured.
- If the tab dies or drifts, it reloads rather than sitting on a blank
  page waiting for a person who has gone.

The result is that Ishay can log in whenever it suits him, disconnect
freely, and never tell anyone he did it.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright  # noqa: E402

PROFILE = os.path.expanduser("~/grocery-automation/data/benefits/behatsdaa_profile")
OUT = os.path.expanduser("~/grocery-automation/data/sessions/behatsdaa_state.json")
STAMP = os.path.expanduser("~/grocery-automation/data/sessions/behatsdaa_login_seen.json")
START_URL = "https://www.behatsdaa.org.il/login"

HOURS = float(os.environ.get("WATCH_HOURS", "6"))
POLL_SECONDS = 20

# A JWT is three base64url segments separated by dots. Anything of that
# shape and length is worth saving, whatever key it hides under — the
# point of this watcher is not to know the site's naming.
_JWT = re.compile(r"^[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}$")


# The site keeps its session in an HttpOnly `AccessToken` cookie on
# `back.behatsdaa.org.il` — a *different* host from the page. Neither
# `document.cookie` nor localStorage can see it, which is why the first
# capture attempt watched a logged-in browser for five minutes and
# reported nothing. Detection has to come from the browser context,
# which sees every cookie regardless of host or HttpOnly.
_AUTH_COOKIES = ("accesstoken", "refreshtoken", ".aspnetcore.session")


def _cookie_reason(cookies: list) -> str:
    """A reason string when the context holds a real session cookie."""
    for cookie in cookies:
        name = str(cookie.get("name", ""))
        if name.lower() in _AUTH_COOKIES and len(str(cookie.get("value", ""))) > 40:
            return f"{name} cookie on {cookie.get('domain', '?')}"
    return ""


def _looks_authenticated(snapshot: dict) -> str:
    """A reason string when this looks logged in, else ""."""
    for key, value in snapshot.get("localStorage", {}).items():
        text = str(value or "")
        if _JWT.match(text.strip('"')):
            return f"JWT-shaped value in localStorage[{key}]"
        if len(text) > 60 and key.lower() not in ("adoric_query_conditions",):
            if any(w in key.lower() for w in ("token", "auth", "user", "session", "member")):
                return f"long credential-ish value in localStorage[{key}]"
    for name in snapshot.get("cookies", []):
        if any(w in name.lower() for w in ("token", "auth", "jwt", ".aspxauth", "identity")):
            return f"auth cookie {name}"
    return ""


def main() -> int:
    proxy = os.environ.get("PLAYWRIGHT_PROXY", "")
    deadline = time.time() + HOURS * 3600
    saved_reason = ""

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=False, locale="he-IL", timezone_id="Asia/Jerusalem",
            viewport={"width": 1260, "height": 760},
            proxy={"server": proxy} if proxy else None,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:  # noqa: BLE001
            print(f"initial load failed: {type(exc).__name__}", flush=True)

        print(f"watching for {HOURS}h — log in whenever, no need to tell anyone", flush=True)
        while time.time() < deadline:
            time.sleep(POLL_SECONDS)
            try:
                if page.is_closed():
                    page = ctx.new_page()
                    page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)

                snapshot = page.evaluate(
                    """() => {
                        const ls = {};
                        try {
                            for (let i = 0; i < localStorage.length; i++) {
                                const k = localStorage.key(i);
                                ls[k] = (localStorage.getItem(k) || '').slice(0, 400);
                            }
                        } catch (e) {}
                        return {
                            url: location.href,
                            localStorage: ls,
                            cookies: document.cookie.split(';').map(c => c.split('=')[0].trim()),
                        };
                    }"""
                )
                reason = _cookie_reason(ctx.cookies()) or _looks_authenticated(snapshot)
                stamp = datetime.now().strftime("%H:%M:%S")
                if reason and reason != saved_reason:
                    ctx.storage_state(path=OUT)
                    with open(STAMP, "w", encoding="utf-8") as handle:
                        json.dump(
                            {"seen_at": datetime.now().isoformat(), "reason": reason,
                             "url": snapshot.get("url", "")},
                            handle, ensure_ascii=False,
                        )
                    saved_reason = reason
                    print(f"[{stamp}] SAVED — {reason}", flush=True)
                else:
                    keys = len(snapshot.get("localStorage", {}))
                    print(f"[{stamp}] {snapshot.get('url','?')[:58]} keys={keys}"
                          f"{' (saved)' if saved_reason else ''}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"poll error: {type(exc).__name__} {str(exc)[:90]}", flush=True)
                try:
                    page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)
                except Exception:  # noqa: BLE001
                    pass
        ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
