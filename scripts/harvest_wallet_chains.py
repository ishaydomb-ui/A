"""Harvest each wallet's own 'רשימת רשתות' — which chains it can pay at.

Why this exists rather than reading `catalog_tagged.csv`: the club says
plainly that the lists and the rates move — *"רשימת הרשתות וגובה ההנחה
מתעדכנים מעת לעת בהתאם למבצעים"* — and the CSV carries a wallet's rate
but neither its expiry nor whether it can still be loaded. That gap
already produced a wrong answer: on 2026-09-09 the 25% חודש ההוקרה
wallet, expired since 30.6.26, was reported to Ishay as a live option
for קניון רמת אביב. The wallet's own list is the source; the CSV is a
snapshot of it.

**The endpoint is discovered, not assumed.** An earlier probe in this
project invented an endpoint name, got a 404 and learned nothing. This
loads the page and records whatever JSON the page fetches for itself,
then picks out the responses that look like chain lists — so it keeps
working if the API is renamed, and it reports what it saw when it finds
nothing.

Read-only: it never touches /cart, the payment flow, or a load button.
"""
from __future__ import annotations

import csv
import json
import os
import random
import sys
import time

from playwright.sync_api import sync_playwright

OUT_DIR = os.path.expanduser("~/grocery-automation/data/benefits/lab_rescue")
OUT = os.path.join(OUT_DIR, "wallet_chains.csv")
DISCOVERY = os.path.join(OUT_DIR, "wallet_chains_endpoints.json")
STATE = os.path.expanduser("~/grocery-automation/data/sessions/behatsdaa_state.json")
PROXY = os.environ.get("PLAYWRIGHT_PROXY", "socks5://127.0.0.1:1055")
# The iPhone UA the branch harvest uses. Two scripts written the same day
# omitted a user agent entirely and were bounced to /login by the WAF
# with a 200 — the failure looks like a logged-out session, not a block.
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

# walletID -> the name Ishay sees. Ids confirmed against
# `cards/GetCardGeneralInfo` on 2026-09-09.
WALLETS = {
    "3379": "מבצע ראש השנה 30%",
    "3336": "ארנק בתשלום פייטר 15%",
    "2110": "רשתות בהצדעה 15%",
    "2595": "מזון + אתרי אונליין 7%",
    "3294": "קרפור 10%",
    "2868": "מסעדות 20%",
    "3131": "חודש ההוקרה 25% (פג)",
}

# Keys that mark a JSON array as a list of chains rather than of cities,
# categories or banners.
_CHAIN_KEYS = ("chainid", "chainname", "storename", "businessname")


def _chain_rows(payload):
    """Every chain-looking record inside an arbitrary JSON response."""
    found = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            keys = {k.lower() for k in node}
            if any(k in keys for k in _CHAIN_KEYS):
                found.append(node)
            else:
                for value in node.values():
                    walk(value)

    walk(payload)
    return found


def _pick(record, *names):
    for name in names:
        for key, value in record.items():
            if key.lower() == name.lower() and value not in (None, ""):
                return value
    return ""


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    only = sys.argv[1:] or list(WALLETS)
    endpoints = {}
    rows_written = 0

    handle = open(OUT, "w", encoding="utf-8-sig", newline="")
    writer = csv.writer(handle)
    writer.writerow(["walletID", "ארנק", "chainID", "רשת", "הנחה%", "אונליין"])

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, proxy={"server": PROXY})
        ctx = browser.new_context(storage_state=STATE, locale="he-IL",
                                  timezone_id="Asia/Jerusalem", user_agent=UA,
                                  viewport={"width": 390, "height": 844})
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.new_page()
        captured = {}

        def on_response(response):
            if "back.behatsdaa.org.il" not in response.url:
                return
            try:
                if "json" in response.headers.get("content-type", ""):
                    key = response.url.replace("https://back.behatsdaa.org.il/api/", "")
                    captured[key] = response.json()
            except Exception:  # noqa: BLE001
                pass

        page.on("response", on_response)

        page.goto("https://www.behatsdaa.org.il/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(7000)
        if "/login" in page.url:
            print("SESSION_EXPIRED — re-login needed", flush=True)
            handle.close()
            ctx.close()
            return 2

        for wallet_id in only:
            captured.clear()
            name = WALLETS.get(wallet_id, wallet_id)
            try:
                page.goto(f"https://www.behatsdaa.org.il/card/shops?walletId={wallet_id}",
                          wait_until="domcontentloaded", timeout=60000)
            except Exception as exc:  # noqa: BLE001
                print(f"{name}: nav {type(exc).__name__}", flush=True)
                continue
            # The list lazy-loads; scroll until the page stops growing.
            previous = -1
            for _ in range(25):
                page.wait_for_timeout(1400)
                try:
                    height = page.evaluate("() => document.body.scrollHeight")
                    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                except Exception:  # noqa: BLE001
                    break
                if height == previous:
                    break
                previous = height

            best, best_rows = "", []
            for url, payload in captured.items():
                rows = _chain_rows(payload)
                if len(rows) > len(best_rows):
                    best, best_rows = url, rows
            endpoints[wallet_id] = {"name": name, "endpoint": best,
                                    "count": len(best_rows),
                                    "saw": sorted(captured)[:25]}
            seen_ids = set()
            for record in best_rows:
                chain_id = str(_pick(record, "chainID", "chainId", "id"))
                if chain_id in seen_ids:
                    continue
                seen_ids.add(chain_id)
                writer.writerow([
                    wallet_id, name, chain_id,
                    _pick(record, "chainName", "storeName", "businessName", "name"),
                    _pick(record, "discountRate", "discount", "הנחה"),
                    _pick(record, "isOnline", "online"),
                ])
                rows_written += 1
            handle.flush()
            print(f"{name:28} {len(seen_ids):5} chains   via {best[:60] or 'NOTHING FOUND'}",
                  flush=True)
            time.sleep(3 + random.random() * 3)

        ctx.close()
        browser.close()

    handle.close()
    with open(DISCOVERY, "w", encoding="utf-8") as discovery:
        json.dump(endpoints, discovery, ensure_ascii=False, indent=1)
    print(f"RUN_DONE rows={rows_written} -> {OUT}", flush=True)
    # Nothing found is a real failure, not an empty result: it means the
    # page did not render or the shape changed. Say so with an exit code.
    return 0 if rows_written else 3


if __name__ == "__main__":
    raise SystemExit(main())
