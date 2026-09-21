"""An in-memory shortlist of catalogue names for the vNext resolver.

Phase 2a, blocker #8 of the Phase 1.5 report: a plan build ran ~55
terms × (1 + qualifier words) × 2 chains LIKE '%…%' searches over
~300k `store_prices` rows -- ~0.3 s each, ~2 minutes per plan. A
substring LIKE cannot use an index, so instead the newest name per
product is read **once** per chain (0.55 s measured for Tiv Taam's
25k products, 5.8k for the Shufersal catalogue) and searched in
Python. Same semantics as `Storage.search_store_price_names` /
`search_catalog_names`: every word must be contained, apostrophes
folded, shortest names first, `limit` rows.

Read-only. Cached per process with a TTL from `VNextConfig`; the cache
is keyed by DB path so tests with temp databases never see each other.
"""
from __future__ import annotations

import logging
import time
from bisect import bisect_right
from dataclasses import dataclass

from .storage import _fold_apostrophes

logger = logging.getLogger(__name__)

_CACHE: dict[tuple[str, str], "_Shortlist"] = {}


@dataclass
class _Shortlist:
    built_at: float
    rows: list[dict]           # {code, name, price, folded}, shortest name first
    blob: str                  # "\n".join(folded names) -- one C-speed scan per query
    offsets: list[int]         # start offset of each row's line in `blob`


def _build(storage, store: str) -> _Shortlist:
    rows: list[dict] = []
    if store == "shufersal":
        for r in storage.catalog_names_all():
            rows.append({"code": str(r["item_code"]), "name": r["name"], "price": r.get("price"),
                         "folded": _fold_apostrophes(r["name"] or "")})
    else:
        for r in storage.store_price_names_latest(store):
            rows.append({"code": str(r["barcode"]), "name": r["name"], "price": r.get("price"),
                         "folded": _fold_apostrophes(r["name"] or "")})
    rows.sort(key=lambda r: len(r["name"] or ""))
    offsets, pos, parts = [], 0, []
    for r in rows:
        line = r["folded"].replace("\n", " ")
        offsets.append(pos)
        parts.append(line)
        pos += len(line) + 1
    return _Shortlist(built_at=time.monotonic(), rows=rows, blob="\n".join(parts), offsets=offsets)


def _shortlist(storage, store: str, ttl_seconds: float) -> _Shortlist:
    key = (getattr(storage, "_db_path", "") or repr(storage), store)
    cached = _CACHE.get(key)
    if cached is None or time.monotonic() - cached.built_at > ttl_seconds:
        started = time.monotonic()
        cached = _build(storage, store)
        _CACHE[key] = cached
        logger.info("vNext catalogue shortlist for %s: %d names in %.2fs", store, len(cached.rows),
                    time.monotonic() - started)
    return cached


def shortlist(storage, store: str, ttl_seconds: float) -> list[dict]:
    return _shortlist(storage, store, ttl_seconds).rows


def search(storage, store: str, query: str, limit: int, also: list[str] | None,
           ttl_seconds: float) -> list[dict]:
    """Rows whose name contains `query` and every word of `also`,
    shortest first. The first word is found by scanning one joined
    string (C speed); the rest are checked only on those hits."""
    words = [w for w in [query] + list(also or []) if str(w or "").strip()]
    if not words:
        return []
    folded_words = [_fold_apostrophes(w) for w in words]
    sl = _shortlist(storage, store, ttl_seconds)
    first = folded_words[0]
    out: list[dict] = []
    seen_line = -1
    pos = sl.blob.find(first)
    while pos >= 0 and len(out) < limit:
        line = bisect_right(sl.offsets, pos) - 1
        if line > seen_line:
            seen_line = line
            row = sl.rows[line]
            if all(fw in row["folded"] for fw in folded_words[1:]):
                out.append(row)
            # jump to the next line so one long name is not hit twice
            next_start = sl.offsets[line + 1] if line + 1 < len(sl.offsets) else len(sl.blob)
            pos = sl.blob.find(first, next_start)
        else:
            pos = sl.blob.find(first, pos + 1)
    return out


def clear_cache() -> None:
    _CACHE.clear()
