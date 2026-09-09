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
    # What a person calls this place. Ishay will not type the canonical
    # name — he types "רמת אביב", "בקניון רמת אביב" or "ramat aviv" —
    # so the names people actually use are data, not an afterthought.
    aliases: tuple = field(default=())
    # Some malls sit on an ordinary street, so the street name alone
    # over-collects: דיזנגוף 50 is the Center while 116, 122 and 269 are
    # street shops, and החשמונאים carries the TLV mall at 88-132 and
    # unrelated businesses further down. Where that is true the mall
    # states its house numbers and a row without one is refused.
    house_numbers: tuple = field(default=())
    house_range: tuple = field(default=())  # inclusive (low, high)
    # A few rows name the complex instead of a street ("קניון רמת אביב"),
    # carrying no usable number. These terms admit such a row despite a
    # house-number rule, and only these.
    numberless_terms: tuple = field(default=())


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
        aliases=("שבעת הכוכבים", "שבעת הכובים", "7 הכוכבים", "שבעה כוכבים",
                 "seven stars", "shivat hakochavim"),
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
        aliases=("ביג פאשן גלילות", "ביג גלילות", "פאשן גלילות", "גלילות",
                 "big glilot", "glilot"),
    ),
    Mall(
        name="קניון עזריאלי, תל אביב",
        city_terms=("תל אביב",),
        # Both the current street name and the older דרך פ"ת form appear.
        address_terms=("מנחם בגין 132", "בגין 132", "פת 132", "פ ת 132"),
        exclude_terms=(),
        name_hints=("עזריאלי",),
        aliases=("עזריאלי תל אביב", "עזריאלי תא", "עזריאלי", "azrieli"),
    ),
    Mall(
        name="דיזנגוף סנטר, תל אביב",
        city_terms=("תל אביב",),
        # Both spellings appear in the harvest for the same address.
        address_terms=("דיזנגוף סנטר", "דיזינגוף סנטר", "דיזנגוף", "דיזינגוף"),
        exclude_terms=(),
        name_hints=("דיזנגוף סנטר",),
        aliases=("דיזנגוף סנטר", "דיזינגוף סנטר", "דיזנגוף מרכז", "דיזנגוף",
                 "דיזינגוף", "dizengoff center", "dizengoff"),
        # The Center is number 50. דיזנגוף 116, 122 and 269 are shops on
        # the street and must not be folded in — the whole reason this
        # mall states a number at all.
        house_numbers=(50, 1),
        numberless_terms=("דיזנגוף סנטר", "דיזינגוף סנטר"),
    ),
    Mall(
        name="קניון רמת אביב, תל אביב",
        city_terms=("תל אביב",),
        address_terms=("קניון רמת אביב", "איינשטיין", "אינשטיין"),
        # מרכז שוסטר and ברודצקי are separate centres in the same
        # neighbourhood, not this mall; אשדוד has its own אריק איינשטיין
        # street, caught by the city test as well.
        exclude_terms=("שוסטר", "ברודצקי", "אשדוד"),
        name_hints=("רמת אביב",),
        aliases=("קניון רמת אביב", "רמת אביב", "ramat aviv", "ramat aviv mall"),
        # The mall is Einstein 40. Einstein 68 is not it.
        house_numbers=(40,),
        numberless_terms=("קניון רמת אביב",),
    ),
    Mall(
        name="TLV פאשן מול (גינדי), תל אביב",
        city_terms=("תל אביב",),
        address_terms=("החשמונאים",),
        exclude_terms=(),
        name_hints=("גינדי", "TLV"),
        aliases=("tlv פאשן מול", "פאשן מול", "גינדי tlv", "קניון tlv", "גינדי",
                 "tlv fashion mall", "tlv"),
        # One building with entrances across a run of street numbers —
        # 88, 94, 96, 100 and 132 all appear, and every row in that span
        # is a Gindi/TLV branch. Numbers further down HaHashmonaim are
        # ordinary street businesses, hence a range rather than the
        # street name alone.
        house_range=(88, 132),
    ),
)


def _house_number(address: str, street: str):
    """The house number following `street`, or None if there is none.

    Taken relative to the street rather than as "the first number in the
    string", because addresses carry other digits — a row reading
    "החשמונאים 88 88 תל אביב" repeats it, and city names can be followed
    by numbers of their own.
    """
    index = address.find(street)
    if index < 0:
        return None
    match = re.search(r"\d+", address[index + len(street):])
    return int(match.group()) if match else None


