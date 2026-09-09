"""Which benefit-carrying chains sit in a given mall.

The question this answers is Ishay's: standing in a mall, which of the
shops around him carry a behatsdaa benefit. That needs a mall→chains
mapping, and the only raw material is the branch list harvested from
behatsdaa, where each row has a branch name and a street address.

**The branch name is a label; the address is the fact.** Matching on the
name over-collects, and not subtly — measured on the harvest of
2026-09-09, "עזריאלי" in a branch name returns shops in Holon, Haifa,
Ramla, Modiin and Akko, because a chain names its branch after the mall
brand wherever that brand happens to be. Worse, "גלילות" in a name
catches קניון פי גלילות, a different site a few minutes away, and one
row whose name says גלילות while its address says הוד השרון.

Names are therefore a *secondary* signal only, used to break ties, never
to admit a branch on their own.

The second trap is that a mall name can be an ordinary street name
somewhere else. שבעת הכוכבים is a street in Eilat as well as the mall in
Herzliya, so every mall here declares the city it is in and rows from
elsewhere are rejected outright.

Addresses arrive dirty — `שד` for `שדרות`, house number 8 or 0 or
missing, and at least one outright typo (`שבעת הכובים`) — so matching
normalises rather than compares literally. Each mall lists the address
forms actually observed, not forms invented in advance; when the harvest
grows, new variants get added here after being seen.
"""
from __future__ import annotations

import csv
import glob
import os
import re
from dataclasses import dataclass, field

DATA_GLOB = os.path.expanduser(
    "~/grocery-automation/data/benefits/lab_rescue/branches*.csv"
)

_PUNCT = re.compile(r"[\"'`׳״.,\-]")
_SPACE = re.compile(r"\s+")


def _norm(text) -> str:
    """Fold the noise that varies between rows for the same place."""
    out = _PUNCT.sub(" ", str(text or ""))
    out = _SPACE.sub(" ", out).strip()
    # `שד'` and `שדרות` are the same street prefix; so are the numberless
    # and numbered forms of the same address.
    out = re.sub(r"^שד(רות)?\b", "שדרות", out)
    return out


@dataclass(frozen=True)
class Mall:
    """A mall, defined by where it is rather than what shops call it."""

    name: str
    city_terms: tuple  # any one of these in the address admits the row
    address_terms: tuple  # the street/complex forms actually observed
    exclude_terms: tuple = field(default=())  # nearby sites that must not merge
    name_hints: tuple = field(default=())  # tie-breakers only, never sufficient


# Ordered most specific first: a row is assigned to the first mall that
# claims it, so a site with a narrow definition wins over a broad one.
MALLS = (
    Mall(
        name="קניון שבעת הכוכבים, הרצליה",
        city_terms=("הרצליה",),
        # The Eilat street of the same name is excluded by the city test,
        # not by an address term — that is the point of having both.
        address_terms=("שבעת הכוכבים", "שבעת הכובים"),
        exclude_terms=("אילת",),
        name_hints=("שבעת הכוכבים",),
    ),
    Mall(
        name="ביג פאשן גלילות",
        # The complex straddles a junction; rows give either the town or
        # the junction itself as the "city".
        city_terms=("רמת השרון", "גלילות"),
        address_terms=("ביג גלילות", "ביג פאשן גלילות", "מתחם ביג",
                       "מתחם גלילות", "צומת גלילות", "רב מכר"),
        # קניון פי גלילות and סינמה סיטי are separate destinations that
        # share the place name; הוד השרון and הרצליה are simply elsewhere.
        exclude_terms=("פי גלילות", "סינמה סיטי", "הוד השרון", "הרצליה"),
        name_hints=("גלילות",),
    ),
    Mall(
        name="קניון עזריאלי, תל אביב",
        city_terms=("תל אביב",),
        # Both the current street name and the older דרך פ"ת form appear.
        address_terms=("מנחם בגין 132", "בגין 132", "פת 132", "פ ת 132"),
        exclude_terms=(),
        name_hints=("עזריאלי",),
    ),
)


def _claims(mall: Mall, address: str, branch: str) -> bool:
    """True when this mall's address definition admits the row."""
    if any(term in address for term in mall.exclude_terms):
        return False
    if not any(term in address for term in mall.city_terms):
        return False
    return any(term in address for term in mall.address_terms)


def load_rows(pattern: str = DATA_GLOB) -> list:
    """Every harvested branch row, de-duplicated across harvest files."""
    seen, rows = set(), []
    for path in sorted(glob.glob(pattern)):
        try:
            handle = open(path, encoding="utf-8-sig")
        except OSError:
            continue
        with handle:
            for row in csv.DictReader(handle):
                key = (row.get("chainID"), row.get("סניף"), row.get("כתובת"))
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
    return rows


def chains_in(mall_name: str, rows=None) -> list:
    """The distinct benefit chains with a branch in this mall.

    Returns (chain, branch, address) sorted by chain, one entry per
    chain — a chain with two units in the same mall is still one answer
    to "what can I use here".
    """
    mall = next((m for m in MALLS if m.name == mall_name), None)
    if mall is None:
        return []
    rows = load_rows() if rows is None else rows
    found = {}
    for row in rows:
        address = _norm(row.get("כתובת"))
        branch = _norm(row.get("סניף"))
        if not _claims(mall, address, branch):
            continue
        chain = (row.get("חנות") or "").strip()
        if chain and chain not in found:
            found[chain] = (chain, row.get("סניף") or "", row.get("כתובת") or "")
    return [found[k] for k in sorted(found)]


def mall_of(address, branch="") -> str:
    """Which known mall this branch sits in, or "" if none."""
    normalised, name = _norm(address), _norm(branch)
    for mall in MALLS:
        if _claims(mall, normalised, name):
            return mall.name
    return ""


def coverage(rows=None) -> dict:
    """How many chains each known mall resolves to — the honest count."""
    rows = load_rows() if rows is None else rows
    return {mall.name: len(chains_in(mall.name, rows)) for mall in MALLS}
