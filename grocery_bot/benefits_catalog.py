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
import re
from pathlib import Path

DEFAULT_DATA_DIR = "data/benefits"
# Data arrived in two waves and lives in two places on purpose: the
# behatsdaa files were *rescued* from another project (lab_rescue/), while
# anything harvested since is written at the root. Rather than move
# someone else's files around, both are searched.
SEARCH_SUBDIRS = ("", "lab_rescue")
MAX_RESULTS = 15

# Data freshness, per club. This is a static snapshot, not a live feed —
# behatsdaa cannot be re-pulled (its login is not automated), so its
# numbers are as-of these dates and no newer. Surfaced in the CLI so a
# caller sees it without reading docs/BENEFITS.md, which carries the full
# table (balances/vouchers age; wallet rates are structural and age well).
DATA_AS_OF = {
    "בהצדעה": "קטלוג נלכד 2026-09-03 · לא מתרענן (התחברות ידנית) · יתרות/שוברים לא כלולים",
    "מקס": "קטלוג נלכד 2026-09-03 · ניתן לרענון (scripts/harvest_max.py)",
}


def _data_dir() -> Path:
    return Path(os.environ.get("BENEFITS_DATA_DIR", DEFAULT_DATA_DIR))


def freshness() -> dict:
    """As-of / refresh status per club — see DATA_AS_OF. For callers that
    need to state how current the data is (e.g. another bot asking)."""
    return dict(DATA_AS_OF)


def known_clubs() -> list[dict]:
    """Clubs declared in eligibility.yaml, harvested or not.

    Returns `[]` if the file is absent — declaring clubs is optional, and
    a missing file must not look like an error. See eligibility.yaml's own
    header for why this file exists at all (household-declared ~6 clubs,
    not ClubHub's 100+).
    """
    path = _data_dir() / "eligibility.yaml"
    if not path.exists():
        return []
    import yaml

    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("clubs") or []


def unharvested_club(query: str) -> str | None:
    """If `query` names a club the household is in but never harvested,
    return its declared name; otherwise `None`.

    Exists to catch a specific silent-wrong shape: a query for an
    unharvested club's *name* (e.g. "כאל") still runs the substring
    search below and can hit unrelated rows purely by spelling
    (מי-**כאל** matches "כאל"), returning what looks like a real —
    if unhelpful — result. A caller trusting that count would report a
    handful of benefits that do not exist, or conclude "no such benefit"
    when the truth is narrower and more useful: we never collected this
    source. So this is checked *before* the substring search, as an
    **exact** name match (not substring) — it identifies "you asked about
    a club we track," not "you asked about anything resembling one."
    Found 2026-09-05 testing round 3 (L3); see docs/BENEFITS.md.
    """
    q = _norm((query or "").strip()).replace('"', "").replace("״", "")
    if not q:
        return None
    for club in known_clubs():
        name = str(club.get("name") or "")
        if not name or club.get("harvested", False):
            continue
        if _norm(name).replace('"', "").replace("״", "") == q:
            return name
    return None


def _find(pattern: str) -> list[Path]:
    """Files matching `pattern` across the benefits dir and its lab_rescue/."""
    found: list[Path] = []
    root = _data_dir()
    for sub in SEARCH_SUBDIRS:
        directory = root / sub if sub else root
        if directory.is_dir():
            found.extend(sorted(directory.glob(pattern)))
    return found


_CHAIN_ID_CACHE: dict[str, str] | None = None
_ADDRESS_CACHE: dict[str, str] | None = None


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _chain_ids_by_name() -> dict[str, str]:
    """Store name -> chainID, from the raw harvest.

    The join key the curated file lost. Cached per process: this runs on
    every catalogue load and the file is a few thousand rows.
    """
    global _CHAIN_ID_CACHE
    if _CHAIN_ID_CACHE is None:
        found: dict[str, str] = {}
        for path in _find("catalog_full.csv"):
            for row in _read_csv(path):
                name = (row.get("חנות") or "").strip()
                if name and row.get("chainID"):
                    found.setdefault(name, str(row["chainID"]).strip())
        _CHAIN_ID_CACHE = found
    return _CHAIN_ID_CACHE


