"""Which benefit chains are in a neighbourhood, or near a point.

The gap this closes, in Ishay's words to Miri on 2026-09-10: *"ומחוץ
לקניון, באזור הכללי של שכונת רמת אביב?"* She answered honestly that she
could only check a named mall or a named chain. The cause was the data:
of 6,136 harvested branch addresses, four contain the string "רמת אביב"
and three are the mall itself. Addresses are street + number + city and
carry no neighbourhood, so no amount of text search could answer it.

`scripts/geocode_branches.py` fills that in — coordinates and a
structured neighbourhood per address — and this reads the result.

**Two things it refuses to do.**

An address that failed to geocode is *invisible* here, so every answer
carries how many addresses in that city could not be placed. Silently
answering from partial data is how "no benefit here" gets said about a
shop that is right there.

And a neighbourhood is only claimed when the cache actually contains it.
`known_areas()` is derived from the data rather than hand-listed,
because a curated list drifts from the harvest and starts refusing
places that are present.
"""
from __future__ import annotations

import json
import math
import os
import re

CACHE = os.path.expanduser("~/grocery-automation/data/benefits/geocode_cache.json")

# OSM writes "רמת-אביב"; a person types "רמת אביב". Folding the hyphen
# is not cosmetic — without it the one neighbourhood Ishay actually asked
# about never matches.
_PUNCT = re.compile(r"[־\-–—\"'`׳״.,]")
_SPACE = re.compile(r"\s+")
_FILLER = ("שכונת", "שכונה", "אזור", "באזור", "ב", "של", "הכללי", "יש", "לי",
           "הנחה", "הנחות", "הטבה", "הטבות", "מה", "אילו", "חנויות")


def _norm(text) -> str:
    out = _PUNCT.sub(" ", str(text or "")).lower()
    words = [w for w in _SPACE.sub(" ", out).split() if w and w not in _FILLER]
    return " ".join(words)


def load_geocodes() -> dict:
    try:
        with open(CACHE, encoding="utf-8") as handle:
            return json.load(handle) or {}
    except (OSError, ValueError):
        return {}


def known_areas(city: str = "תל אביב", geo=None) -> list:
    """Neighbourhoods we can actually answer for, commonest first."""
    geo = load_geocodes() if geo is None else geo
    counts: dict = {}
    for address, hit in geo.items():
        if not hit or city not in address:
            continue
        hood = (hit.get("neighbourhood") or "").strip()
        if hood:
            counts[hood] = counts.get(hood, 0) + 1
    return [h for h, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def resolve_areas(query, city: str = "תל אביב", geo=None) -> list:
    """Every neighbourhood a question covers. Possibly more than one.

    OSM splits some Tel Aviv neighbourhoods into parts — "הצפון הישן"
    exists only as "החלק הדרומי" and "החלק הצפוני". Someone asking about
    הצפון הישן means both, and returning one of them arbitrarily would
    quietly hide half the shops. So a broader question returns every part
    it covers, while a question naming a part returns just that part.
    """
    text = _norm(query)
    if not text:
        return []
    exact = [h for h in known_areas(city, geo) if _norm(h) in text]
    if exact:
        # The question named a specific area (or several). Keep only the
        # longest, so "הצפון הישן - החלק הדרומי" does not also drag in a
        # shorter name contained within it.
        longest = max(len(_norm(h)) for h in exact)
        return [h for h in exact if len(_norm(h)) == longest]
    # No area name contains the question, so the question may be the
    # broader term: return every area whose name contains it.
    return [h for h in known_areas(city, geo) if text and text in _norm(h)]


def resolve_area(query, city: str = "תל אביב", geo=None) -> str:
    """The single best neighbourhood, or "". Prefer `resolve_areas`."""
    found = resolve_areas(query, city, geo)
    return found[0] if found else ""


def coverage(city: str = "תל אביב", geo=None) -> dict:
    """How much of this city we can actually place. Reported with answers."""
    geo = load_geocodes() if geo is None else geo
    looked = [a for a in geo if city in a]
    placed = [a for a in looked if geo[a]]
    return {"looked_up": len(looked), "placed": len(placed),
            "missing": len(looked) - len(placed)}


def _haversine(lat1, lon1, lat2, lon2) -> float:
    radius = 6371.0
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 2 * radius * math.asin(math.sqrt(a))


def chains_in_area(area, rows, geo=None) -> list:
    """(chain, branch, address) for every benefit chain in `area`.

    `area` may be one neighbourhood or several, because a broad question
    covers several of OSM's parts.
    """
    geo = load_geocodes() if geo is None else geo
    wanted = {area} if isinstance(area, str) else set(area)
    found: dict = {}
    for row in rows:
        address = (row.get("כתובת") or "").strip()
        hit = geo.get(address)
        if not hit or (hit.get("neighbourhood") or "").strip() not in wanted:
            continue
        chain = (row.get("חנות") or "").strip()
        if chain and chain not in found:
            found[chain] = (chain, row.get("סניף") or "", address)
    return [found[k] for k in sorted(found)]


def chains_near(lat: float, lon: float, rows, radius_km: float = 1.0, geo=None) -> list:
    """(chain, branch, address, km) within `radius_km`, nearest first.

    The reason geocoding was chosen over a hand-written neighbourhood
    table: this works anywhere, including the case Ishay described of
    standing somewhere with the kids, and it does not care whether the
    place has a name he knows.
    """
    geo = load_geocodes() if geo is None else geo
    best: dict = {}
    for row in rows:
        address = (row.get("כתובת") or "").strip()
        hit = geo.get(address)
        if not hit:
            continue
        km = _haversine(lat, lon, hit["lat"], hit["lon"])
        if km > radius_km:
            continue
        chain = (row.get("חנות") or "").strip()
        if chain and (chain not in best or km < best[chain][3]):
            best[chain] = (chain, row.get("סניף") or "", address, km)
    return sorted(best.values(), key=lambda r: r[3])
