"""Suggest concrete products for a category request — like a recipe, not an item.

2026-09-22: "הרבה ירקות ופירות שאנחנו לא אוכלים בדרך כלל" was filed as
the items "ירקות" and "פירות", and the list watcher put frozen soup mix,
frozen mixed vegetables and a dried-fruit tray into the real carts. Ishay:
"מבחינתי זה כמו להגיד מתכון". So a category word never becomes a product
(`vnext_semantics.category_only`, refused on every cart path); instead
this module proposes real shelf products the household has *not* been
buying, and the household ticks what it wants. Nothing here writes
anything.

Deterministic, no model call: the chains' catalogue names (in-memory
shortlist, read-only) filtered by the semantic lexicon, minus what was
bought in the last N days and anything ever rejected, ranked
never-bought-first. Produce requests keep only fresh forms -- the exact
failure mode being fixed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import vnext_catalogue, vnext_semantics as sem
from .storage import normalize_term

# Household words -> lexicon category. The household says "ירקות ופירות"
# in one breath; both map to produce, which is one shelf here.
CATEGORY_ALIASES: dict[str, str] = {
    "ירקות": sem.PRODUCE, "ירק": sem.PRODUCE, "פירות": sem.PRODUCE, "פרי": sem.PRODUCE,
    "פירות וירקות": sem.PRODUCE, "ירקות ופירות": sem.PRODUCE,
    "בשר": sem.MEAT_FISH, "בשרים": sem.MEAT_FISH, "דגים": sem.MEAT_FISH, "דג": sem.MEAT_FISH,
    "גבינות": sem.DAIRY, "גבינה": sem.DAIRY, "מוצרי חלב": sem.DAIRY, "חלבי": sem.DAIRY,
    "חטיפים": sem.SNACK, "ממתקים": sem.SNACK,
    "שתייה": sem.BEVERAGE, "משקאות": sem.BEVERAGE,
    "מאפים": sem.BAKERY, "לחמים": sem.BAKERY,
    "קטניות": sem.PANTRY, "תבלינים": sem.PANTRY, "יבשים": sem.PANTRY,
    "קפואים": sem.FROZEN,
}

CATEGORY_LABELS: dict[str, str] = {
    sem.PRODUCE: "ירקות ופירות", sem.MEAT_FISH: "בשר ודגים", sem.DAIRY: "מוצרי חלב",
    sem.SNACK: "חטיפים", sem.BEVERAGE: "משקאות", sem.BAKERY: "מאפים", sem.PANTRY: "מזווה",
    sem.FROZEN: "קפואים",
}

# For a produce request only the raw thing counts. "packaged"/"flavored"
# are not disqualifying (גזר ארוז is still a carrot).
_PROCESSED_FOR_FRESH = frozenset({
    "drink", "pickled", "cooked", "canned", "vacuum", "frozen", "puree", "dried", "powder", "snack",
})

# Produce names the lexicon would pass but which are not a thing to eat
# on its own, or are the processed thing under another word.
_PRODUCE_NOISE = ("סלט", "מרק", "תערובת", "מיקס", "לקט", "מעורב", "מעורבים", "מעורבות", "חתוך", "חתוכים",
                  "פרוס", "פרוסים", "גרוס", "טחון", "מגורד", "קלוף", "שקדים", "אגוזי", "אגוזים", "בוטנים",
                  "פיצוחים", "גרעיני", "גרעינים", "צ'יפס", "ציפס", "קראנצ", "חמוצה", "מסוכר", "מסוכרים",
                  "תה", "קפה", "סירופ", "תמצית", "ריח", "ניחוח", "בטעם", "טעם", "יוגורט", "גלידה",
                  "עוגה", "עוגת", "פאי", "מאפה", "ממולא", "ממולאים", "קציצות", "שניצל", "נקניק",
                  "סבון", "שמפו", "מרכך", "קרם", "ג'ל", "משחת", "נר", "מטהר", "מבשם",
                  "אסקימו", "ארטיק", "מנטוס", "סוכריות", "סוכריה", "מוגז", "מיסט", "מסטיק", "לחתול", "לכלב", "לגור")

_WEIGHT_RE = re.compile(r"\d")
# Fresh produce is sold by the kilo or the unit. A produce noun with a
# small gram weight in the name is a bar, a snack or a spice packet
# ("דניאלה בננה 88 גרם") -- the lexicon cannot see that, the number can.
_GRAMS = re.compile(r"\d+\s*(?:גרם|גר\b|ג')")
_VOLUME = re.compile(r"\d+(?:[.,]\d+)?\s*(?:ליטר|ל'|מ\"ל|מל\b)")
# Drink brands that carry no beverage word in their names.
_DRINK_BRANDS = ("פריגת", "גאמפ", "ספרינג", "פאנטה", "קוקה", "ספרייט", "טמפו", "יפאורה", "נביעות",
                 "עין גדי", "מי עדן", "טרופיקנה", "שוופס", "פפסי", "סנקיסט", "xl", "בלו", "פיוז")
# Spice-shelf words that pass the meat lexicon through a stem collision
# ("כורכום הודי" ~ "הודו") or sit under produce nouns ("שום גרוס").
_PET_NOISE = ("לחתול", "לכלב", "לגור", "לחתולים", "לכלבים")
# Not a shelf thing of the category at all, whatever noun is in the name.
_GENERIC_NOISE = ("כוס ", "לחמניית", "לחמנייה", "לחמניה", "מגש")
_SPICE_NOISE = ("כורכום", "קארי", "תבלין", "תבליני", "תיבול", "פפריקה", "פלפל שחור", "מלח", "אבקת")


@dataclass
class Suggestion:
    name: str                         # the product name as it appears on the shelf
    category: str
    chains: dict = field(default_factory=dict)   # store -> {"code": str, "price": float | None}
    last_bought: str = ""             # ISO date, "" = never in the household's history
    times_bought: int = 0
    reason: str = ""

    @property
    def key(self) -> str:
        return normalize_term(self.name)

    def to_dict(self) -> dict:
        return {"name": self.name, "category": self.category, "chains": self.chains,
                "last_bought": self.last_bought, "times_bought": self.times_bought, "reason": self.reason}


def category_for(words: str) -> str | None:
    """The lexicon category a household's category phrase means, or None."""
    text = (words or "").strip()
    if not text:
        return None
    key = normalize_term(text)
    if key in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[key]
    hits = {CATEGORY_ALIASES[w] for w in key.split() if w in CATEGORY_ALIASES}
    if len(hits) == 1:
        return hits.pop()
    if hits:
        # "ירקות ופירות" is produce; anything mixed across shelves is
        # asked about rather than guessed.
        return sem.PRODUCE if hits == {sem.PRODUCE} else None
    return None


