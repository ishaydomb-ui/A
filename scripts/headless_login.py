"""Attempt a headless username/password login, no display required.

This is the alternative to scripts/login_helper.py's manual noVNC flow,
per CLAUDE.md's open question about whether Shufersal login actually needs
an interactive step at all. `eshaham/shufersal-automation` (a maintained
Node/Puppeteer library) claims plain username+password login with no OTP —
but that's their library, on their stack; we test the same claim directly
against our own Python/Playwright adapter instead of taking on a second
runtime just to borrow one technique.

Login form fields were read from a real fetch of
https://www.shufersal.co.il/online/he/login on 2026-08-29 (through the
Israeli proxy): the form is `#loginForm`, posting to
`/online/he/j_spring_security_check`, with `#j_username` (email) and
`#j_password` fields. Nothing about an OTP step being *required* was
visible in that markup, but that is only evidence from the page's HTML,
not a guarantee about what the server does after submit — hence the
explicit OTP/failure detection below rather than assuming success.

Usage:
    SHUFERSAL_USERNAME=... SHUFERSAL_PASSWORD=... \\
        python3 scripts/headless_login.py shufersal data/sessions/shufersal_storage_state.json

Exits non-zero with a clear reason on anything other than a confirmed
logged-in landing page — never guesses success. If it detects an OTP
step, that's reported explicitly so the fallback (login_helper.py via
noVNC) can be used instead; nothing here can complete an OTP challenge.
"""
from __future__ import annotations

import os
import sys

LOGIN_URLS = {
    "shufersal": "https://www.shufersal.co.il/online/he/login",
}
ACCOUNT_URL_MARKERS = {
    "shufersal": "myaccount",
}
USERNAME_SELECTOR = "#loginForm #j_username"
PASSWORD_SELECTOR = "#loginForm #j_password"
SUBMIT_SELECTOR = "#loginForm button[type=submit]"
OTP_SELECTOR = ".js-otp-validate-btn, input[name*=otp i], input[id*=otp i]"


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in LOGIN_URLS:
        print(f"Usage: python {sys.argv[0]} <{'|'.join(LOGIN_URLS)}> <output_path.json>")
        raise SystemExit(1)

    store, output_path = sys.argv[1], sys.argv[2]

    username = os.environ.get("SHUFERSAL_USERNAME", "")
    password = os.environ.get("SHUFERSAL_PASSWORD", "")
    if not username or not password:
        print("Set SHUFERSAL_USERNAME and SHUFERSAL_PASSWORD in the environment first.")
        raise SystemExit(1)

    proxy = os.environ.get("PLAYWRIGHT_PROXY", "")
    if not proxy:
        print(
            "Refusing to run without PLAYWRIGHT_PROXY set. Without the Israeli exit, "
            "the login page is a geo-block placeholder (HTTP 200) that would make "
            "this look like a login failure rather than a network problem."
        )
        raise SystemExit(1)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, proxy={"server": proxy})
        context = browser.new_context()
        page = context.new_page()
        page.goto(LOGIN_URLS[store], wait_until="domcontentloaded")

        page.locator(USERNAME_SELECTOR).fill(username)
        page.locator(PASSWORD_SELECTOR).fill(password)
        page.locator(SUBMIT_SELECTOR).first.click()

        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass  # fall through to state checks below regardless

        otp_visible = page.locator(OTP_SELECTOR).count() > 0
        still_on_login = "login" in page.url.lower() and "error" not in page.url.lower()
        login_failed = "error=true" in page.url.lower()

        browser_state_ok = False
        if not (otp_visible or login_failed):
            # Confirm by visiting the account page rather than trusting the
            # post-submit URL alone — some failure modes redirect back to a
            # generic page instead of surfacing ?error=true.
            page.goto("https://www.shufersal.co.il/online/he/myaccount", wait_until="domcontentloaded")
            browser_state_ok = ACCOUNT_URL_MARKERS[store] in page.url.lower()

        if otp_visible:
            print(
                "FAILED: an OTP step appeared after submitting username/password. "
                "Headless login can't complete this — use scripts/login_helper.py "
                "via noVNC instead, which lets you enter the OTP by hand."
            )
            browser.close()
            raise SystemExit(1)

        if login_failed or still_on_login or not browser_state_ok:
            print(
                "FAILED: did not reach the logged-in account page after submitting "
                f"credentials (landed on {page.url}). Could be wrong credentials, a "
                "changed login form, or a block the site presented as something else. "
                "Not saving a session state — use scripts/login_helper.py via noVNC "
                "to log in by hand and see what the site is actually showing."
            )
            browser.close()
            raise SystemExit(1)

        context.storage_state(path=output_path)
        browser.close()

    print(f"Logged in headlessly and saved session to {output_path}.")


if __name__ == "__main__":
    main()
