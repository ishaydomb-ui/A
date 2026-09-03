"""Read-only access to the harvested benefits catalog, for Miri.

This is deliberately the simplest possible seam: read two CSVs the
harvest already produced and hand back plain dicts. No fetching, no
scoring, no "relevance" — that refinement is explicitly Miri's own work
to build on top of this, not duplicated here.

Data lives under `data/benefits/lab_rescue/` (gitignored — see
docs/BENEFITS.md for why: this is the household's financial data and the
repo pushes to GitHub). Override the directory with `BENEFITS_DATA_DIR`
if a caller runs from elsewhere.

**Scope, as of 2026-09-03:** the store catalog (982 stores, manually
tagged with wallets/discount ceilings) and the partial branch-address
crawl. Nothing about individual purchases, wallet balances, or vouchers
is exposed here — that data either was not harvested yet or was
deliberately excluded (see the card-data finding in docs/BENEFITS.md).
"""
from __future__ import annotations

import csv
import os
from pathlib import Path

DEFAULT_DATA_DIR = "data/benefits/lab_rescue"
MAX_RESULTS = 15


def _data_dir() -> Path:
    return Path(os.environ.get("BENEFITS_DATA_DIR", DEFAULT_DATA_DIR))


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_catalog() -> list[dict]:
    """The manually-tagged store catalog: name, wallets, discount ceiling, cities."""
    return _read_csv(_data_dir() / "catalog_tagged.csv")


def load_branches() -> list[dict]:
    """Street-address branches, merged across the partial per-category/city crawls.

    The branch crawl is incremental and resumable by design (see
    docs/BENEFITS.md), so several files exist with overlapping stores.
    Deduped on (chainID, סניף, כתובת) — the same branch name at the same
    address is one row, however many crawl files captured it.
    """
    seen: dict[tuple, dict] = {}
    for path in sorted(_data_dir().glob("branches*.csv")):
        for row in _read_csv(path):
            key = (row.get("chainID", ""), row.get("סניף", ""), row.get("כתובת", ""))
            seen.setdefault(key, row)
    return list(seen.values())


def _matches(row: dict, query: str, fields: tuple[str, ...]) -> bool:
    return any(query in (row.get(field) or "") for field in fields)


def search_catalog(query: str) -> list[dict]:
    """Stores whose name or category contains the query.

    Plain substring match, no fuzzing — the caller (Miri, ultimately)
    decides how forgiving a lookup needs to be; this stays a dumb filter.
    """
    q = (query or "").strip()
    if not q:
        return []
    return [
        row for row in load_catalog()
        if _matches(row, q, ("חנות", "קטגוריה", "תת-קטגוריה"))
    ]


def search_branches(query: str) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []
    return [
        row for row in load_branches()
        if _matches(row, q, ("חנות", "כתובת", "קטגוריה"))
    ]


def format_catalog_rows(rows: list[dict], query: str = "") -> str:
    if not rows:
        return f'לא נמצא בקטלוג ההטבות: "{query}"' if query else "קטלוג ההטבות ריק — עדיין לא בוצע קציר."
    header = f"*קטלוג הטבות* — {len(rows)} תוצאות" + (f' עבור "{query}"' if query else "")
    lines = [header]
    for row in rows[:MAX_RESULTS]:
        name = (row.get("חנות") or "").strip()
        category = (row.get("קטגוריה") or row.get("תת-קטגוריה") or "").strip()
        wallets = (row.get("ארנקים") or "").strip()
        ceiling = (row.get("תקרת הנחה כוללת ₪") or "").strip()
        online = "אונליין" if (row.get("אונליין") or "").strip() == "כן" else ""
        cities = (row.get("ערים") or "").strip()

        line = f"• {name}" + (f" — {category}" if category else "")
        details = []
        if wallets:
            details.append(wallets)
        if ceiling:
            details.append(f"תקרה ₪{ceiling}")
        if online:
            details.append(online)
        if cities:
            details.append(f"ערים: {cities}")
        if details:
            line += "\n   " + " · ".join(details)
        lines.append(line)
    if len(rows) > MAX_RESULTS:
        lines.append(f"…ועוד {len(rows) - MAX_RESULTS} תוצאות (--json מחזיר הכל)")
    return "\n".join(lines)


def format_branch_rows(rows: list[dict], query: str = "") -> str:
    if not rows:
        return f'לא נמצאו סניפים: "{query}"' if query else "אין נתוני סניפים — הזחילה עדיין חלקית."
    header = f"*סניפים* — {len(rows)} תוצאות" + (f' עבור "{query}"' if query else "")
    lines = [header]
    for row in rows[:MAX_RESULTS]:
        name = (row.get("חנות") or "").strip()
        branch = (row.get("סניף") or "").strip()
        address = (row.get("כתובת") or "").strip()
        phone = (row.get("טלפון") or "").strip()

        line = f"• {name}" + (f" ({branch})" if branch and branch != name else "")
        details = [d for d in (address, phone) if d]
        if details:
            line += "\n   " + " · ".join(details)
        lines.append(line)
    if len(rows) > MAX_RESULTS:
        lines.append(f"…ועוד {len(rows) - MAX_RESULTS} תוצאות (--json מחזיר הכל)")
    return "\n".join(lines)
