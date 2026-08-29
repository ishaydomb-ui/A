"""Headless Shufersal login, reusable by both the CLI and the adapter.

Verified against the real site on 2026-08-29: username+password with no
OTP step. The form is `#loginForm`, posting to
`/online/he/j_spring_security_check`, with `#j_username` (email) and
`#j_password`.

This exists as a module rather than only as a script because of the
project's explicit "מינימום תלות במשתמש" rule: the bot must not stop and
ask the user to log in again during ordinary use. A saved session
eventually expires, and when it does the adapter re-runs this on its own
instead of failing the cycle.

Never assumes success. A login that lands anywhere other than a
confirmed logged-in page raises LoginFailed, and an OTP challenge raises
OtpRequired specifically — nothing here can answer a challenge, and that
case needs the human fallback (`scripts/login_helper.py` over noVNC).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

LOGIN_URL = "https://www.shufersal.co.il/online/he/login"
# The hyphen matters: /online/he/myaccount (no hyphen) soft-404s while
# still not containing "login" in the URL, so checking the wrong one
# reports success against a 404 page.
ACCOUNT_URL = "https://www.shufersal.co.il/online/he/my-account"

USERNAME_SELECTOR = "#loginForm #j_username"
PASSWORD_SELECTOR = "#loginForm #j_password"
SUBMIT_SELECTOR = "#loginForm button[type=submit]"
OTP_SELECTOR = ".js-otp-validate-btn, input[name*=otp i], input[id*=otp i]"


class LoginFailed(RuntimeError):
    """Login did not reach a confirmed logged-in page."""


class OtpRequired(LoginFailed):
    """The site asked for a one-time code, which automation cannot answer."""


def login_and_save_session(
    username: str,
    password: str,
    output_path: str,
    proxy: str,
    headless: bool = True,
) -> None:
    """Log in and write a Playwright storage_state file to `output_path`.

    Raises LoginFailed (or OtpRequired) rather than writing a session
    file that isn't actually logged in — a half-valid session file is
    worse than none, because every later cycle fails confusingly instead
    of at the point where the credentials are known to be the problem.
    """
    if not username or not password:
        raise LoginFailed(
            "No Shufersal credentials configured (set SHUFERSAL_USERNAME and "
            "SHUFERSAL_PASSWORD)."
        )
    if not proxy:
        raise LoginFailed(
            "Refusing to log in without a proxy: from outside Israel the login "
            "page is a geo-block placeholder served with HTTP 200, so this "
            "would look like wrong credentials rather than a blocked request."
        )

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, proxy={"server": proxy})
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded")
            page.locator(USERNAME_SELECTOR).fill(username)
            page.locator(PASSWORD_SELECTOR).fill(password)
            page.locator(SUBMIT_SELECTOR).first.click()

            try:
                page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass  # the state checks below decide, not this wait

            if page.locator(OTP_SELECTOR).count() > 0:
                raise OtpRequired(
                    "Shufersal asked for a one-time code. Use "
                    "scripts/login_helper.py over noVNC to log in by hand."
                )

            if "error=true" in page.url.lower():
                raise LoginFailed("Shufersal rejected the credentials.")

            # Confirm against the account page rather than trusting the
            # post-submit URL: some failures redirect to a generic page
            # instead of surfacing an explicit error.
            page.goto(ACCOUNT_URL, wait_until="domcontentloaded")
            if "login" in page.url.lower():
                raise LoginFailed(
                    f"Did not reach the account page after login (landed on {page.url})."
                )

            context.storage_state(path=output_path)
        finally:
            browser.close()
