"""One-time login to behatsdaa, ours — not inherited from the Strategist.

behatsdaa's login is passwordless: an ID goes in, an OTP comes to Ishay's
SMS and email, and he hands it back. So this is not headless-automatable
end to end — it deliberately stops and waits for a human to supply the
code, exactly like the store noVNC logins.

Why our own and not the Strategist's `state.json`: a session is an
identity. The handover was explicit that this project builds its own,
under this project's `.env` and this project's gitignored profile, so
that ownership and credentials sit in one place.

Flow:
  1. Launch chromium through the Israeli exit node into a *persistent*
     profile (the Incapsula/anti-bot cookie and the login both live in
     the profile, so later crawls reuse a warm session).
  2. Fill the ID, click "send me a one-time code".
  3. Print OTP_SENT and poll `data/benefits/otp.txt` for the code. Ishay
     reads the SMS and the code is written there (by the operator running
     this, i.e. me relaying what Ishay says).
  4. Enter the code, submit, verify we left the login page, save the
     storage_state next to the profile.

Run:
    PLAYWRIGHT_PROXY=socks5://127.0.0.1:1055 \
    .venv/bin/python scripts/behatsdaa_login.py

Nothing here reads, stores, or touches payment data. It logs a session
in and stops. Harvesting is separate code, gated behind this.
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
BENEFITS_DIR = BASE / "data" / "benefits"
PROFILE_DIR = BENEFITS_DIR / "behatsdaa_profile"
STATE_PATH = BENEFITS_DIR / "behatsdaa_state.json"
OTP_PATH = BENEFITS_DIR / "otp.txt"

SITE = "https://www.behatsdaa.org.il/"
ID_SELECTOR = "#loginIdWithShortCode"
CODE_SELECTOR = "#shortCode"
SEND_OTP_TEXTS = ("שלחו לי קוד חד פעמי", "קוד חד פעמי")
SUBMIT_TEXTS = ("התחברות", "כניסה")
OTP_WAIT_SECONDS = 240
# A real browser UA; the anti-bot fingerprints headless Chromium, and a
# mobile UA is what the Strategist's working login used.
UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


def _env(key: str) -> str:
    """Read one key from this project's .env without importing anything."""
    env_file = BASE / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return os.environ.get(key, "")


def _log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    from playwright.sync_api import sync_playwright

    proxy = os.environ.get("PLAYWRIGHT_PROXY", "socks5://127.0.0.1:1055")
    login_id = _env("BEHATSDAA_ID")
    if not login_id:
        _log("ERROR: BEHATSDAA_ID not set in .env — cannot log in.")
        return 2

    BENEFITS_DIR.mkdir(parents=True, exist_ok=True)
    if OTP_PATH.exists():
        OTP_PATH.unlink()  # never reuse a stale code

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=True,
            proxy={"server": proxy},
            locale="he-IL",
            timezone_id="Asia/Jerusalem",
            user_agent=UA,
            viewport={"width": 400, "height": 850},
            args=["--no-sandbox"],
        )
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            pg.goto(SITE, wait_until="domcontentloaded", timeout=60_000)
            pg.wait_for_timeout(5_000)

            # Dismiss any cookie/intro overlay that would eat the first click.
            for text in ("אישור", "קבל", "סגור", "הבנתי"):
                try:
                    el = pg.get_by_text(text, exact=False).first
                    if el.count() and el.first.is_visible():
                        el.first.click(timeout=2_000)
                        pg.wait_for_timeout(500)
                except Exception:
                    pass

            if not pg.locator(ID_SELECTOR).count():
                # Already logged in from the persistent profile.
                if "/login" not in pg.url:
                    ctx.storage_state(path=str(STATE_PATH))
                    _log(f"ALREADY_LOGGED_IN url={pg.url} — state saved, nothing to do.")
                    return 0
                _log("ERROR: login field not found and not logged in.")
                pg.screenshot(path=str(BENEFITS_DIR / "login_error.png"))
                return 1

            pg.fill(ID_SELECTOR, login_id, timeout=15_000)
            pg.wait_for_timeout(500)

            clicked = False
            for text in SEND_OTP_TEXTS:
                try:
                    el = pg.get_by_text(text, exact=False).first
                    if el.count() and el.first.is_visible():
                        el.first.click(timeout=5_000)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                _log("ERROR: could not find the send-OTP button.")
                pg.screenshot(path=str(BENEFITS_DIR / "login_error.png"))
                return 1
            pg.wait_for_timeout(3_000)

            _log(
                f"OTP_SENT — a code was sent to Ishay's SMS and email.\n"
                f"Waiting up to {OTP_WAIT_SECONDS}s for the code in {OTP_PATH}."
            )

            code = ""
            deadline = time.time() + OTP_WAIT_SECONDS
            while time.time() < deadline:
                if OTP_PATH.exists():
                    raw = OTP_PATH.read_text(encoding="utf-8")
                    code = re.sub(r"\D", "", raw)
                    if len(code) >= 4:
                        break
                time.sleep(2)
            if not code:
                _log("ERROR: no OTP supplied within the window. Re-run to retry.")
                return 1

            field = pg.locator(CODE_SELECTOR)
            if not field.count():
                # Fall back to the first visible input that is not the ID box.
                field = pg.locator("input:visible").filter(
                    has_not=pg.locator(ID_SELECTOR)
                ).first
            field.click()
            field.fill(code, timeout=10_000)
            pg.wait_for_timeout(1_000)

            for text in SUBMIT_TEXTS:
                try:
                    btn = pg.get_by_text(text, exact=False).first
                    if btn.count() and btn.first.is_visible():
                        btn.first.click(timeout=6_000)
                        break
                except Exception:
                    continue
            pg.wait_for_timeout(4_000)

            ok = "/login" not in pg.url and pg.locator(ID_SELECTOR).count() == 0
            ctx.storage_state(path=str(STATE_PATH))
            if OTP_PATH.exists():
                OTP_PATH.unlink()  # do not leave a used code on disk
            _log(f"{'LOGGED_IN' if ok else 'CHECK_FAILED'} ok={ok} url={pg.url} — state saved.")
            return 0 if ok else 1
        finally:
            ctx.close()


if __name__ == "__main__":
    raise SystemExit(main())
