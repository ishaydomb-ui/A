"""Deals worth interrupting someone for.

Distinct from `radar`, which looks for stock-up bargains inside the
household's usual shop. This looks across *every* chain now in the
database — including the five they do not shop at — and asks a harder
question: is this cheap enough that it should change what they do?

Two categories qualify, and they qualify for different reasons:

**Things they buy often.** A deep cut on a weekly product compounds. The
saving is small per unit and large per year.

**Expensive things that keep.** Nappies, formula, toilet paper, cleaning
supplies, baby wipes. These are the items where a one-off order from an
unfamiliar chain genuinely pays, because the saving is tens of shekels
per unit and nothing spoils while it waits. The household has a baby due
in January, which makes this category worth watching from late in the
year rather than now.

The bar is deliberately high. A weekly message listing thirty small
discounts gets muted, and then the one that mattered goes unread too.
"""
from __future__ import annotations

from dataclasses import dataclass

from .chains import display_name, is_regular
from .multibuy import _promotion_horizon

# A cut worth a message on something they already buy. Below this it is
# ordinary price movement and the weekly comparison already covers it.
MIN_DISCOUNT = 0.20

# The bar for a product the household has never bought. Much higher,
# because the only reason to mention it is that it is remarkable: the
# household said they are happy to try something new at a good enough
# price, not that they want a catalogue.
EXCEPTIONAL_DISCOUNT = 0.40
EXCEPTIONAL_MIN_SAVING = 8.0

# Past this, it is almost always a feed error rather than a price — a
# 95%-off line is a misplaced decimal, and reporting it burns trust that
# the rest of the list depends on.
IMPLAUSIBLE_DISCOUNT = 0.90

# How many of each kind go in a message. Two short lists are read; one
# long list is skimmed and then ignored.
RELEVANT_LIMIT = 5
EXCEPTIONAL_LIMIT = 5

# The long list behind the link. Everything that cleared a bar but did
# not make the ten, so the short message stays short and nothing found is
# thrown away.
EXTENDED_LIMIT = 20

# For an expensive keeper, the shekels matter more than the percentage:
# 15% off nappies is worth more than half off a tin of corn.
MIN_ABSOLUTE_SAVING = 12.0

# At most this many deals from one product family. Without it the first
# run returned six lines of nappies in eight: all true, all the same
# decision, and the two other findings pushed off the end of the message.
MAX_PER_FAMILY = 2

# Categories where a deep discount justifies buying ahead, because the
# product does not spoil and the unit price is high. Matched on the
# product name, so kept broad but specific enough not to catch food.
# Written as stems, without the final letter, because Hebrew changes it
# when a word is pluralised: "מגבון" ends in a final nun (ן) while
# "מגבונים" uses the medial form (נ), so the singular is not a substring
# of the plural and a naive match silently misses every packet of wipes.
STOCKABLE_PATTERNS = (
    "חיתול", "פמפרס", "האגיס", "טיטול",
    # A newborn's size is written several ways and none of them is a
    # number the size-5 patterns would catch. Added ahead of a January
    # birth so the deep discounts are visible from late in the year,
    # while there is still time to stock up.
    "ניובורן", "newborn", "מידה 0", "חיתול ראשון", "שלב ראשון",
    "מגבונ", "מגבון",
    "סימילאק", "מטרנה", "תמ\"ל", "תמל",
    "נייר טואלט", "מגבת נייר", "טישו",
    "אבקת כביסה", "ג'ל כביסה", "מרכך כביסה", "אקונומיקה",
    "נוזל כלים", "מטהר", "סבון",
    "שמפו", "מרכך שיער", "משחת שיניים", "דאודורנט",
    "מוצץ", "בקבוק לתינוק",
)