def _ok_for_category(name: str, category: str) -> bool:
    if sem.category_of(name) != category:
        return False
    if category == sem.PRODUCE:
        if sem.forms_of(name) & _PROCESSED_FOR_FRESH:
            return False
        low = sem._m(name)  # noqa: SLF001 - same normaliser the lexicon uses
        if any(sem._m(w) in low for w in _PRODUCE_NOISE):  # noqa: SLF001
            return False
        # Fresh produce is sold by the kilo or the unit: a gram or litre
        # figure in the name is a bar, a can or a bottle whatever the noun.
        if _GRAMS.search(name) or _VOLUME.search(name):
            return False
        if any(sem._m(b) in low for b in _DRINK_BRANDS):  # noqa: SLF001
            return False
    low = sem._m(name)  # noqa: SLF001
    if any(sem._m(w) in low for w in _SPICE_NOISE + _PET_NOISE + _GENERIC_NOISE):  # noqa: SLF001
        return False
    return True


# Packaging words that do not make a different product: "גזר" and
# "גזר ארוז" are the same carrot to the household.
_PACKAGING = {"ארוז", "ארוזה", "ארוזים", "ארוזות", "יחידה", "יח", "תפזורת", "ק", "קג", "מובחר", "מארז",
              "טרי", "טריה", "טריים", "טריות", "במשקל", "שקית", "1"}


def _core(name: str) -> frozenset[str]:
    toks = [t.strip("()'\"") for t in normalize_term(name).replace("/", " ").split()]
    return frozenset(t for t in toks if t and t not in _PACKAGING)


def _same_thing(a: frozenset[str], b: frozenset[str]) -> bool:
    return bool(a) and bool(b) and (a <= b or b <= a)


def _recent_purchases(storage, since_days: int, today: date) -> tuple[dict[str, str], dict[str, int]]:
    """normalised name -> last date bought (any chain), and -> times bought."""
    last: dict[str, str] = {}
    times: dict[str, int] = {}
    cutoff = (today - timedelta(days=since_days)).isoformat()

    def _note(name: str, day: str) -> None:
        key = normalize_term(name or "")
        if not key:
            return
        times[key] = times.get(key, 0) + 1
        if day and day > last.get(key, ""):
            last[key] = day

    for line in storage.tivtaam_purchase_lines():
        _note(line.get("raw_name") or "", str(line.get("order_date") or "")[:10])
    for store in ("shufersal", "tivtaam"):
        try:
            dates = storage.last_purchase_dates(store)
        except Exception:  # noqa: BLE001
            dates = {}
        names = {}
        try:
            for row in storage.list_stock_items(store):
                # `stock_items` is the household's recurring-purchase model;
                # a row there means "bought, more than once" even before a
                # date is known. Dates come from `last_purchase` below.
                names[str(row.get("product_code"))] = row.get("product_name") or ""
                _note(row.get("product_name") or "", "")
        except Exception:  # noqa: BLE001
            pass
        for code, day in (dates or {}).items():
            name = names.get(str(code))
            if name:
                _note(name, day.isoformat() if hasattr(day, "isoformat") else str(day)[:10])
    return {k: v for k, v in last.items()}, times


