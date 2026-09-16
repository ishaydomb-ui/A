"""Find candidate malls from coordinates instead of declaring them by hand.

`malls.py` holds seven malls, each hand-written: the city, the address
forms actually observed in the harvest, the house numbers, the aliases a
person would type. That is accurate and it does not scale — every new
mall is a person reading rows and noticing that three spellings mean one
place.

The geocoding pass changed what is possible. 419 Tel Aviv addresses were
looked up and 367 located, so branches now carry coordinates, and
**shops at the same coordinates are in the same building.** A building
holding eight distinct chains is a mall, or near enough to be worth a
person's minute.

This module does not write to `malls.py` and deliberately proposes rather
than decides. A cluster is evidence, not a mall: an office tower with a
busy ground floor looks identical from here, and the difference matters
to someone standing in it.

**What it found on the first run (2026-09-16).** Eleven clusters of eight
or more distinct chains in Tel Aviv: five already fully covered by a
declared mall, three covered in part, and three that no declared mall
claims at all.

The three partials are the point, because a missing address variant is
silent — the mall answers, it just answers short:

- קניון עזריאלי is declared at `מנחם בגין 132`, and 2 chains sit at
  `דרך בגין 121`, which geocodes to the same coordinate.
- דיזנגוף סנטר is declared at `דיזנגוף 50`, and 6 chains sit at
  `דיזינגוף 45` — a different house number *and* a second yod.
- TLV פאשן מול is one chain short at `החשמונאים 96`.

A caution recorded because it was nearly written into the mapping: a
shared coordinate is evidence the rows describe one building, not proof.
`דרך בגין 121` may be the neighbouring tower rather than the mall, and
only a person who has stood there can say. This module proposes; a human
edits `malls.py`.

**One thing it does *not* show, contrary to the first guess.** The two
spellings איינשטיין/אינשטיין geocode 40m apart and so form two separate
clusters, which looked like a mall split in half. It is not: קניון רמת
אביב already declares both spellings as address terms, and the union of
both clusters is exactly the 32 chains it already claims. The clustering
sees a split the text matching never had. Worth stating explicitly —
the cluster view and the mapping view disagree here, and the mapping is
the correct one.
"""
from __future__ import annotations

import collections
import csv
import glob
import json
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

GEOCODE_CACHE = os.path.expanduser(
    "~/grocery-automation/data/benefits/geocode_cache.json"
)
BRANCH_GLOB = os.path.expanduser(
    "~/grocery-automation/data/benefits/lab_rescue/branches*.csv"
)

# Four decimal places is about 11m of latitude — tight enough that two
# sides of a street do not merge, loose enough to absorb the jitter
# between two geocodes of the same building. Chosen by looking at the
# real split above: 0.0003 apart, so a coarser grid would have hidden the
# bug this module exists to surface.
PRECISION = 4

# Below this a cluster is a parade of shops, not a destination. Eight is
# where the Tel Aviv list stops looking like high streets; it is a
# threshold for a person's attention, not a definition of a mall.
MIN_CHAINS = 8


@dataclass
class Cluster:
    """Distinct chains sharing one coordinate, with the addresses seen."""

    lat: float
    lon: float
    chains: set = field(default_factory=set)
    addresses: set = field(default_factory=set)

    @property
    def size(self) -> int:
        return len(self.chains)


def _branch_rows() -> list[dict]:
    rows: list[dict] = []
    for path in sorted(glob.glob(BRANCH_GLOB)):
        try:
            with open(path, encoding="utf-8") as handle:
                rows += list(csv.DictReader(handle))
        except OSError:
            logger.exception("Could not read branch file %s", path)
    return rows