def is_stockable(name: str) -> bool:
    """Does this keep indefinitely and cost enough to be worth stocking?

    Case-folded because some of these names are Latin: "Huggies Newborn"
    would not match a lowercase pattern, and nappy brands write their
    sizes in English as often as in Hebrew. Hebrew has no case, so this
    costs nothing there.
    """
    text = (name or "").lower()
    return any(pattern.lower() in text for pattern in STOCKABLE_PATTERNS)


@dataclass(frozen=True)
class HotDeal:
    barcode: str
    name: str
    chain: str
    price: float
    reference_price: float
    reference_chain: str = "shufersal"
    bought_often: bool = False

    @property
    def saving(self) -> float:
        return round(self.reference_price - self.price, 2)

    @property
    def discount(self) -> float:
        if not self.reference_price:
            return 0.0
        return round(self.saving / self.reference_price, 3)

    @property
    def stockable(self) -> bool:
        return is_stockable(self.name)

    @property
    def worth_reporting(self) -> bool:
        if not self.plausible:
            return False
        # An expensive keeper clears on shekels; everything else has to
        # clear on percentage, so a cheap item cannot shout.
        if self.stockable and self.saving >= MIN_ABSOLUTE_SAVING:
            return True
        return self.discount >= MIN_DISCOUNT and self.saving >= 2.0

    @property
    def relevant(self) -> bool:
        """Is this about the household's own shopping?"""
        return self.bought_often or self.stockable

    @property
    def plausible(self) -> bool:
        return 0 < self.discount < IMPLAUSIBLE_DISCOUNT

    @property
    def exceptional(self) -> bool:
        """Remarkable enough to mention even if they have never bought it."""
        return (
            self.plausible
            and self.discount >= EXCEPTIONAL_DISCOUNT
            and self.saving >= EXCEPTIONAL_MIN_SAVING
        )

    @property
    def reason(self) -> str:
        if self.stockable:
            return "לא מתקלקל, שווה לאגור"
        if self.bought_often:
            return "אתם קונים את זה הרבה"
        return "הנחה חריגה"


def scan(storage, chains=None) -> list[HotDeal]:
    """Every worthwhile discount across every chain with data."""
    frequent = {
        row["product_name"]
        for store in ("shufersal", "tivtaam")
        for row in storage.list_stock_items(store)
        if row.get("tier") in ("A", "B")
    }

    from .chains import CHAIN_NAMES

    candidates = chains or [c for c in CHAIN_NAMES if c != "shufersal"]
    deals: list[HotDeal] = []
    for chain in candidates:
        for barcode, row in storage.latest_store_prices(chain).items():
            reference = storage.catalog_price(barcode)
            if not reference or not reference.get("price"):
                continue
            deal = HotDeal(
                barcode=barcode,
                name=reference["name"],
                chain=chain,
                price=row["price"],
                reference_price=reference["price"],
                bought_often=reference["name"] in frequent,
            )
            if deal.relevant and deal.worth_reporting:
                deals.append(deal)
            elif deal.exceptional:
                deals.append(deal)

    deals.extend(_promotion_deals(storage, frequent))
    return deals


def _promotion_deals(storage, frequent: set) -> list[HotDeal]:
    """Deals that are a promotion rather than a cheaper chain.

    Cross-chain price comparison cannot see these at all: a 2+1 on pasta
    at the household's own shop leaves the shelf price untouched, so
    without this the most ordinary kind of Israeli deal — the one the
    household actually asked about — would never appear.

    The per-unit price is what matters, so a "3 for ₪15" is compared as
    ₪5, not ₪15.
    """
    from contextlib import closing

    with closing(storage._connect()) as conn:  # noqa: SLF001 - storage-internal
        rows = conn.execute(
            "SELECT p.item_code, p.discounted_price, p.min_qty, c.name, c.price "
            "FROM catalog_promotions p JOIN catalog_products c "
            "  ON c.item_code = p.item_code "
            "WHERE p.discounted_price > 0 AND p.ends_at != '' AND p.ends_at < ? "
            "  AND p.discounted_price < p.min_qty * c.price",
            (_promotion_horizon(),),
        ).fetchall()

    deals = []
    for row in rows:
        quantity = float(row["min_qty"] or 1)
        unit_price = float(row["discounted_price"]) / (quantity or 1)
        deal = HotDeal(
            barcode=row["item_code"],
            name=row["name"],
            chain="shufersal",
            price=round(unit_price, 2),
            reference_price=float(row["price"]),
            bought_often=row["name"] in frequent,
        )
        if (deal.relevant and deal.worth_reporting) or deal.exceptional:
            deals.append(deal)
    return deals