def _addresses_by_chain() -> dict[str, str]:
    """chainID -> one representative street address.

    A chain has many branches; the catalogue row is the chain, so it
    carries the first real address as evidence that we know where this
    merchant physically is. Per-branch detail stays in load_branches().
    """
    global _ADDRESS_CACHE
    if _ADDRESS_CACHE is None:
        found: dict[str, str] = {}
        for row in load_branches():
            chain_id = str(row.get("chainID") or "").strip()
            address = (row.get("כתובת") or "").strip()
            if chain_id and address and chain_id not in found:
                found[chain_id] = address
        _ADDRESS_CACHE = found
    return _ADDRESS_CACHE


def load_catalog() -> list[dict]:
    """Every club's catalog, in one list, each row carrying its `club`.

    Two shapes are merged deliberately rather than forced into one schema:

    - **behatsdaa** (`lab_rescue/catalog_tagged.csv`) — 982 stores, manually
      tagged with wallets and discount ceilings. Its rows have no `club`
      column of their own, so one is added here.
    - **מקס** (`max_catalog.csv`) — harvested from MAX's public API, which
      carries per-branch address, city, region and phone but no ceiling,
      because a card-linked discount has no wallet balance to cap.

    Columns therefore differ between clubs, and that is honest: a caller
    reading `תקרת הנחה כוללת ₪` on a MAX row gets nothing because MAX has
    no such concept, not because the harvest missed it. Filter on `club`
    when the distinction matters.
    """
    rows: list[dict] = []
    chain_ids = _chain_ids_by_name()
    addresses = _addresses_by_chain()
    for path in _find("catalog_tagged.csv"):
        for row in _read_csv(path):
            row.setdefault("club", "בהצדעה")
            # The tagged file was hand-curated without `chainID`, which is
            # the only key the harvested branch data is filed under — so
            # 3,326 real street addresses sat on disk unusable, and the
            # catalogue could answer "which city" but never "where". The
            # id is recovered from the raw harvest by store name, which
            # matches all 982 rows because the tagged file was derived
            # from it in the first place.
            chain_id = chain_ids.get((row.get("חנות") or "").strip())
            if chain_id:
                row.setdefault("chainID", chain_id)
                branch = addresses.get(str(chain_id).strip())
                if branch:
                    row.setdefault("כתובת", branch)
            rows.append(row)
    # MAX, and any club harvested later, land beside the rescued data.
    for path in _find("*_catalog.csv"):
        rows.extend(_read_csv(path))
    return rows


def load_branches() -> list[dict]:
    """Street-address branches, merged across the partial per-category/city crawls.

    The branch crawl is incremental and resumable by design (see
    docs/BENEFITS.md), so several files exist with overlapping stores.
    Deduped on (chainID, סניף, כתובת) — the same branch name at the same
    address is one row, however many crawl files captured it.
    """
    seen: dict[tuple, dict] = {}
    for path in _find("branches*.csv"):
        for row in _read_csv(path):
            key = (row.get("chainID", ""), row.get("סניף", ""), row.get("כתובת", ""))
            seen.setdefault(key, row)
    return list(seen.values())


# Apostrophe family folded away before matching: ASCII ', Hebrew geresh ׳,
# right single quote ', backtick. A query for "terminal x" that returns
# nothing because the row is "Terminal X", or "קוטג' 5%" that misses
# "קוטג 5%", is "I didn't understand the form" masquerading as "doesn't
# exist" — and it stops ask-when-unsure from ever firing, since the miss
# looks like a clean zero. So normalise both sides *before* deciding
# found/one/many. (Approved by Ishay 2026-09-04 as the normalisation step.)
_APOSTROPHES = str.maketrans("", "", "'׳’`")


def _norm(text: str) -> str:
    return (text or "").lower().translate(_APOSTROPHES)


