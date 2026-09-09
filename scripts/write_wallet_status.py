"""Publish behatsdaa wallet *status* as a small file other projects read.

Why this exists. The budget project decodes card charges by rate, and to
avoid attributing a charge to a wallet that can no longer issue one it
hard-coded two expiry dates. Those dates are not a field: they are
parsed out of the wallet's display name ("…עד 30/9/2026"), so a reworded
name loses one silently. It cannot read the API itself — no session in
that project — so the coupling was the only mechanism available to it.

This removes the coupling: the status is written here, where the session
lives, and read there. `isLoadAllowed` is the structural signal that a
wallet is closed and is what a consumer should branch on; the parsed
expiry is included but marked for what it is.

**Balances are deliberately excluded.** They are personal, they change
by the hour, and no consumer of this file needs them — it answers "can
this wallet be used and at what rate", not "how much is in it".

Run it while a behatsdaa session is live; it captures the payload the
account page fetches for itself rather than calling the API directly
(CORS refuses a cross-origin fetch of our own).
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime

OUT = os.path.expanduser("~/grocery-automation/data/benefits/wallet_status.json")
STATE = os.path.expanduser("~/grocery-automation/data/sessions/behatsdaa_state.json")
PROXY = os.environ.get("PLAYWRIGHT_PROXY", "socks5://127.0.0.1:1055")
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

# "עד 30/9/2026" and "עד ה-30.6.26" both appear in wallet names.
_DATE = re.compile(r"עד\s*ה?-?\s*(\d{1,2})[/.](\d{1,2})[/.](\d{2,4})")


def parse_expiry(wallet_name: str) -> str:
    """The expiry inside a display name, ISO, or "" when absent.

    Returns "" rather than guessing when the date is impossible — a
    wallet named "31/9" has no 31st of September, and silently accepting
    or silently skipping it are both how a value stops being read.
    """
    found = _DATE.search(wallet_name or "")
    if not found:
        return ""
    day, month, year = (int(part) for part in found.groups())
    if year < 100:
        year += 2000
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def build(payload: dict) -> dict:
    wallets = []
    for wallet in payload.get("wallets", []):
        name = wallet.get("walletName", "")
        loading = wallet.get("loadingMode") or {}
        wallets.append({
            "wallet_id": str(wallet.get("walletID", "")),
            "name": name.strip(),
            "discount_rate": wallet.get("discountRate"),
            "is_load_allowed": bool(loading.get("isLoadAllowed")),
            "max_balance": float(wallet.get("maxBalance") or 0),
            "max_deposit_per_month": wallet.get("maxDepositForMonth"),
            "max_deposit_yearly": wallet.get("maxDepositYearly"),
            # Marked for what it is: parsed from the display name above,
            # not a field the API returns.
            "expiry_from_name": parse_expiry(name),
        })
    return {
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "source": "behatsdaa api/cards/GetCardGeneralInfo",
        "owner": "grocery-automation (docs/BENEFITS.md)",
        "note": ("is_load_allowed is the structural signal that a wallet is "
                 "closed; expiry_from_name is parsed from the wallet's display "
                 "name and is only as reliable as the club's wording. "
                 "Balances are deliberately not published."),
        "wallets": sorted(wallets, key=lambda w: -(w["discount_rate"] or 0)),
    }


def main() -> int:
    from playwright.sync_api import sync_playwright

    captured = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, proxy={"server": PROXY})
        ctx = browser.new_context(storage_state=STATE, locale="he-IL",
                                  timezone_id="Asia/Jerusalem", user_agent=UA,
                                  viewport={"width": 390, "height": 844})
        page = ctx.new_page()

        def on_response(response):
            if "GetCardGeneralInfo" not in response.url:
                return
            try:
                captured["payload"] = (response.json() or {}).get("data") or {}
            except Exception:  # noqa: BLE001
                pass

        page.on("response", on_response)
        page.goto("https://www.behatsdaa.org.il/", wait_until="domcontentloaded",
                  timeout=60000)
        page.wait_for_timeout(9000)
        if "/login" in page.url:
            print("SESSION_EXPIRED — re-login needed", flush=True)
            ctx.close()
            browser.close()
            return 2
        for _ in range(6):
            if captured:
                break
            page.wait_for_timeout(2500)
        ctx.close()
        browser.close()

    if not captured:
        print("GetCardGeneralInfo not seen — nothing written", flush=True)
        return 3

    data = build(captured["payload"])
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1)
    print(f"wrote {len(data['wallets'])} wallets -> {OUT}", flush=True)
    for wallet in data["wallets"]:
        state = "loadable" if wallet["is_load_allowed"] else "CLOSED"
        print(f"  {wallet['discount_rate']:>5}%  {state:9} "
              f"{wallet['expiry_from_name'] or '(no date in name)':12} "
              f"{wallet['name'][:44]}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
