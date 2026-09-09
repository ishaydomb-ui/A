"""Harvest branch addresses for every chain that still lacks them.

`crawl_branches.py` already proved the technique — the site's own
`GetWalletChainBranches` call, observed rather than guessed. Two things
differ here:

- **Target.** That script selects by category or region, which left the
  work half-done: 222 of 972 chains with physical branches had addresses
  and nobody could say which 750 did not. This selects exactly the
  complement, so the job has a finish line.
- **Session.** It used a persistent profile that had gone stale. This
  loads the state captured 2026-09-09, whose marker is an HttpOnly
  `AccessToken` cookie on `back.behatsdaa.org.il` — a different host from
  the page, which is why two earlier watchers sat on a logged-in browser
  and reported nothing.

Read-only throughout: it visits a branch-list page and reads the JSON the
page fetches for itself. Nothing is added to a basket or ordered.
"""
import csv
import glob
import os
import random
import sys
import time

from playwright.sync_api import sync_playwright

LAB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lab_rescue")
LAB = os.path.abspath(LAB)
STATE = os.path.expanduser("~/grocery-automation/data/sessions/behatsdaa_state.json")
OUT = os.path.join(LAB, "branches_all.csv")
PROXY = os.environ.get("PLAYWRIGHT_PROXY", "socks5://127.0.0.1:1055")
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
WMAP = {"מסעדות": "2868", "רשתות בהצדעה": "2110", "מזון+אונליין": "2595",
        "קרפור": "3294", "פייטר": "3336", "מבצע הוקרה 25%": "3131",
        "ראש השנה 30%": "3379"}

MAXN = int(sys.argv[1]) if len(sys.argv) > 1 else 10 ** 9


def _targets():
    """Chains with physical branches whose addresses we do not yet hold."""
    chains = {}
    for row in csv.DictReader(open(os.path.join(LAB, "catalog_full.csv"), encoding="utf-8-sig")):
        cid = (row.get("chainID") or "").strip()
        if cid and cid not in chains:
            chains[cid] = row
    have = set()
    for path in glob.glob(os.path.join(LAB, "branches*.csv")):
        for row in csv.DictReader(open(path, encoding="utf-8-sig")):
            if (row.get("כתובת") or "").strip():
                have.add(row.get("chainID"))
    todo = []
    for cid, row in chains.items():
        if not (row.get("מס׳ערים") or "").strip() or cid in have:
            continue
        wallet = WMAP.get(row.get("ארנק"))
        if wallet:
            todo.append((cid, wallet, (row.get("חנות") or "").strip(),
                         row.get("קטגוריה") or "", row.get("אונליין") or ""))
    return todo


def main() -> int:
    todo = _targets()[:MAXN]
    print(f"todo={len(todo)} -> {OUT}", flush=True)
    if not todo:
        return 0

    captured = {}

    def on_response(response):
        if "GetWalletChainBranches" in response.url and "chainId=" in response.url:
            cid = response.url.split("chainId=")[-1].split("&")[0]
            try:
                if "json" in response.headers.get("content-type", ""):
                    captured[cid] = response.json().get("data") or []
            except Exception:  # noqa: BLE001
                pass

    fresh = not os.path.exists(OUT)
    handle = open(OUT, "a", encoding="utf-8-sig", newline="")
    writer = csv.writer(handle)
    if fresh:
        writer.writerow(["chainID", "חנות", "קטגוריה", "אונליין", "סניף",
                         "כתובת", "טלפון", "אתר"])

    done = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, proxy={"server": PROXY})
        ctx = browser.new_context(storage_state=STATE, locale="he-IL",
                                  timezone_id="Asia/Jerusalem", user_agent=UA,
                                  viewport={"width": 390, "height": 844})
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.new_page()
        page.on("response", on_response)
        page.goto("https://www.behatsdaa.org.il/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        if page.locator("#loginIdWithShortCode").count() > 0:
            print("SESSION_EXPIRED — re-login needed", flush=True)
            handle.close()
            ctx.close()
            return 2

        misses = 0
        for cid, wallet, name, category, online in todo:
            try:
                page.goto(
                    f"https://www.behatsdaa.org.il/card/shops/branches"
                    f"?walletId={wallet}&chainId={cid}",
                    wait_until="domcontentloaded", timeout=60000,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  nav {cid}: {type(exc).__name__}", flush=True)
            for _ in range(12):
                page.wait_for_timeout(700)
                if cid in captured:
                    break
            data = captured.get(cid)
            if data is None:
                misses += 1
                # Four blanks in a row is the site throttling or the
                # session dying, not four chains that happen to be empty.
                if misses >= 4:
                    print(f"ABORT after {done} ok: repeated no-data. Re-run to resume.", flush=True)
                    break
            else:
                misses = 0
                done += 1
                for branch in data:
                    writer.writerow([cid, name, category, online,
                                     branch.get("branchName"), branch.get("storeAddress"),
                                     branch.get("storePhone1"), branch.get("webSite")])
                handle.flush()
            if done and done % 25 == 0:
                print(f"...{done} chains done", flush=True)
            time.sleep(3 + random.random() * 2)
        handle.close()
        ctx.close()
        browser.close()
    print(f"RUN_DONE ok_this_run={done}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
