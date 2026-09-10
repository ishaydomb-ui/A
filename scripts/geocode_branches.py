"""Give branch addresses coordinates and a neighbourhood. Tel Aviv first.

Why this exists: Ishay asked Miri "ומחוץ לקניון, באזור הכללי של שכונת
רמת אביב?" and she correctly answered that she cannot — the project can
answer by *mall* and by *chain*, and nothing else. The reason is in the
data, not the wiring: of 6,136 harvested branch addresses, exactly four
contain the string "רמת אביב", and three of those are the mall itself.
Addresses are street + number + city, with no neighbourhood field at
all, so text search over them can never answer "what is around me".

Geocoding fixes it properly. Nominatim returns both coordinates and a
structured neighbourhood, so one pass buys radius search *and* named
areas — verified on real rows from our own data:

    אבן גבירול 64  -> הצפון החדש - החלק הדרומי
    איינשטיין 40   -> רמת-אביב
    דיזנגוף 50     -> הצפון הישן - החלק הדרומי
    החשמונאים 96   -> גני שרונה
    בן צבי 84      -> יפו

Scoped to Tel Aviv by Ishay's decision 2026-09-10 ("לא נתחיל מכל הארץ.
נתחיל מתל אביב") — 419 distinct addresses rather than 2,814.

**On being a good citizen of a free service.** Nominatim asks for at
most one request a second and discourages bulk use. So this sleeps
between calls, sends a real User-Agent, and — the part that matters —
**caches every answer permanently, including the failures.** A rerun
costs zero requests for anything already seen, which is what keeps this
from becoming bulk use every time someone re-runs it.

Dirty addresses simply fail: "חבר הלאוומים 1" (a typo for הלאומים)
returns nothing. Those are recorded as misses rather than guessed at, so
the gap is visible instead of silently wrong.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grocery_bot import malls  # noqa: E402

CACHE = os.path.expanduser("~/grocery-automation/data/benefits/geocode_cache.json")
ENDPOINT = "https://nominatim.openstreetmap.org/search"
UA = {"User-Agent": "grocery-automation/1.0 (household benefit locator, personal use)"}
DELAY_SECONDS = 1.2  # Nominatim asks for <= 1/s; this leaves headroom.

_HOOD_FIELDS = ("neighbourhood", "suburb", "quarter", "city_district")


def load_cache() -> dict:
    try:
        with open(CACHE, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def save_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as handle:
        json.dump(cache, handle, ensure_ascii=False, indent=1)


def _clean(address: str) -> str:
    """The address as Nominatim prefers it: no "- יפו" tail, single spaces."""
    out = " ".join(str(address or "").split())
    return out.replace("תל אביב - יפו", "תל אביב").strip()


def geocode(address: str, client: httpx.Client) -> dict:
    """One lookup. Returns {} when nothing matched — a miss, not a guess."""
    response = client.get(
        ENDPOINT,
        params={"q": _clean(address), "format": "json", "limit": 1,
                "countrycodes": "il", "addressdetails": 1},
        headers=UA, timeout=30,
    )
    rows = response.json()
    if not rows:
        return {}
    row = rows[0]
    detail = row.get("address", {})
    hood = next((detail[f] for f in _HOOD_FIELDS if detail.get(f)), "")
    return {
        "lat": float(row["lat"]),
        "lon": float(row["lon"]),
        "neighbourhood": hood,
        "city": detail.get("city") or detail.get("town") or "",
        "matched": row.get("display_name", "")[:120],
    }


def addresses_for(city: str) -> list:
    seen = []
    for row in malls.load_rows():
        address = (row.get("כתובת") or "").strip()
        if address and city in address and address not in seen:
            seen.append(address)
    return seen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", default="תל אביב", help="only addresses containing this")
    parser.add_argument("--limit", type=int, default=0, help="stop after N new lookups")
    parser.add_argument("--report", action="store_true", help="summarise the cache and exit")
    args = parser.parse_args()

    cache = load_cache()
    targets = addresses_for(args.city)

    if args.report:
        known = [a for a in targets if a in cache]
        hits = [a for a in known if cache[a]]
        hoods: dict = {}
        for a in hits:
            hoods.setdefault(cache[a]["neighbourhood"] or "(ללא שכונה)", []).append(a)
        print(f"{args.city}: {len(targets)} addresses, {len(known)} looked up, "
              f"{len(hits)} located, {len(known) - len(hits)} misses")
        for hood, rows in sorted(hoods.items(), key=lambda kv: -len(kv[1])):
            print(f"  {len(rows):4}  {hood}")
        return 0

    todo = [a for a in targets if a not in cache]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{args.city}: {len(targets)} addresses, {len(todo)} to look up "
          f"(~{len(todo) * DELAY_SECONDS / 60:.0f} min)", flush=True)

    done = misses = skipped = 0
    with httpx.Client() as client:
        for address in todo:
            result, failed = None, None
            for attempt in range(3):
                try:
                    result = geocode(address, client)
                    break
                except Exception as exc:  # noqa: BLE001
                    failed = exc
                    time.sleep(3 * (attempt + 1))
            if result is None:
                # **Not cached.** A network timeout is not evidence that
                # an address does not exist, and caching it as a miss
                # would make a transient failure permanent and invisible
                # — the address would never be retried and the shop would
                # simply never appear. Skipped now, retried next run.
                skipped += 1
                print(f"  skip (retryable {type(failed).__name__}): {address[:44]}",
                      flush=True)
                continue
            cache[address] = result
            done += 1
            if not result:
                misses += 1
            if done % 25 == 0:
                save_cache(cache)
                print(f"  ...{done}/{len(todo)} ({misses} misses)", flush=True)
            time.sleep(DELAY_SECONDS)

    save_cache(cache)
    print(f"done: {done} looked up, {misses} with no match, {skipped} skipped "
          f"(retryable — rerun to pick them up), cache holds {len(cache)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