def find(storage, chains=None) -> tuple[list[HotDeal], list[HotDeal]]:
    """Two short lists rather than one long one.

    The household was explicit about the risk here: a deal list built only
    from what they already buy would never surface strawberries, a 2+1 on
    pasta, or the deodorant one of them uses — and they *do* want those,
    because they are happy to try something new at a good enough price.

    But a single merged list solves that by becoming a catalogue nobody
    reads. So the answer is two buckets with different bars: things that
    are theirs, and things that are simply remarkable.
    """
    deals = _dedupe(scan(storage, chains))
    relevant = [d for d in deals if d.relevant][:RELEVANT_LIMIT]
    # "Never bought" has to mean never bought. Filtering only on what
    # already made the first list put yellow peppers — a tier-A product
    # for this household — under the heading "even if you have not bought
    # it", purely because five nappy deals outranked it.
    # One per family here rather than two: the whole point of this list is
    # breadth, and two sun creams at the same price is one idea taking two
    # of the five slots.
    exceptional, families = [], set()
    for deal in deals:
        if not deal.exceptional or deal.relevant:
            continue
        family = _family(deal.name)
        if family in families:
            continue
        families.add(family)
        exceptional.append(deal)
        if len(exceptional) >= EXCEPTIONAL_LIMIT:
            break
    return relevant, exceptional


def find_extended(storage, chains=None, limit: int = EXTENDED_LIMIT) -> list[HotDeal]:
    """The wider list behind the link — deliberately the *least* filtered.

    This exists to escape the bias in the short list, so it must not
    inherit its ordering. It did at first: `_dedupe` ranks by
    (relevant, stockable, saving), which floats the hand-written
    "stockable" categories to the top, and those are mostly nappies and
    cleaning products. Measured on real data, 181 deals on products this
    household has never bought were available and the list showed five of
    them; thirteen of twenty slots went to that one pattern list.

    The household asked for this precisely so they would hear about
    strawberries and a deodorant one of them uses. So here the ranking is
    by discount alone, with one slot per product family, and novel
    products are placed first — the short list already covers what is
    theirs, and repeating it here would waste the only view that can
    surprise them.
    """
    deals = _dedupe(scan(storage, chains))
    relevant, exceptional = find(storage, chains)
    shown = {d.barcode for d in relevant} | {d.barcode for d in exceptional}
    candidates = [d for d in deals if d.barcode not in shown]

    # Novel first, then by depth of discount. Saving in shekels would
    # re-introduce the same bias by another route: the expensive keepers
    # are expensive, so they would win on absolute money every time.
    candidates.sort(key=lambda d: (not d.relevant, d.discount), reverse=True)
    candidates.sort(key=lambda d: (d.relevant, -d.discount))

    picked, families = [], set()
    for deal in candidates:
        family = _family(deal.name)
        if family in families:
            continue
        families.add(family)
        picked.append(deal)
        if len(picked) >= limit:
            break
    return picked


def format_extended(deals: list[HotDeal]) -> str:
    from .mdtext import escape

    if not deals:
        return "אין כרגע מבצעים נוספים מעבר למה שכבר שלחתי."
    lines = [f"*עוד {len(deals)} מבצעים*", ""]
    for deal in deals:
        tag = "" if is_regular(deal.chain) else " \u26a1"
        lines.append(
            f"\u2022 *{escape(deal.name)}* \u2014 \u20aa{deal.price:.2f} "
            f"\u05d1{escape(display_name(deal.chain))}{tag} "
            f"_(\u20aa{deal.saving:.2f}, {deal.discount * 100:.0f}%)_"
        )
    return "\n".join(lines)