def _number_ok(mall: Mall, address: str) -> bool:
    """Whether the house number satisfies a mall that declares one."""
    if not (mall.house_numbers or mall.house_range):
        return True
    if any(term in address for term in mall.numberless_terms):
        return True
    for street in mall.address_terms:
        if street not in address:
            continue
        number = _house_number(address, street)
        if number is None:
            continue
        if number in mall.house_numbers:
            return True
        if mall.house_range and mall.house_range[0] <= number <= mall.house_range[1]:
            return True
    return False


def _claims(mall: Mall, address: str, branch: str) -> bool:
    """True when this mall's address definition admits the row."""
    if any(term in address for term in mall.exclude_terms):
        return False
    if not any(term in address for term in mall.city_terms):
        return False
    if not any(term in address for term in mall.address_terms):
        return False
    return _number_ok(mall, address)


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


# Words that carry no identifying information in a mall query. Stripping
# them lets "בקניון רמת אביב", "הקניון של רמת אביב" and "רמת אביב" all
# reduce to the same thing.
_FILLER = ("קניון", "הקניון", "בקניון", "מתחם", "במתחם", "מרכז מסחרי",
           "mall", "the", "של", "יש", "לי", "הנחה", "הנחות", "הטבה",
           "הטבות", "חנויות", "חנות", "אילו", "איזה", "מה", "כמה", "ב",
           "אני", "עכשיו", "כאן", "פה")
# Hebrew's inseparable prefixes (ב ל מ ה ו כ ש) need no special handling
# here, because matching is by substring: "ברמת אביב" already *contains*
# "רמת אביב". An earlier version stripped them explicitly and applied the
# same stripping to the aliases, which silently corrupted every alias
# beginning with one of those letters — "שבעת הכוכבים" became
# "בעת שבעת הכוכבים" and stopped matching itself. Substring is enough.


# City names that can appear in a question. Used only to *veto* a match
# whose city contradicts the question, never to make one.
_CITIES = ("תל אביב", "הרצליה", "רמת השרון", "חיפה", "ירושלים", "באר שבע",
           "אילת", "נתניה", "חולון", "רמלה", "מודיעין", "עכו", "אשדוד",
           "ראשון לציון", "פתח תקווה", "רעננה", "כפר סבא", "גבעתיים",
           "הוד השרון", "טבריה", "נס ציונה", "רחובות", "עפולה", "בת ים",
           "אשקלון", "כרמיאל", "נהריה", "אור עקיבא", "בית שמש")


def _norm_query(text) -> str:
    out = _PUNCT.sub(" ", str(text or "")).lower()
    out = out.replace("״", " ").replace("׳", " ")
    words = [w for w in _SPACE.sub(" ", out).split() if w and w not in _FILLER]
    return " ".join(words)


def resolve(query) -> str:
    """The mall a person means, from however they happen to say it.

    Ishay types "יש לי הנחה בקניון רמת אביב", not the canonical name, so
    matching has to survive Hebrew prefixes, the word קניון appearing or
    not, both spellings of דיזנגוף, and Latin transliterations.

    Longest alias wins: "עזריאלי" and "עזריאלי תל אביב" both match the
    same mall, but where two malls could claim a query the more specific
    name should decide it.
    """
    text = _norm_query(query)
    if not text:
        return ""
    named_city = next((c for c in _CITIES if c in text), "")
    best_name, best_len = "", 0
    for mall in MALLS:
        # A city in the question that is not this mall's city vetoes it.
        # "קניון עזריאלי חיפה" must not answer for the Tel Aviv Azrieli —
        # the chains are a brand, the mall is a place, and we hold only
        # the Tel Aviv one.
        if named_city and not any(c in text for c in mall.city_terms):
            continue
        for alias in mall.aliases:
            folded = _norm_query(alias)
            if folded and folded in text and len(folded) > best_len:
                best_name, best_len = mall.name, len(folded)
    return best_name


def chains_for_query(query, rows=None) -> tuple:
    """(mall name, chains) for a free-text question, or ("", []) if unknown."""
    name = resolve(query)
    if not name:
        return "", []
    return name, chains_in(name, rows)


def known_malls() -> list:
    """Every mall this module can answer for — for a "which malls?" reply."""
    return [mall.name for mall in MALLS]


def coverage(rows=None) -> dict:
    """How many chains each known mall resolves to — the honest count."""
    rows = load_rows() if rows is None else rows
    return {mall.name: len(chains_in(mall.name, rows)) for mall in MALLS}
