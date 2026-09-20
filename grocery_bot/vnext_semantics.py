"""Meaning before matching — vNext Phase 1.5.

The Phase 1 sample mapped "יוגורט Pro וניל" to a shower gel, "בננה" to a
banana drink, "מלפפונים מיני" to pickles and "סלק (לא באריזת ואקום)" to a
vacuum-packed beet. All four were lexical overlap winning over meaning.
This module is the independent semantic layer the audit asked for: it
parses what the household *said* into a `ParsedTerm` (head noun,
qualifiers, form, negatives) and classifies what a product *is* from
its name alone — never from Tiv Taam's `department`, which is `שונות`
for 390/390 rows.

Everything here is keyword rules over Hebrew product language. That is
deliberate: the rules are inspectable, testable and cheap, and every
violation they raise names the rule, so a wrong rule is a one-line fix
rather than a retrained model. Coverage is honest, not complete — an
unknown word classifies as UNKNOWN, and UNKNOWN never counts as a
category match.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# -- categories ----------------------------------------------------------------

PRODUCE = "produce"
DAIRY = "dairy"
BAKERY = "bakery"
PANTRY = "pantry"
MEAT_FISH = "meat_fish"
FROZEN = "frozen"
SNACK = "snack"
BEVERAGE = "beverage"
PERSONAL_CARE = "personal_care"
HOUSEHOLD = "household"
UNKNOWN = "unknown"

FOOD = {PRODUCE, DAIRY, BAKERY, PANTRY, MEAT_FISH, FROZEN, SNACK, BEVERAGE}
NON_FOOD = {PERSONAL_CARE, HOUSEHOLD}

# Non-food first: a single one of these words decides the category no
# matter how many food words sit beside it ("תחליב רחצה יוגורט וניל").
_PERSONAL_CARE = (
    "תחליב", "רחצה", "שמפו", "מרכך שיער", "סבון", "דאודורנט", "משחת שיניים", "מברשת שיניים",
    "קרם", "ג'ל רחצה", "גל רחצה", "מגבונים", "טיפוח", "בושם", "גילוח", "טמפון", "תחבושות",
    "אל סבון", "מסכת", "לק", "צמר גפן", "מקלוני",
)
_HOUSEHOLD = (
    "נייר טואלט", "שקיות אשפה", "אקונומיקה", "מנקה", "אבקת כביסה", "ג'ל כביסה", "מרכך כביסה",
    "נוזל כלים", "ספוג", "מגבת נייר", "נייר סופג", "נרות", "סוללות", "מטהר אוויר", "מלבין",
    "חיתולים", "טבליות למדיח", "אבקה למדיח", "נייר אפייה", "רדיד", "ניילון נצמד", "שקיות",
    "מסנן", "מטליות", "מגבת", "כפפות",
)
_BEVERAGE_FORM = ("משקה", "מיץ", "נקטר", "שתייה", "לשתייה", "סודה", "קולה", "בירה", "יין", "תמצית",
                  "ליטר משקה", "משקאות")
_DAIRY = ("גו", "יוגורט", "חלב", "גבינה", "גבינת", "קוטג", "שמנת", "חמאה", "לבן", "מעדן", "דנונה", "מולר",
          "יופלה", "מילקי", "אשל", "גיל", "פרוביוטי", "מוצרלה", "קממבר", "בולגרית", "צפתית", "לאבנה",
          "ריקוטה", "מסקרפונה", "עמק", "גלבוע", "טרה", "יטבתה", "תנובה", "שטראוס", "רמת הגולן")
_BAKERY = ("לחם", "פיתה", "פיתות", "לחמניה", "לחמניות", "חלה", "בייגל", "טורטיה", "קרקר", "פריכיות",
           "לחמית", "ברמן", "אנג'ל", "אנגל", "בורקס", "מאפה", "ג'חנון", "מלאווח")
_PANTRY = ("אורז", "פסטה", "ספגטי", "קמח", "סוכר", "מלח", "שמן", "קטניות", "עדשים", "חומוס", "טחינה",
           "סילאן", "דבש", "ריבה", "קפה", "תה", "גרנולה", "דגני", "דגנים", "שיבולת", "קורנפלקס",
           "תבלין", "פפריקה", "כמון", "רוטב", "סויה", "חומץ", "טונה", "שימורי", "שימורים", "דפי אורז",
           "אטריות", "פתיתים", "קוסקוס", "בורגול", "קינואה", "שקדים", "אגוזים", "צימוקים", "מיונז",
           "קטשופ", "חרדל", "אבקת", "תמרים", "שעועית אדומה", "שעועית לבנה", "אפונה", "תירס", "זיתים",
           "מלפפונים בחומץ", "מלפפון בחומץ", "קורנישונים", "כבוש", "כבושים", "פירורי", "ממרח")
_MEAT_FISH = ("עוף", "בקר", "הודו", "פסטרמה", "נקניק", "קבנוס", "שניצל", "דג", "סלמון", "בשר",
              "כבש", "טחון", "כרעיים", "חזה", "כתף", "אנטריקוט", "המבורגר", "קציצות", "נתחי",
              "פרגית", "שוקיים", "כנפיים", "טונה טרייה", "דניס", "לברק", "אמנון")
_FROZEN = ("קפוא", "קפואה", "קפואים", "טבעפרוסט", "סנפרוסט", "גלידה", "שלגון", "ארטיק")
_SNACK = ("חטיף", "חטיפים", "במבה", "ביסלי", "שוקולד", "ממתק", "ממתקים", "עוגיות", "עוגיה", "וופל",
          "וופלים", "ביסקוויט", "סוכריות", "מסטיק", "פופקורן", "צ'יפס", "ציפס", "גמדים", "חטיפטף",
          "עוגה", "עוגות", "קרמבו", "שקדי מרק")
_PRODUCE = ("בננה", "בננות", "תפוח", "תפוחים", "אגס", "אגסים", "סלק", "גזר", "מלפפון", "מלפפונים",
            "עגבניה", "עגבניות", "עגבנייה", "פלפל", "פלפלים", "בצל", "שום", "חסה", "כרוב", "תפוח אדמה",
            "תפוחי אדמה", "בטטה", "חציל", "חצילים", "קישוא", "קישואים", "שזיף", "שזיפים", "נקטרינה",
            "אפרסק", "אפרסקים", "ענבים", "אבטיח", "מלון", "לימון", "לימונים", "תפוז", "תפוזים",
            "קלמנטינה", "קלמנטינות", "אבוקדו", "רימון", "רימונים", "תות", "תותים", "פטריות", "פטריה",
            "כוסברה", "פטרוזיליה", "נענע", "שמיר", "בצל ירוק", "ברוקולי", "כרובית", "שעועית ירוקה",
            "אספרגוס", "סלרי", "צנון", "צנונית", "לפת", "קולורבי", "ארטישוק", "דלעת", "מנגו", "אננס",
            "קיווי", "דובדבן", "דובדבנים", "משמש", "אשכולית", "פומלה", "פומלית", "אפרסמון", "תאנים",
            "ליצי", "פסיפלורה", "גויאבה", "עלי", "תרד", "מנגולד", "בזיליקום", "רוקט", "ג'ינג'ר",
            "זנגביל", "כרוב סגול", "כרוב לבן", "שומר", "כרישה", "בצל סגול", "בצל אדום", "בצל יבש",
            "פטריות שמפיניון", "עגבניות שרי", "שרי")

# Form markers: what was *done* to the thing. Some belong to other
# categories too (מיץ is a beverage) — they are listed here for the
# produce-vs-processed rule, which is about the request, not the shelf.
_FORMS: dict[str, tuple[str, ...]] = {
    "drink": _BEVERAGE_FORM,
    "pickled": ("בחומץ", "חמוץ", "חמוצים", "כבוש", "כבושים", "קורנישונים", "קורשנישונים", "במלח", "מוחמץ"),
    "cooked": ("מבושל", "מבושלת", "מבושלים", "מקולף", "מקולפים", "אפוי", "צלוי", "מטוגן", "מוכן"),
    "canned": ("שימורים", "שימורי", "בקופסה", "קופסת", "פחית"),
    "vacuum": ("ואקום", "וואקום", "בואקום", "vacuum"),
    "frozen": _FROZEN,
    "puree": ("מחית", "רסק", "ממרח", "רוטב", "ריבה", "קונפיטורה", "מרקחת"),
    "dried": ("מיובש", "מיובשים", "מיובשות", "צימוקים", "יבשים"),
    "powder": ("אבקת", "אבקה"),
    "snack": _SNACK,
    "flavored": ("בטעם", "טעם"),
    "packaged": ("ארוז", "ארוזים", "ארוזות", "מארז", "בשקית", "שקית", "יחידות", "יח'"),
}
# Forms that make a fresh-produce request unmet.
_PROCESSED_FORMS = ("drink", "pickled", "cooked", "canned", "vacuum", "frozen", "puree", "dried",
                    "powder", "snack", "flavored")

# Qualifiers the household states that the product must carry (soft:
# absence is "unverified", presence of a conflicting one is a violation).
_SIZE_SMALL = ("מיני", "קטן", "קטנים", "קטנות", "בייבי", "זעיר", "זעירים", "שרי")
_SIZE_LARGE = ("גדול", "גדולים", "גדולות", "ענק", "ענקיים", "ג'מבו", "משפחתי")
_COLORS = ("אדום", "אדומים", "אדומה", "צהוב", "צהובים", "צהובה", "ירוק", "ירוקים", "ירוקה", "כתום",
           "כתומים", "סגול", "סגולים", "לבן", "לבנים", "שחור", "שחורים", "ורוד")
_VARIETY = ("במגוון צבעים", "בכמה צבעים", "מגוון צבעים", "צבעוני", "צבעוניים", "מיקס", "מעורב",
            "תלת צבעי", "שלושה צבעים", "בשלושה צבעים")
_FLAVORS = ("וניל", "שוקולד", "תות", "בננה", "אפרסק", "פירות יער", "קרמל", "קפה", "לימון", "תפוח",
            "אגס", "מנגו", "קוקוס", "פיסטוק", "פטל", "דובדבן", "עוגת גבינה", "טבעי")
# "Pro" is a line name that the shelves spell three ways.
_BRAND_ALIASES = {
    "pro": ("pro", "פרו", "פרוטאין", "protein", "חלבון"),
    "go": ("go", "גו"),
}
_BRAND_PHRASES = ("דגנית עין בר", "אנג'ל", "אנגל", "ברמן", "תנובה", "שטראוס", "מולר", "דנונה", "יופלה", "טרה",
                  "יטבתה", "עלית", "אסם", "תלמה", "מהדרין", "רמת הגולן", "עמק", "גלבוע", "טבעפרוסט", "סנפרוסט",
                  "ריו מרה", "סימפוניה", "פרי גליל", "זוגלובק", "טירת צבי", "ויליפוד", "מזרח ומערב", "אולאין")
# A stated qualifier that the shelf contradicts with its opposite.
_QUALIFIER_CONFLICTS = {
    "מלא": ("לבן", "לבנה"), "מלאה": ("לבן", "לבנה"), "לבן": ("מלא", "מלאה", "מחיטה מלאה"),
    "עמיד": ("טרי", "טרייה"), "טרי": ("עמיד", "קפוא", "קפואה"), "קפוא": ("טרי", "טרייה"),
    "אורגני": (), "מלוח": ("מתוק",), "מתוק": ("מלוח", "חריף"), "חריף": ("מתוק",),
}
_FREE_OF_DECLARATIONS = {
    "סוכר": ("ללא תוספת סוכר", "ללא סוכר", "ללס", "נטול סוכר", "0% סוכר", "בלי סוכר", "ללא סוכרים"),
    "לקטוז": ("ללא לקטוז", "נטול לקטוז", "דל לקטוז", "0% לקטוז", "בלי לקטוז"),
    "גלוטן": ("ללא גלוטן", "נטול גלוטן", "בלי גלוטן"),
    "מלח": ("ללא מלח", "דל מלח", "ללא תוספת מלח", "בלי מלח"),
    "שומן": ("0% שומן", "ללא שומן", "דל שומן"),
}
_FREE_OF_CONFLICTS = {
    "סוכר": ("בתוספת סוכר", "ממותק", "מסוכר", "עם סוכר"),
    "לקטוז": (),
    "גלוטן": (),
    "מלח": ("במלח", "מלוח"),
    "שומן": (),
}
_STOPWORDS = ("של", "עם", "את", "או", "ו", "ב", "ל", "על", "לא", "ללא", "בלי", "כמה", "מגוון", "באריזת",
              "אריזת", "אריזה", "בסגנון", "טרי", "טריים", "טרייה", "טריות")

_PAREN = re.compile(r"[()\[\]]")
_TOKEN_SPLIT = re.compile(r"[\s,/\-–]+")


def _norm(text: str) -> str:
    text = str(text or "").replace("’", "'").replace("`", "'").replace("״", '"').replace("׳", "'")
    text = text.replace("'", "")  # קוטג' -> קוטג
    # Final-form letters are the same letter for matching: the stem of
    # מלפפונים is מלפפונ and the singular is מלפפון.
    text = text.translate(_FINALS)
    return " ".join(text.lower().split())


_FINALS = str.maketrans("ךםןףץ", "כמנפצ")


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split(_norm(text)) if t]


def _stem(token: str) -> str:
    """Crude Hebrew plural/feminine stripping, enough for head-noun matching."""
    for suffix in ("יות", "ות", "ים", "ין"):
        if len(token) > 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _has_any(text: str, words) -> str | None:
    """The first marker word present, matched on word boundaries where
    the marker is a single word (so 'לבן' doesn't fire inside 'לבנה'
    unless listed), substring for multi-word markers."""
    toks = set(_tokens(text))
    stems = {_stem(t) for t in toks}
    for w in words:
        wn = _norm(w)
        if " " in wn:
            if wn in text:
                return w
        elif wn in toks or wn in stems or _stem(wn) in stems:
            return w
    return None


# -- category of a product name --------------------------------------------------

def category_of(name: str) -> str:
    """What a shelf product is, from its name. Non-food wins outright;
    then a beverage *form* wins over the food noun inside it (משקה בננה
    is a drink); then the first food noun in reading order decides
    (עוגיות גרנולה are cookies, גרנולה עם פירות is granola)."""
    text = _norm(name)
    if _has_any(text, _PERSONAL_CARE):
        return PERSONAL_CARE
    if _has_any(text, _HOUSEHOLD):
        return HOUSEHOLD
    if _has_any(text, _BEVERAGE_FORM):
        return BEVERAGE
    if _has_any(text, _FROZEN):
        return FROZEN
    order = []
    for cat, words in ((SNACK, _SNACK), (DAIRY, _DAIRY), (BAKERY, _BAKERY), (MEAT_FISH, _MEAT_FISH),
                       (PANTRY, _PANTRY), (PRODUCE, _PRODUCE)):
        pos = _first_position(text, words)
        if pos is not None:
            order.append((pos, cat))
    if not order:
        return UNKNOWN
    order.sort()
    # A processed marker on a produce noun means the product is the
    # processed thing, whatever came first ("מחית תפוח" is a puree).
    first = order[0][1]
    if first == PRODUCE and any(_has_any(text, _FORMS[f]) for f in ("puree", "powder", "dried", "pickled", "canned")):
        return PANTRY
    return first


def _first_position(text: str, words) -> int | None:
    toks = _tokens(text)
    stems = [_stem(t) for t in toks]
    best = None
    for w in words:
        wn = _norm(w)
        if " " in wn:
            idx = text.find(wn)
            if idx >= 0:
                pos = len(text[:idx].split())
                best = pos if best is None else min(best, pos)
            continue
        ws = _stem(wn)
        for i, (t, s) in enumerate(zip(toks, stems)):
            if t == wn or s == wn or s == ws:
                best = i if best is None else min(best, i)
                break
    return best


def forms_of(name: str) -> set[str]:
    text = _norm(name)
    return {form for form, words in _FORMS.items() if _has_any(text, words)}


# -- parsing the household's words --------------------------------------------------

@dataclass
class ParsedTerm:
    raw: str
    head: str                                  # the noun the request is about (normalised)
    head_stem: str
    category: str
    brand: list[str] = field(default_factory=list)
    flavor: list[str] = field(default_factory=list)
    colors: list[str] = field(default_factory=list)
    variety: bool = False                      # "במגוון צבעים" — several kinds wanted
    size: str = ""                             # small | large | ""
    weight: str = ""                           # "800 גרם", "1 ליטר" as written
    fresh: bool = False                        # produce/meat asked without a processed form
    forms: set[str] = field(default_factory=set)   # forms the request itself names (e.g. "קפוא")
    positive: list[str] = field(default_factory=list)   # other required words (עמיד, מלא, אורגני…)
    negatives: list[str] = field(default_factory=list)  # "לא X": product must not carry X
    free_of: list[str] = field(default_factory=list)    # "ללא X": product must declare X-free

    def to_dict(self) -> dict:
        return {
            "raw": self.raw, "head": self.head, "category": self.category, "brand": self.brand,
            "flavor": self.flavor, "colors": self.colors, "variety": self.variety, "size": self.size,
            "weight": self.weight, "fresh": self.fresh, "forms": sorted(self.forms),
            "positive": self.positive, "negatives": self.negatives, "free_of": self.free_of,
        }

    @property
    def qualifiers(self) -> list[str]:
        out = list(self.brand) + list(self.flavor) + list(self.colors) + list(self.positive)
        if self.variety:
            out.append("variety")
        if self.size:
            out.append(f"size:{self.size}")
        if self.weight:
            out.append(self.weight)
        out += [f"not:{n}" for n in self.negatives]
        out += [f"free-of:{n}" for n in self.free_of]
        return out


_WEIGHT = re.compile(r"(\d+(?:[.,]\d+)?)\s*(גרם|ג'|גר|ק\"ג|קג|ק״ג|ליטר|ל'|מ\"ל|מל|יח'|יחידות)")
_NEG_PAREN = re.compile(r"\((לא|ללא|בלי)\s+([^)]+)\)")
_NEG_INLINE = re.compile(r"(?:^|\s)(לא|ללא|בלי|נטול|נטולת)\s+(?:ב|באריזת\s+|אריזת\s+|תוספת\s+)?([^\s(),]+)")


def parse_term(raw: str) -> ParsedTerm:
    text = _norm(raw)
    negatives: list[str] = []
    free_of: list[str] = []
    for m in _NEG_PAREN.finditer(text):
        _collect_negation(m.group(1), m.group(2), negatives, free_of)
    stripped = _NEG_PAREN.sub(" ", text)
    for m in _NEG_INLINE.finditer(stripped):
        _collect_negation(m.group(1), m.group(2), negatives, free_of)
    stripped = _NEG_INLINE.sub(" ", stripped)
    stripped = _PAREN.sub(" ", stripped)

    weight = ""
    wm = _WEIGHT.search(stripped)
    if wm:
        weight = wm.group(0)
        stripped = stripped.replace(wm.group(0), " ")

    brand: list[str] = []
    for phrase in _BRAND_PHRASES:
        pn = _norm(phrase)
        if pn in stripped:
            brand.append(pn)
            stripped = stripped.replace(pn, " ")
    toks = [t for t in _tokens(stripped) if t not in _STOPWORDS]
    brand += [t for t in toks if re.fullmatch(r"[a-z][a-z0-9]*", t)]   # Latin word = a line/brand name
    flavor = [w for w in _FLAVORS if _has_any(stripped, (w,)) and _norm(w) not in _head_candidates(toks)]
    colors = [w for w in _COLORS if _has_any(stripped, (w,))]
    variety = bool(_has_any(text, _VARIETY))
    size = "small" if _has_any(stripped, _SIZE_SMALL) else ("large" if _has_any(stripped, _SIZE_LARGE) else "")
    forms = forms_of(stripped) - {"packaged", "flavored"}

    head = ""
    for t in toks:
        if t in brand or t in {_norm(c) for c in colors} or _norm(t) in {_norm(v) for v in _VARIETY}:
            continue
        if _stem(t) in {_stem(_norm(s)) for s in _SIZE_SMALL + _SIZE_LARGE}:
            continue
        head = t
        break
    head = head or (toks[0] if toks else text)
    category = category_of(head) if head else UNKNOWN
    if category == UNKNOWN:
        category = category_of(stripped)
    fresh = category in (PRODUCE, MEAT_FISH) and not (forms & set(_PROCESSED_FORMS))

    known = {head} | set(brand) | {_norm(f) for f in flavor} | {_norm(c) for c in colors}
    positive = [t for t in toks if t not in known and _stem(t) not in {_stem(_norm(s)) for s in _SIZE_SMALL + _SIZE_LARGE}
                and t not in {w for v in _VARIETY for w in _norm(v).split()}]
    # Known brand aliases also count as brand even when written in Hebrew.
    for key, aliases in _BRAND_ALIASES.items():
        if any(a in toks for a in aliases if not re.fullmatch(r"[a-z]+", a)):
            brand.append(key)
            positive = [p for p in positive if p not in aliases]
    return ParsedTerm(raw=str(raw), head=head, head_stem=_stem(head), category=category, brand=sorted(set(brand)),
                      flavor=flavor, colors=colors, variety=variety, size=size, weight=weight, fresh=fresh,
                      forms=forms, positive=positive, negatives=negatives, free_of=free_of)


def _head_candidates(toks: list[str]) -> set[str]:
    return {toks[0]} if toks else set()


def _collect_negation(word: str, what: str, negatives: list[str], free_of: list[str]) -> None:
    what = _norm(what).strip()
    if not what:
        return
    if word == "לא":
        negatives.append(what)
    else:  # ללא / בלי / נטול: the product is *declared* free of it
        free_of.append(what)


# -- violations: does this product satisfy that request? ------------------------------

@dataclass(frozen=True)
class Violation:
    rule: str                 # short machine name
    detail: str               # one human line
    hard: bool = True         # hard -> rejected; soft -> substitution candidate at best


def violations(term: ParsedTerm, product_name: str) -> list[Violation]:
    """Every way `product_name` fails `term`. Empty means no known
    conflict — it does NOT mean every qualifier was verified; see
    `unverified()` for that."""
    name = _norm(product_name)
    out: list[Violation] = []
    pcat = category_of(name)
    pforms = forms_of(name)

    if term.category in FOOD and pcat in NON_FOOD:
        out.append(Violation("category:non_food", f"request is food ({term.category}), product is {pcat}"))
    if term.category == PRODUCE and pcat == BEVERAGE and "drink" not in term.forms:
        out.append(Violation("category:beverage_for_produce", "produce requested, product is a drink"))
    if term.category in (PRODUCE, DAIRY, BAKERY, PANTRY, MEAT_FISH) and pcat in FOOD and pcat != term.category \
            and pcat not in (UNKNOWN,) and not (term.category == PANTRY and pcat == PRODUCE) \
            and not (pcat == FROZEN and "frozen" in term.forms):
        # A different food category is a mismatch unless the request
        # itself named that form (a frozen request may hit FROZEN).
        if not (term.category == PRODUCE and pcat == PANTRY and _head_in(term, name) and not (pforms & set(_PROCESSED_FORMS))):
            out.append(Violation("category:mismatch", f"request is {term.category}, product reads as {pcat}"))
    if term.fresh:
        hit = pforms & set(_PROCESSED_FORMS)
        if hit:
            out.append(Violation("form:processed_for_fresh", f"fresh {term.category} requested, product is {'/'.join(sorted(hit))}"))
    for neg in term.negatives:
        words = [w for w in neg.split() if w not in _STOPWORDS]
        hit = _mentions(name, neg) or any(_mentions(name, w) or _mentions(name, "ב" + w) for w in words)
        if hit:
            out.append(Violation(f"negative:{neg}", f"request excludes '{neg}', product carries it"))
    for attr in term.free_of:
        key = next((k for k in _FREE_OF_DECLARATIONS if k in attr), None)
        if key and any(c in name for c in _FREE_OF_CONFLICTS.get(key, ())):
            out.append(Violation(f"free_of:{key}:conflict", f"request wants no {key}, product says it has it"))
        elif key and not any(d in name for d in _FREE_OF_DECLARATIONS[key]):
            out.append(Violation(f"free_of:{key}:undeclared", f"request requires '{attr}', product does not declare it"))
        elif not key and not _mentions(name, f"ללא {attr}") and not _mentions(name, f"בלי {attr}"):
            out.append(Violation(f"free_of:{attr}:undeclared", f"request requires 'ללא {attr}', product does not declare it"))
    if term.flavor:
        found = [f for f in term.flavor if _mentions(name, f)]
        other = [f for f in _FLAVORS if _mentions(name, f) and f not in term.flavor and f != term.head]
        if not found and other:
            out.append(Violation("flavor:conflict", f"requested {'/'.join(term.flavor)}, product is {'/'.join(other)}"))
        elif not found:
            out.append(Violation("flavor:missing", f"requested flavor {'/'.join(term.flavor)} not in product name"))
    if term.colors and not term.variety:
        found = [c for c in term.colors if _mentions(name, c)]
        other = [c for c in _COLORS if _mentions(name, c) and c not in term.colors]
        if not found and other:
            out.append(Violation("color:conflict", f"requested {'/'.join(term.colors)}, product is {'/'.join(other)}"))
    if term.variety:
        single = [c for c in _COLORS if _mentions(name, c)]
        if single and not _has_any(name, _VARIETY):
            out.append(Violation("variety:collapsed", f"a mix was requested, product is one kind ({single[0]})", hard=False))
    for p in term.positive:
        clash = [c for c in _QUALIFIER_CONFLICTS.get(p, ()) if _mentions(name, c)]
        if clash and not _mentions(name, p):
            out.append(Violation(f"qualifier:{p}:conflict", f"requested '{p}', product says '{clash[0]}'"))
    if term.size == "small" and _has_any(name, _SIZE_LARGE):
        out.append(Violation("size:conflict", "small requested, product is large"))
    if term.size == "large" and _has_any(name, _SIZE_SMALL):
        out.append(Violation("size:conflict", "large requested, product is small"))
    if not _head_in(term, name):
        out.append(Violation("head:missing", f"product name does not contain '{term.head}'"))
    return out


def unverified(term: ParsedTerm, product_name: str) -> list[str]:
    """Qualifiers the request states that the product name neither
    confirms nor contradicts. Soft: they lower confidence, never reject."""
    name = _norm(product_name)
    out = []
    for b in term.brand:
        aliases = _BRAND_ALIASES.get(b, (b,))
        if not any(_mentions(name, a) for a in aliases):
            out.append(f"brand:{b}")
    for p in term.positive:
        if not _mentions(name, p):
            out.append(f"qualifier:{p}")
    if term.size == "small" and not _has_any(name, _SIZE_SMALL):
        out.append("size:small")
    if term.size == "large" and not _has_any(name, _SIZE_LARGE):
        out.append("size:large")
    if term.variety and not _has_any(name, _VARIETY):
        out.append("variety")
    if term.weight and _norm(term.weight) not in name:
        out.append(f"weight:{term.weight}")
    for c in term.colors:
        if term.variety or not _mentions(name, c):
            if not term.variety:
                out.append(f"color:{c}")
    return out


def checked(term: ParsedTerm, product_name: str) -> list[str]:
    unv = set(unverified(term, product_name))
    return [q for q in term.qualifiers if q not in unv and not q.startswith("not:") and not q.startswith("free-of:")] \
        + [q for q in term.qualifiers if q.startswith("not:") or q.startswith("free-of:")]


def _mentions(name: str, word: str) -> bool:
    return _has_any(name, (word,)) is not None


def _head_in(term: ParsedTerm, name: str) -> bool:
    if not term.head:
        return True
    toks = _tokens(name)
    stems = {_stem(t) for t in toks}
    h, hs = term.head, term.head_stem
    if h in toks or hs in stems or h in stems:
        return True
    # two-word heads ("תפוח אדמה", "בצל ירוק") and the singular of a plural request
    return h in name or (len(hs) >= 3 and any(s.startswith(hs) or hs.startswith(s) for s in stems if len(s) >= 3))


def head_matches(term: ParsedTerm, product_name: str) -> bool:
    return _head_in(term, _norm(product_name))
