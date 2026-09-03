"""Harvest MAX's public "הטבות פלוס" benefit catalog.

**No login, no credentials, no Israeli exit node.** The catalog is public
marketing material aimed at people who are not customers yet — verified
2026-09-03 by driving the page anonymously and by calling this endpoint
with no session at all. Ishay offered account credentials for this and
they were declined as unnecessary: the account layer (his card, his
transactions) is a different thing we do not need for a catalog.

Permitted: `robots.txt` explicitly `Allow`s `/benefits/bizplus`, and none
of its 192 `Disallow` rules covers `/api`.

The endpoint and its signature were found in the page's own Angular
TransferState blob, not guessed:

    GET /api/benefitsPlus/getDiscountsPlus?isMobile=false&loadLobby=false&page=N

12 records per page, ~11,300 records, so ~945 requests. That is a lot of
someone else's bandwidth, so this is deliberately slow and resumable:
a delay with jitter between pages, a checkpoint after every page, and a
bounded page count so a changed API cannot spin forever. Re-running picks
up where it stopped.

Output: `data/benefits/max_catalog.csv`, normalised to sit alongside the
behatsdaa catalog (see grocery_bot/benefits_catalog.py).
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import time
from pathlib import Path

import httpx

BASE = Path(__file__).resolve().parent.parent
OUT_DIR = BASE / "data" / "benefits"
OUT_CSV = OUT_DIR / "max_catalog.csv"
CHECKPOINT = OUT_DIR / "max_harvest_checkpoint.json"

API = "https://www.max.co.il/api/benefitsPlus/getDiscountsPlus"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
PER_PAGE = 12
# Politeness. ~945 pages at ~1.2s is ~20 minutes; there is no hurry, and
# a burst is how this project got Victory Cloudflare-blocked once already.
DELAY_SECONDS = 1.2
JITTER_SECONDS = 0.6
# A bound, not a cap on the catalog: an unbounded "until isLast" loop
# cannot tell "more pages" from "the API changed and never says isLast".
MAX_PAGES = 1200

CLUB = "מקס"

FIELDNAMES = [
    "club", "חנות", "הנחה%", "קטגוריה", "כתובת", "עיר", "אזור",
    "טלפון", "אתר", "תיאור", "עודכן", "business_id",
]


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def _row(discount: dict) -> dict:
    """One API record -> one normalised catalog row."""
    business = discount.get("business") or {}
    region = (business.get("region") or {}).get("name", "")
    # Category lives in `type.typeName` ("לבית ולגן"), with businessGroup as
    # a fallback. Both arrive as objects, so a bare `or` on them stringifies
    # a whole dict into the column — which is exactly what the first run did.
    type_obj = discount.get("type")
    category = ""
    if isinstance(type_obj, dict):
        category = (type_obj.get("typeName") or "").strip()
    elif type_obj:
        category = str(type_obj).strip()
    if not category:
        group = discount.get("businessGroup") or business.get("businessGroup")
        if isinstance(group, dict):
            category = (group.get("name") or "").strip()
        elif group:
            category = str(group).strip()
    return {
        "club": CLUB,
        "חנות": (discount.get("title") or business.get("name") or "").strip(),
        "הנחה%": discount.get("discountPercent", ""),
        "קטגוריה": category,
        "כתובת": (discount.get("businessAddress") or business.get("displayAddress") or "").strip(),
        "עיר": (business.get("city") or "").strip(),
        "אזור": region,
        "טלפון": (discount.get("businessPhone") or business.get("phoneNumber") or "").strip(),
        "אתר": (discount.get("businessWebsite") or business.get("website") or "").strip(),
        "תיאור": _strip_html(discount.get("description", ""))[:300],
        "עודכן": discount.get("updateDate", ""),
        "business_id": business.get("id", ""),
    }


def _load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        try:
            return json.loads(CHECKPOINT.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"next_page": 0, "seen": []}


def _save_checkpoint(next_page: int, seen: set) -> None:
    CHECKPOINT.write_text(
        json.dumps({"next_page": next_page, "seen": sorted(seen)}, ensure_ascii=False),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES)
    parser.add_argument("--restart", action="store_true", help="ignore the checkpoint")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    state = {"next_page": 0, "seen": []} if args.restart else _load_checkpoint()
    start_page = int(state.get("next_page", 0))
    seen = set(state.get("seen", []))

    # Append if resuming and the file already has rows; otherwise start clean.
    resuming = start_page > 0 and OUT_CSV.exists()
    handle = OUT_CSV.open("a" if resuming else "w", encoding="utf-8-sig", newline="")
    writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
    if not resuming:
        writer.writeheader()

    print(f"{'resuming at' if resuming else 'starting at'} page {start_page}, "
          f"{len(seen)} already collected", flush=True)

    written = 0
    total = None
    try:
        with httpx.Client(
            timeout=45,
            headers={"User-Agent": UA, "Accept": "application/json"},
        ) as client:
            for page in range(start_page, args.max_pages):
                try:
                    response = client.get(
                        API,
                        params={"isMobile": "false", "loadLobby": "false", "page": page},
                    )
                except Exception as exc:
                    print(f"page {page}: request failed ({str(exc)[:80]}) — stopping, "
                          f"re-run to resume", flush=True)
                    break

                if response.status_code != 200:
                    print(f"page {page}: HTTP {response.status_code} — stopping, "
                          f"re-run to resume", flush=True)
                    break

                result = (response.json() or {}).get("result") or {}
                discounts = result.get("discounts") or []
                if total is None:
                    total = result.get("totalDiscounts")
                    print(f"catalog reports {total} discounts", flush=True)

                if not discounts:
                    print(f"page {page}: empty — treating as the end", flush=True)
                    break

                for discount in discounts:
                    row = _row(discount)
                    # Dedupe on business id + title: paging a live list can
                    # repeat a record if the underlying order shifts.
                    key = f"{row['business_id']}|{row['חנות']}"
                    if key in seen:
                        continue
                    seen.add(key)
                    writer.writerow(row)
                    written += 1

                handle.flush()
                _save_checkpoint(page + 1, seen)

                if page % 25 == 0:
                    print(f"page {page}: {len(seen)} unique so far", flush=True)

                if result.get("isLast"):
                    print(f"page {page}: isLast — done", flush=True)
                    break

                time.sleep(DELAY_SECONDS + random.random() * JITTER_SECONDS)
            else:
                print(f"hit the {args.max_pages}-page bound without isLast — "
                      f"stopping deliberately rather than looping", flush=True)
    finally:
        handle.close()

    print(f"wrote {written} new rows, {len(seen)} unique total -> {OUT_CSV}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