def _geocodes() -> dict:
    try:
        with open(GEOCODE_CACHE, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        logger.warning("No geocode cache at %s; nothing to cluster", GEOCODE_CACHE)
        return {}


def clusters(min_chains: int = MIN_CHAINS, rows=None, geo=None) -> list[Cluster]:
    """Coordinates where several distinct chains sit, largest first."""
    rows = _branch_rows() if rows is None else rows
    geo = _geocodes() if geo is None else geo

    found: dict = {}
    for row in rows:
        address = (row.get("כתובת") or "").strip()
        chain = (row.get("חנות") or "").strip()
        located = geo.get(address)
        if not (chain and located and located.get("lat")):
            continue
        key = (round(located["lat"], PRECISION), round(located["lon"], PRECISION))
        cluster = found.get(key)
        if cluster is None:
            cluster = found[key] = Cluster(lat=key[0], lon=key[1])
        cluster.chains.add(chain)
        cluster.addresses.add(address)
    return sorted(
        (c for c in found.values() if c.size >= min_chains),
        key=lambda c: -c.size,
    )


def audit_known(min_chains: int = MIN_CHAINS) -> list[dict]:
    """Where a cluster disagrees with the malls we already declare.

    Three outcomes per cluster, and only the middle one is interesting:

    - every chain already claimed by a declared mall → nothing to say;
    - *some* chains claimed → the mall is real but its address list is
      missing a variant, which is the קניון רמת אביב case above;
    - none claimed → a candidate nobody has looked at yet.
    """
    from . import malls

    rows = _branch_rows()
    # Claimed *addresses*, not claimed chains. Joining on chain names
    # matched אבן גבירול 71 in Tel Aviv to קניון שבעת הכוכבים in Herzliya,
    # because גולף and המשביר are in both and a chain roster says nothing
    # about where a building is. The address is the fact here for exactly
    # the reason `malls.py` says it is: the name is a label.
    claimed_addresses: dict = {}
    claimed_chains: dict = {}
    for mall in malls.MALLS:
        try:
            triples = malls.chains_in(mall.name, rows)
        except Exception:  # noqa: BLE001
            logger.exception("Could not read chains for %s", mall.name)
            triples = []
        claimed_addresses[mall.name] = {t[2].strip() for t in triples if len(t) > 2}
        claimed_chains[mall.name] = {t[0] for t in triples}

    findings = []
    for cluster in clusters(min_chains, rows=rows):
        overlaps = {
            name: len(cluster.addresses & addresses)
            for name, addresses in claimed_addresses.items()
            if cluster.addresses & addresses
        }
        best = max(overlaps, key=overlaps.get) if overlaps else ""
        # Once the building is identified by address, "covered" is the
        # question that matters: how many of the chains standing here does
        # that mall already know about.
        covered = len(cluster.chains & claimed_chains[best]) if best else 0
        findings.append({
            "lat": cluster.lat,
            "lon": cluster.lon,
            "chains": cluster.size,
            "addresses": sorted(cluster.addresses),
            "matched_mall": best,
            "covered": covered,
            "missing": cluster.size - covered,
            "verdict": (
                "unclaimed" if not best
                else "partial" if covered < cluster.size
                else "covered"
            ),
        })
    return findings


def format_audit(findings: list[dict]) -> str:
    """The audit as something a person can act on."""
    if not findings:
        return "לא נמצאו אשכולות — האם יש קואורדינטות בכלל?"
    lines = []
    partial = [f for f in findings if f["verdict"] == "partial"]
    unclaimed = [f for f in findings if f["verdict"] == "unclaimed"]

    if partial:
        lines.append("*קניונים מוכרים שחסרות להם כתובות* — כל שורה היא וריאנט חסר:")
        for f in partial:
            lines.append(
                f"• {f['matched_mall']} — {f['missing']} רשתות מחוץ למיפוי "
                f"({', '.join(f['addresses'][:2])})"
            )
    if unclaimed:
        lines.append("")
        lines.append("*מועמדים שלא מופו* — אשכול הוא ראיה, לא קניון:")
        for f in unclaimed:
            lines.append(
                f"• {f['chains']} רשתות ב-{', '.join(f['addresses'][:2])}"
            )
    return "\n".join(lines)