# Several patterns describe one shopping decision. Without this the cap
# was dodged by accident: "12 האגיס חיתולי שחיה" matched "חיתול" while
# "האגיס אקסטרה קר" matched "האגיס", so three nappy lines counted as two
# different families and filled the list anyway.
_FAMILY_ALIASES = {
    "חיתול": "חיתולים", "פמפרס": "חיתולים", "האגיס": "חיתולים",
    "טיטול": "חיתולים",
    "סימילאק": "תמל", "מטרנה": "תמל", "תמ\"ל": "תמל", "תמל": "תמל",
    "נייר טואלט": "נייר", "מגבת נייר": "נייר", "טישו": "נייר",
    "אבקת כביסה": "כביסה", "ג'ל כביסה": "כביסה", "מרכך כביסה": "כביסה",
    "שמפו": "טיפוח", "מרכך שיער": "טיפוח", "משחת שיניים": "טיפוח",
    "דאודורנט": "טיפוח", "סבון": "טיפוח",
    "מגבונ": "מגבונים", "מגבון": "מגבונים",
}


def _family(name: str) -> str:
    """The shopping decision a product belongs to, not its brand.

    Outside the named categories the first word is the grouping. Two
    words was too fine to be useful: "פינוקיות קרם פסק זמן" and
    "פינוקיות בטעם טורטית" counted as different families and took two of
    twenty slots in a list whose only job is variety.
    """
    text = name or ""
    for pattern in STOCKABLE_PATTERNS:
        if pattern in text:
            return _FAMILY_ALIASES.get(pattern, pattern)
    words = text.split()
    return words[0] if words else ""


def _dedupe(deals: list[HotDeal]) -> list[HotDeal]:
    """Cheapest chain per product, and no more than a couple per family."""
    best: dict[str, HotDeal] = {}
    for deal in deals:
        current = best.get(deal.barcode)
        if current is None or deal.price < current.price:
            best[deal.barcode] = deal

    ordered = sorted(
        best.values(), key=lambda d: (d.relevant, d.stockable, d.saving), reverse=True
    )
    seen: dict[str, int] = {}
    diverse = []
    for deal in ordered:
        family = _family(deal.name)
        if seen.get(family, 0) >= MAX_PER_FAMILY:
            continue
        seen[family] = seen.get(family, 0) + 1
        diverse.append(deal)
    return diverse


def format_deals(relevant: list[HotDeal], exceptional: list[HotDeal] | None = None) -> str:
    from .mdtext import escape

    exceptional = exceptional or []
    if not relevant and not exceptional:
        return ""

    def line(deal: HotDeal) -> str:
        where = display_name(deal.chain)
        tag = "" if is_regular(deal.chain) else " ⚡"
        return (
            f"• *{escape(deal.name)}* — ₪{deal.price:.2f} ב{escape(where)}{tag} "
            f"מול ₪{deal.reference_price:.2f} "
            f"_(חיסכון ₪{deal.saving:.2f}, {deal.discount * 100:.0f}%)_"
        )

    lines = []
    if relevant:
        lines += ["*מבצעים על מה שאתם קונים*", ""]
        lines += [line(d) for d in relevant]
    if exceptional:
        if lines:
            lines.append("")
        lines += ["*מבצעים חריגים — שווה מבט גם אם לא קניתם*", ""]
        lines += [line(d) for d in exceptional]
    if any(not is_regular(d.chain) for d in relevant + exceptional):
        lines += ["", "_⚡ = רשת שאתם לא קונים בה בדרך כלל_"]
    return "\n".join(lines)