# Hebrew glues these onto the front of a word: "בראשון לציון" is
# "ראשון לציון" with a ב. A substring search never sees through it, so a
# question phrased the way a person speaks matched nothing while the bare
# city name matched 658 rows.
_PREFIXES = ("ב", "ל", "מ", "ה", "ו", "כ", "ש")

# Below this length, stripping a prefix does more harm than good: "בית"
# would become "ית" and match noise.
_MIN_STEM = 3


def _variants(word: str) -> tuple[str, ...]:
    """The word, plus its form without a leading inseparable prefix."""
    if len(word) > _MIN_STEM and word[0] in _PREFIXES:
        return (word, word[1:])
    return (word,)


def _matches_token(row: dict, token: str, fields: tuple[str, ...]) -> bool:
    """Does one word of the query appear anywhere in this row?"""
    haystacks = [_norm(row.get(field) or "") for field in fields]
    return any(
        variant in haystack
        for variant in _variants(_norm(token))
        for haystack in haystacks
    )


def _matches(row: dict, query: str, fields: tuple[str, ...]) -> bool:
    """Every word of the query must appear somewhere in the row.

    Word-by-word rather than as one string. A person asks "קניון בראשון
    לציון"; the catalogue holds "ראשון לציון" in a city field and never
    that whole phrase anywhere, so the literal match returned nothing
    while the city alone returned hundreds. Requiring each word to appear
    *somewhere* in the row keeps the search precise without demanding the
    question be phrased the way the data was filed.
    """
    tokens = [t for t in _norm(query).split() if t]
    if not tokens:
        return False
    return all(_matches_token(row, token, fields) for token in tokens)


def search_catalog(query: str) -> list[dict]:
    """Stores whose name or category contains the query.

    Plain substring match, no fuzzing — the caller (Miri, ultimately)
    decides how forgiving a lookup needs to be; this stays a dumb filter.
    """
    q = (query or "").strip()
    if not q:
        return []
    # `עיר` and `אזור` only exist on MAX rows, `תת-קטגוריה` only on
    # behatsdaa's; `_matches` skips absent fields, so one field list serves
    # both shapes without the caller needing to know which club it hit.
    #
    # `club` is deliberately NOT searched. It reads like a useful filter
    # until you notice "מקס" is a substring of "מקסיקנה", so searching the
    # club name silently returns another club's merchants. Filtering by
    # club is an exact-match job on the `club` field — do it on the rows,
    # not through this substring search.
    # `ערים` is behatsdaa's city field and `עיר` is MAX's — searching
    # only the latter left 916 behatsdaa merchants findable by name and
    # by nothing else, so a city question missed the club that actually
    # carries the household's wallets.
    fields = ("חנות", "קטגוריה", "תת-קטגוריה", "עיר", "ערים", "אזור", "כתובת")
    rows = load_catalog()
    hits = [row for row in rows if _matches(row, q, fields)]

    # Nothing matched every word. Rather than answer "no benefits here"
    # to a question that had one word too many in it, retry on the words
    # that do land — a mall in a city we cover is still a city we cover,
    # and saying so beats a flat no. The caller is told which words were
    # actually used so the answer can say so out loud.
    if not hits:
        tokens = [t for t in _norm(q).split() if t]
        for drop in range(1, len(tokens)):
            kept = tokens[drop:]
            hits = [
                row for row in rows
                if all(_matches_token(row, t, fields) for t in kept)
            ]
            if hits:
                break
    return sorted(hits, key=lambda row: (_location_score(row, q), *_relevance(row, q)))


# Where a row actually *is*, as opposed to what it is called. MAX names
# many merchants "<brand> - <city>" while the address points somewhere
# else entirely — "פינקל בל - ראשון לציון" sits at "קניון מלחה,
# ירושלים". Matching a city word against the merchant name therefore
# proves nothing about geography, so a row that matches inside these
# fields is ranked ahead of one that only matched its name.
_LOCATION_FIELDS = ("עיר", "ערים", "אזור", "כתובת")


