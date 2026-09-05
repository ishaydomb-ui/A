"""Read-only access to the harvested coffeetrail.co.il coffee-cart

directory, for Miri. Same shape as `benefits_catalog.py` on purpose —
same household, same seam pattern (Ishay, 2026-09-05): read what
`scripts/harvest_coffeetrail.py` already produced and hand back plain
dicts. No fetching, no login, nothing live — that stays in the harvest
script, which runs monthly, not on every question.

Data lives under `data/coffeetrail/` (gitignored — see .gitignore for
why: a large regenerable external corpus, not a privacy concern like the
benefits data). Override the directory with `COFFEETRAIL_DATA_DIR` if a
caller runs from elsewhere.

**Two fields are the whole point of this being structured rather than
scraped text**, per Ishay's own framing: `lat`/`lng` and
`opening_hours` are kept as real numbers and a real day/time grammar,
which is what makes "עגלת קפה קרובה" (nearby) and "מה פתוח עכשיו" (open
now) answerable at all — a free-text address or hours string could only
ever be displayed, never computed over.

**Taxonomy membership (region/road/foodtype/type/diners) is best-effort,
not exhaustive** — see the harvester's own docstring for exactly why
(the site paginates large terms via AJAX the harvester does not drive).
Every `region`/`road` lookup here says so in its own output; don't let a
short list read as "that's all of them."
"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_DATA_DIR = "data/coffeetrail"
ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

_DAY_CODES = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
_PY_WEEKDAY_TO_CODE = {0: "Mo", 1: "Tu", 2: "We", 3: "Th", 4: "Fr", 5: "Sa", 6: "Su"}

# Same apostrophe family folded away as benefits_catalog.py, for the same
# reason: a form-miss ("Cafe Rooka" vs "café rooka") must not read as
# "doesn't exist".
_APOSTROPHES = str.maketrans("", "", "'׳’`")


def _norm(text: str) -> str:
    return (text or "").lower().translate(_APOSTROPHES)


def _data_dir() -> Path:
    return Path(os.environ.get("COFFEETRAIL_DATA_DIR", DEFAULT_DATA_DIR))


def load_catalog() -> list[dict]:
    """Every harvested cart, as a list (the harvest stores them dict-keyed
    by slug on disk so a re-run can update one in place; callers get a
    list, since order and the slug-vs-dict distinction are storage
    details, not part of this seam's contract)."""
    path = _data_dir() / "carts.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    return list(data.values())


def load_terms() -> dict:
    """Taxonomy dictionaries + best-effort membership. See module docstring
    for why membership lists are a floor, not a complete list."""
    path = _data_dir() / "terms.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def freshness() -> dict:
    """How current the harvest is: cart count, and the newest and oldest
    `date_modified` seen — so a caller can state an as-of, not assume
    today. Empty dict if nothing has been harvested yet."""
    rows = load_catalog()
    if not rows:
        return {}
    dates = sorted(r.get("date_modified") or "" for r in rows if r.get("date_modified"))
    return {
        "carts": len(rows),
        "oldest_change": dates[0] if dates else "",
        "newest_change": dates[-1] if dates else "",
    }


def _matches(row: dict, query: str) -> bool:
    q = _norm(query)
    return any(
        q in _norm(row.get(field) or "")
        for field in ("name", "legal_name", "description", "address_text")
    )


def search_catalog(query: str) -> list[dict]:
    """Carts whose name, description or address contains the query.

    Plain substring match, case/apostrophe-folded — same policy as
    benefits_catalog.search_catalog, for the same reason: this stays a
    dumb, predictable filter, and any smarter ranking is Miri's to build
    on top.
    """
    q = (query or "").strip()
    if not q:
        return []
    return [row for row in load_catalog() if _matches(row, q)]


_EARTH_RADIUS_KM = 6371.0


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def nearby(lat: float, lng: float, radius_km: float | None = None, rows: list[dict] | None = None) -> list[dict]:
    """Carts within `radius_km` of (lat, lng), nearest first.

    Each returned row gets a `distance_km` key (rounded to 1 decimal).
    Rows with no coordinates are silently excluded — they cannot be
    placed, not "far away". `rows` is injectable for testing; real
    callers omit it and get the full harvested catalog.
    """
    candidates = load_catalog() if rows is None else rows
    out = []
    for row in candidates:
        row_lat, row_lng = row.get("lat"), row.get("lng")
        if row_lat is None or row_lng is None:
            continue
        distance = _haversine_km(lat, lng, row_lat, row_lng)
        if radius_km is not None and distance > radius_km:
            continue
        enriched = dict(row)
        enriched["distance_km"] = round(distance, 1)
        out.append(enriched)
    return sorted(out, key=lambda r: r["distance_km"])


_HOURS_ENTRY_RE = re.compile(
    r"^\s*((?:" + "|".join(_DAY_CODES) + r")(?:\s*,\s*(?:" + "|".join(_DAY_CODES) + r"))*)"
    r"\s+(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$"
)


def _parse_hours_entry(entry: str) -> list[tuple[str, int, int]] | None:
    """One "Mo,Tu 09:00-17:00"-shaped string -> [(day_code, start_min, end_min)].

    Returns None for a string this parser doesn't recognise — callers
    treat that as "can't tell", never as "closed": an unparsed hours
    string is a parser gap, not a fact about the cart.
    """
    match = _HOURS_ENTRY_RE.match(entry or "")
    if not match:
        return None
    days_part, sh, sm, eh, em = match.groups()
    days = [d.strip() for d in days_part.split(",")]
    start = int(sh) * 60 + int(sm)
    end = int(eh) * 60 + int(em)
    return [(day, start, end) for day in days]


def open_now(row: dict, when: datetime | None = None) -> bool | None:
    """Whether `row` is open at `when` (default: now, Israel time).

    Returns `None` — not `False` — when `opening_hours` is empty or
    unparseable: "we don't know" and "closed" are different facts, and
    conflating them is exactly the failure class this project has hit
    before with benefit clubs (see docs/benefits_seam_ground_truth_round3.md,
    L3). A caller asking "what's open now" must be able to tell "closed"
    from "no hours on file" apart.
    """
    entries = row.get("opening_hours") or []
    if not entries:
        return None
    when = (when or datetime.now(ISRAEL_TZ)).astimezone(ISRAEL_TZ)
    today_code = _PY_WEEKDAY_TO_CODE[when.weekday()]
    now_minutes = when.hour * 60 + when.minute
    saw_any_parseable = False
    for entry in entries:
        parsed = _parse_hours_entry(entry)
        if parsed is None:
            continue
        saw_any_parseable = True
        for day, start, end in parsed:
            if day == today_code and start <= now_minutes <= end:
                return True
    return False if saw_any_parseable else None


MAX_RESULTS = 15


def format_catalog_rows(rows: list[dict], query: str = "") -> str:
    if not rows:
        return f'לא נמצא במאגר עגלות הקפה: "{query}"' if query else (
            "מאגר עגלות הקפה ריק — עדיין לא בוצע קציר."
        )
    header = f"*עגלות קפה* — {len(rows)} תוצאות" + (f' עבור "{query}"' if query else "")
    lines = [header]
    for row in rows[:MAX_RESULTS]:
        name = (row.get("name") or "").strip()
        address = (row.get("address_text") or "").strip()
        line = f"• {name}"
        if "distance_km" in row:
            line += f" — {row['distance_km']} ק\"מ"
        details = []
        if address:
            details.append(address)
        status = open_now(row)
        if status is True:
            details.append("פתוח עכשיו")
        elif status is False:
            details.append("סגור עכשיו")
        if row.get("has_map"):
            details.append(row["has_map"])
        if details:
            line += "\n   " + " · ".join(details)
        lines.append(line)
    if len(rows) > MAX_RESULTS:
        lines.append(f"…ועוד {len(rows) - MAX_RESULTS} תוצאות (--json מחזיר הכל)")
    return "\n".join(lines)


def search_by_term(taxonomy: str, slug: str) -> list[dict]:
    """Carts tagged with `slug` under `taxonomy` (region/road/foodtype/
    type/diners), from the best-effort membership crawl. See module
    docstring: this can under-report a large term."""
    terms = load_terms().get(taxonomy) or {}
    entry = terms.get(slug)
    if not entry:
        return []
    by_slug = {row.get("slug"): row for row in load_catalog()}
    return [by_slug[s] for s in entry.get("carts", []) if s in by_slug]