def suggest(storage, category: str, exclude_recent_days: int = 60, count: int = 6,
            config=None, offset: int = 0, today: date | None = None) -> list[Suggestion]:
    """Up to `count` products of `category` the household has not bought
    in `exclude_recent_days`, never-bought first, one per distinct name.
    `offset` skips the first N for a "show more" page. Pure read."""
    today = today or date.today()
    ttl = float(getattr(config, "catalogue_cache_ttl_seconds", 900) or 900) if config else 900.0
    last, times = _recent_purchases(storage, exclude_recent_days, today)
    cutoff = (today - timedelta(days=exclude_recent_days)).isoformat()
    rejected = {normalize_term(r.get("product_name") or "") for r in storage.list_rejections()}
    rejected.discard("")
    bought_cores = [(k, _core(k)) for k in set(last) | set(times)]
    bought_cores = [(k, c) for k, c in bought_cores if c]

    merged: dict[frozenset, Suggestion] = {}
    for store in ("tivtaam", "shufersal"):
        try:
            rows = vnext_catalogue.shortlist(storage, store, ttl)
        except Exception:  # noqa: BLE001
            rows = []
        for row in rows:
            name = (row.get("name") or "").strip()
            if not name or not _ok_for_category(name, category):
                continue
            key = normalize_term(name)
            if not key or key in rejected:
                continue
            core = _core(name)
            if not core:
                continue
            # Bought under any packaging variant counts as bought.
            bought_as = [k for k, c in bought_cores if _same_thing(core, c)]
            when = max((last.get(k, "") for k in bought_as), default="")
            n_times = sum(times.get(k, 0) for k in bought_as)
            if when and when >= cutoff:
                continue
            group = next((g for g in merged if _same_thing(core, g)), None)
            if group is None:
                merged[core] = Suggestion(name=name, category=category, last_bought=when, times_bought=n_times)
                group = core
            sug = merged[group]
            if len(name) < len(sug.name) and core <= _core(sug.name):
                sug.name = name  # the plainest name for the same thing
            sug.chains.setdefault(store, {"code": row.get("code"), "price": row.get("price")})

    def _price(s: Suggestion) -> float:
        prices = [c["price"] for c in s.chains.values() if c.get("price")]
        return min(prices) if prices else 9e9

    ranked = sorted(
        merged.values(),
        key=lambda s: (s.times_bought > 0, s.last_bought or "", -len(s.chains),
                       _has_digits(s.name), _price(s), len(s.name)),
    )
    for s in ranked:
        s.reason = ("לא קניתם אף פעם" if not s.times_bought
                    else f"נקנה לאחרונה ב-{s.last_bought}" if s.last_bought else "נקנה בעבר")
    return ranked[offset: offset + count]


def _has_digits(name: str) -> int:
    # Plain "בטטה" before "בטטה 1 ק"ג מארז": the simplest name for a
    # produce line is the one the household would say.
    return 1 if _WEIGHT_RE.search(name) else 0


def render(category: str, suggestions: list[Suggestion], chosen: set[str]) -> str:
    label = CATEGORY_LABELS.get(category, category)
    lines = [f"🥦 הצעות ל{label} שלא קניתם לאחרונה — סמנו מה לקחת:"]
    if not suggestions:
        lines.append("לא מצאתי משהו חדש להציע בקטגוריה הזו.")
        return "\n".join(lines)
    for s in suggestions:
        mark = "✅" if s.key in chosen else "⬜"
        prices = [f"{'טיב טעם' if st == 'tivtaam' else 'שופרסל'} ₪{c['price']:.2f}"
                  for st, c in sorted(s.chains.items()) if c.get("price")]
        tail = " · ".join(prices)
        lines.append(f"{mark} {s.name}" + (f" — {tail}" if tail else "") + f" ({s.reason})")
    lines.append("\nכלום לא נוסף עד שתלחצו 'הוסף את המסומנים'.")
    return "\n".join(lines)
