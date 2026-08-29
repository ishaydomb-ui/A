"""Is the Israeli exit actually usable right now?

The store adapters reach Shufersal/Tiv Taam through a SOCKS5 proxy
(Tailscale userspace mode) whose exit node is a device at the user's home
in Israel. That device is a TV box the user genuinely powers off, so
"the exit is down" is a normal, expected state — not an exceptional one.

Checking first matters because the failure is silent and misleading:
with the exit node down, requests hang or die at the SOCKS layer, and
with a *non-Israeli* exit the stores answer HTTP 200 with a geo-block
placeholder that parses as "every selector broke". Either way a whole
order cycle burns through every item producing errors, which looks like
the bot is broken rather than like the network is unavailable.

So: probe once, cheaply, before committing to a cycle, and confirm the
exit is not just reachable but actually *Israeli*.
"""
from __future__ import annotations

import json
import logging
import urllib.request

logger = logging.getLogger(__name__)

# ipinfo.io answers with the caller's apparent country. Requested through
# the same proxy the adapters use, so it reports the exit node's country
# rather than this server's.
PROBE_URL = "https://ipinfo.io/json"
PROBE_TIMEOUT_SECONDS = 12
EXPECTED_COUNTRY = "IL"


class ExitStatus:
    """Outcome of one probe. Not an exception: 'down' is a normal state."""

    def __init__(self, available: bool, detail: str, country: str = "") -> None:
        self.available = available
        self.detail = detail
        self.country = country

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ExitStatus(available={self.available}, country={self.country!r}, detail={self.detail!r})"


def check_israeli_exit(proxy: str) -> ExitStatus:
    """Probe the proxy and report whether it currently exits via Israel.

    Never raises — every failure mode (no proxy configured, exit node
    asleep, DNS failure, wrong country) comes back as an ExitStatus the
    caller can show the user.
    """
    if not proxy:
        return ExitStatus(False, "אין פרוקסי מוגדר (PLAYWRIGHT_PROXY ריק)")

    # urllib speaks SOCKS only via an external dependency, but Playwright's
    # proxy string is also a plain HTTP-proxy-style URL, so route the probe
    # through curl instead of adding a dependency for one health check.
    import subprocess

    try:
        completed = subprocess.run(
            [
                "curl",
                "--silent",
                "--max-time",
                str(PROBE_TIMEOUT_SECONDS),
                "--proxy",
                proxy,
                PROBE_URL,
            ],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS + 5,
        )
    except subprocess.TimeoutExpired:
        return ExitStatus(False, "הפרוקסי לא הגיב בזמן — כנראה שצומת היציאה כבויה")
    except FileNotFoundError:
        return ExitStatus(False, "curl לא מותקן, לא ניתן לבדוק את הפרוקסי")

    if completed.returncode != 0 or not completed.stdout.strip():
        return ExitStatus(False, "לא ניתן להתחבר דרך הפרוקסי — צומת היציאה כנראה כבויה")

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return ExitStatus(False, "תשובה לא צפויה מבדיקת הפרוקסי")

    country = str(payload.get("country", ""))
    if country != EXPECTED_COUNTRY:
        # Reachable but exiting somewhere else: the stores would serve a
        # geo-block page with HTTP 200, which is worse than a clean failure
        # because it imitates broken selectors.
        return ExitStatus(
            False,
            f"היציאה פעילה אבל לא מישראל (מדינה: {country or 'לא ידוע'})",
            country=country,
        )

    return ExitStatus(True, f"יציאה ישראלית פעילה ({payload.get('org', '')})", country=country)