# MAX files a shared placeholder address against merchants that are not
# there: 104 rows carry "קניון מלחה, ירושלים" between just two distinct
# address strings, while their own names say גבעתיים, זכרון יעקב, מיטב.
# Measured 2026-09-09. Treating it as a location would put a hundred
# shops in a mall none of them occupy, which is worse than admitting we
# do not know where they are.
_PLACEHOLDER_ADDRESSES = ("קניון מלחה, ירושלים",)


def has_trustworthy_address(row: dict) -> bool:
    address = (row.get("כתובת") or "").strip()
    if not address:
        return False
    return not any(bad in address for bad in _PLACEHOLDER_ADDRESSES)


def mall_of(row: dict) -> str:
    """The mall this row sits in, when its address genuinely says so."""
    import re as _re

    if not has_trustworthy_address(row):
        return ""
    address = row.get("כתובת") or ""
    found = _re.search(r"(קניון\s+[^,]{2,20}|[^,]{2,20}\s+סנטר)", address)
    return found.group(1).strip() if found else ""


def _location_score(row: dict, query: str) -> int:
    """How many query words land in a location field. Higher is better."""
    tokens = [t for t in _norm(query).split() if t]
    return -sum(
        1 for token in tokens if _matches_token(row, token, _LOCATION_FIELDS)
    )


def _relevance(row: dict, query: str) -> tuple[int, int]:
    """Rank the closest name matches first; ties by shorter name.

    Substring search over the whole catalogue returns anything containing
    the query, in file order — so a query for a store surfaces a
    same-named-prefix but unrelated merchant next to the real one (Fox
    fashion vs "פוקס דרי ישראל"). This cannot *separate* those without a
    merchant-identity key the data lacks, but it can at least put an exact
    or word-start name match above one where the query only appears in a
    category or mid-name. A dumb filter with a sensible order.
    """
    # Rank on the normalised forms too, or "Terminal X" would rank below a
    # lowercase match despite being the exact store.
    name = _norm((row.get("חנות") or "").strip())
    query = _norm(query)
    if name == query:
        rank = 0
    elif name.startswith(query):
        rank = 1
    elif re.search(rf"(?:^|\s){re.escape(query)}(?:\s|$)", name):
        rank = 2
    elif query in name:
        rank = 3
    else:
        rank = 4  # matched only a category / city / region field
    return rank, len(name)


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
    # Name the freshness of any club present in the results, so no reader
    # mistakes a static September snapshot for live data.
    clubs_here = {(r.get("club") or "").strip() for r in rows[:MAX_RESULTS]}
    for club in sorted(c for c in clubs_here if c in DATA_AS_OF):
        lines.append(f"_({club}: {DATA_AS_OF[club]})_")
    for row in rows[:MAX_RESULTS]:
        name = (row.get("חנות") or "").strip()
        category = (row.get("קטגוריה") or row.get("תת-קטגוריה") or "").strip()
        club = (row.get("club") or "").strip()
        wallets = (row.get("ארנקים") or "").strip()
        ceiling = (row.get("תקרת הנחה כוללת ₪") or "").strip()
        online = "אונליין" if (row.get("אונליין") or "").strip() == "כן" else ""
        # behatsdaa lists the cities a chain operates in; MAX gives one
        # branch with its own address. Show whichever the row actually has.
        cities = (row.get("ערים") or "").strip()
        percent = str(row.get("הנחה%") or "").strip()
        address = (row.get("כתובת") or "").strip()
        city = (row.get("עיר") or "").strip()

        line = f"• {name}" + (f" — {category}" if category else "")
        if club:
            line += f"  [{club}]"
        details = []
        if wallets:
            details.append(wallets)
        elif percent:
            details.append(f"{percent}% הנחה")
        if ceiling:
            details.append(f"תקרה ₪{ceiling}")
        if online:
            details.append(online)
        if address:
            details.append(address)
        elif city:
            details.append(city)
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
