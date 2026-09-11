"""Deals the cycle puts *in the cart* on its own, not just reports.

Set by Ishay 2026-09-06, from how he actually shops: he fills the cart
with "really everything" and then deletes what is already at home,
because deleting a line from a cart takes seconds while spotting a deal
by hand does not happen at all. A deal that only ever arrives as a
Telegram message is a deal that needs a second action to act on — and
the measured record says those second actions do not happen: the ₪599
gift threshold was missed on both of the two most recent real orders
(by ₪38.21 and ₪77.81), *after* the checking feature was already live.

So the bar here is deliberately not "would he definitely want this".
It is "would he rather delete it than never have seen it", which for a
stockpileable product on a deep discount is nearly always yes.

Two guards keep that from turning into waste, which is the failure this
project cares about more than a missed saving:

**Pantryable only, by default.** Softener at 40% off sits in a cupboard;
half-price bananas rot. `radar` already classifies this by department,
and perishables are left to the report rather than the cart.

**Only things the household actually buys.** The candidate set is the
stock table — products with real purchase history — never the catalogue
at large. A deep discount on something never once bought is not a
saving, it is a new habit nobody asked for.

Two chains, two joins, and the difference matters. Shufersal's own feed
carries no barcode, so its deals are found by name through `radar`. Tiv
Taam publishes to the shared transparency portal, where prices *and*
promotions are both keyed on the manufacturer's EAN — so its deals join
on the barcode and cannot mis-identify a product at all. (This project
recorded for a week that Tiv Taam published no feed. It does; the
earlier check looked for a chain-hosted one under a different spelling
and read "not found" as "does not exist".)

A deal is only ever added to the cart of the chain that published it. A
Shufersal promotion put into a Tiv Taam cart would simply be bought at
Tiv Taam's undiscounted price — the saving is not transferable.

**Novel products.** Ishay 2026-09-06: he wants deep discounts on things
he has *never* bought too, not only on his own repertoire. That is a
different risk — an unknown product bought on price alone is exactly how
a cupboard fills with things nobody eats — so it carries a higher
discount bar, a smaller cap, its own label in the summary, and a
best-effort perishables filter. That filter is a keyword list, which is
weaker than the department data used for known products, and is
documented as such rather than presented as reliable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .disambiguate import _normalise
from .radar import MIN_DISCOUNT, find_stockup_deals

# Chains whose own promotions we actually hold. See the module docstring:
# this is about where the discount is real, not about which carts we can
# fill (we can fill both).
DEAL_CAPABLE_STORES = {"shufersal", "tivtaam"}

# Chains whose promotions arrive keyed by barcode, so a deal is joined to
# a shelf price with no name matching in the path at all.
BARCODE_DEAL_STORES = {"tivtaam"}

# A ceiling on how much a cycle may add by itself. The workflow tolerates
# deleting a few lines; it does not tolerate a cart that has to be read
# in full to find the things actually wanted.
MAX_DEAL_ITEMS = 8

# Novel products get a higher bar and a tighter cap than the household's
# own repertoire: buying something never once bought, on price alone, is
# the move most likely to end as waste.
NOVEL_MIN_DISCOUNT = 0.40
MAX_NOVEL_ITEMS = 4

# Three ceilings, all of them added after running this against the real
# feeds and reading what it actually proposed:
#
# - A novel item is an impulse the household did not ask for, so it has
#   to be cheap enough that a wrong guess is trivial. The first run
#   offered a ₪169 stainless serving spoon (down from ₪669) — a genuine
#   75% off, and obviously not something to drop in a grocery cart
#   unasked.
# - Above ~80% "off", the number is almost always an artefact rather
#   than a discount: the first run also offered three near-identical
#   goat's cheeses at "₪30 instead of ₪179", which is a per-kilo shelf
#   price against a per-unit promotion, not a saving.
# - Anything sold by weight has that same unit mismatch by construction,
#   so novel picks skip it entirely rather than guess the basis.
NOVEL_MAX_PRICE = 30.0
NOVEL_MAX_DISCOUNT = 0.80
_WEIGHED_WORDS = ("משקל", "במשקל", "לק\"ג", 'לק"ג')


def _sold_by_weight(name: str) -> bool:
    return any(word in (name or "") for word in _WEIGHED_WORDS)


# A promotion that only pays out on a second unit, or on a different
# product entirely. Measured on the live Tiv Taam feed 2026-09-11:
# 3,650 of 13,087 live promotions are worded this way, and **1,530 of
# them carry min_qty = 1**, so the quantity field cannot be used to
# detect them — the wording is the only signal there is.
#
# Why it matters, with the numbers that exposed it: every one of the 12
# picks the Tiv Taam cycle proposed that day was of this kind. Broccoli
# reads shelf ₪24.90, discounted ₪12.45, min_qty 1 — and ₪12.45 is the
# price of *the second bag*, so one bag costs the full ₪24.90. The run
# advertised ₪161.87 of savings on a ₪135.93 cart that would in fact have
# cost ₪297.80 and saved nothing.
#
# Buying two to make the promotion real is a different offer, and not one
# the household asked for, so it is Ishay's call rather than a silent
# change of behaviour. Until then these are refused: a saving that does
# not happen is worse than a deal not found, because the report is what
# he checks the cart against.
_MULTI_BUY_PATTERN = re.compile(
    r"השני|השניה|השנייה|השלישי|השלישית|הרביעי|מהשני"
    r"|קנה\s*\d|\d\s*יח['\"׳]?\s*ב|\d\s*ב\s*-?\s*\d"
)


def _needs_more_than_one(description: str, min_qty) -> bool:
    """Does this promotion require more than the single unit we add?"""
    try:
        if float(min_qty or 1) > 1:
            return True
    except (TypeError, ValueError):
        pass
    return bool(_MULTI_BUY_PATTERN.search(description or ""))


def _plausible_novel(name: str, shelf_price: float, discount: float) -> bool:
    """Is this a real, small, safe-to-guess deal — or a feed artefact?"""
    if shelf_price > NOVEL_MAX_PRICE:
        return False
    if discount > NOVEL_MAX_DISCOUNT:
        return False
    if _sold_by_weight(name):
        return False
    return True

# Best-effort perishables filter for products with no department data.
# Weaker than the department taxonomy used for known products — it reads
# a name, and names lie — so it is a guard, not a guarantee, and the
# summary labels these picks as unfamiliar for exactly that reason.
_PERISHABLE_WORDS = (
    # "טרי" and "פירות" are spelled out rather than left as stems: as a
    # stem, "טרי" reads "טריאקי" as fresh and "פיר" reads "פירורי לחם"
    # and "פירה" as fruit.
    "טרי ", "טריה", "טרייה", "טריים", "טריות",
    "חלב", "גבינ", "יוגורט", "קוטג", "שמנת", "חמאה", "ביצים",
    "בשר", "עוף", "הודו", "דג ", "דגים", "סלמון", "טונה טרי", "סלט", "לחם",
    "פיתה", "לחמני", "עוגה", "בצק", "ירק", "פירות", "עגבני", "מלפפון", "חסה",
    "בננ", "תפוח", "אבוקדו", "לימון", "גזר", "בצל", "שום טרי",
)


# Matched at the start of a word, optionally behind one inseparable
# prefix letter — not anywhere in the string. A plain substring test
# reads "אטריות" and "פטריות" as "טרי", "טריאקי" as fresh, and
# "כפפות ניטריל" as food: 5,591 of Tiv Taam's 24,741 product names were
# flagged perishable, and the noodles, the teriyaki and the gloves were
# all among them. That was harmless while the guard only ran on novel
# picks; once it runs on the household's own repertoire (2026-09-11) it
# silently withholds real deals on things they buy every week.
# A trailing space in the list means "this word and nothing longer" —
# "דג " must not also catch "דגני בוקר".
_PERISHABLE_PATTERN = re.compile(
    r"(?:^|[^֐-׿])[בהולמשכ]?(?:" + "|".join(
        re.escape(w.strip()) + (r"(?![֐-׿])" if w != w.rstrip() else "")
        for w in _PERISHABLE_WORDS
    ) + r")"
)


def _looks_perishable(name: str) -> bool:
    return bool(_PERISHABLE_PATTERN.search(name or ""))


@dataclass(frozen=True)
class DealPick:
    """One deal worth adding to the cart without being asked."""

    term: str
    catalog_name: str
    shelf_price: float
    deal_price: float
    discount: float
    description: str
    quantity: int = 1
    # False when the household has never bought this. Kept separate in
    # the summary: "your usual thing is cheap this week" and "here is
    # something new that is very cheap" are different offers, and only
    # the second one needs justifying.
    familiar: bool = True

    @property
    def label(self) -> str:
        """Why this is in the cart, short enough to scan in a list."""
        saved = self.shelf_price - self.deal_price
        base = (
            f"-{round(self.discount * 100)}% · {self.deal_price:.2f}₪ "
            f"במקום {self.shelf_price:.2f}₪ (חיסכון {saved:.2f}₪)"
        )
        note = _condition_note(self.description)
        return f"{base} · {note}" if note else base


# Conditions the feeds state in words and nowhere else. The price is real
# and the saving arithmetic is right — but only if the condition holds,
# and only the household can confirm that. Naming it on the line is the
# whole intervention: the alternative is a report that quietly promises a
# discount the till may not give.
_CLUB_WORDS = ("מועדון", "חבר מועדון", "לחברי")
_MIN_BASKET = re.compile(r"מות\s*-?\s*(\d{2,4})")


def _condition_note(description: str) -> str:
    text = description or ""
    notes = []
    if any(word in text for word in _CLUB_WORDS):
        # Assumed to apply: the household is in TivCoins. Never verified
        # against the account, which is why it is said out loud.
        notes.append("מחיר מועדון")
    basket = _MIN_BASKET.search(text)
    if basket:
        notes.append(f"מותנה בקנייה מעל {basket.group(1)}₪")
    return " · ".join(notes)


def _barcode_picks(
    storage,
    store: str,
    skip: set[str],
    limit: int,
    min_discount: float,
    familiar_only: bool,
    pantryable_only: bool = True,
) -> list[DealPick]:
    """Deals at a chain that publishes promotions keyed by barcode.

    No name matching anywhere: the promotion and the shelf price are the
    same barcode, so either they join or they don't.

    **The perishable guard applies to familiar picks too**, which it did
    not until 2026-09-11. Having bought something before says the
    household eats it, not that a surprise second one will be eaten
    before it spoils — and an unasked-for perishable is precisely the
    kind of item that gets thrown away. The bug was structural rather
    than incidental: `_looks_perishable` sat behind `if not familiar`,
    so this chain and Shufersal disagreed about the same rule. Shufersal
    gates its familiar picks on `deal.pantryable` from the department
    taxonomy; this feed carries no department, so the name check is the
    closest equivalent available. Live example that got through: "בצק
    פריך מלוח" at 41% off.
    """
    promotions = storage.live_store_promotions(store)
    if not promotions:
        return []
    prices = storage.latest_store_prices(store)
    bought = storage.bought_barcodes(store)

    picks: list[DealPick] = []
    for barcode, promo in promotions.items():
        shelf = prices.get(barcode)
        if not shelf or not shelf.get("price"):
            continue
        familiar = barcode in bought
        if familiar_only and not familiar:
            continue
        if not familiar_only and familiar:
            continue  # handled by the familiar pass
        if _needs_more_than_one(promo.get("description"), promo.get("min_qty")):
            continue
        deal_price = float(promo["discounted_price"])
        shelf_price = float(shelf["price"])
        # A "deal" dearer than the shelf is a multi-buy total or a feed
        # artefact, not a saving. The feed carries plenty of both.
        if deal_price <= 0 or deal_price >= shelf_price:
            continue
        discount = 1 - deal_price / shelf_price
        if discount < min_discount:
            continue
        name = shelf.get("name") or ""
        if not name:
            continue
        if (pantryable_only or not familiar) and _looks_perishable(name):
            continue
        if not familiar and not _plausible_novel(name, shelf_price, discount):
            continue
        folded = _normalise(name)
        if any(folded == s or folded in s or s in folded for s in skip):
            continue
        picks.append(
            DealPick(
                term=name,
                catalog_name=name,
                shelf_price=shelf_price,
                deal_price=deal_price,
                discount=discount,
                description=promo.get("description", ""),
                familiar=familiar,
            )
        )
    picks.sort(key=lambda p: -p.discount)
    return picks[:limit]


def _novel_shufersal_picks(
    storage, skip: set[str], limit: int, min_discount: float
) -> list[DealPick]:
    """Deep discounts at Shufersal on things never bought before.

    Shufersal's feed has no barcode and no department, so this is the
    weakest of the three paths: catalogue name against catalogue name,
    with only a keyword guard against buying something that will rot.
    """
    known = {_normalise(row["product_name"]) for row in storage.list_stock_items("shufersal")}
    picks: list[DealPick] = []
    for product, promo in storage.catalog_deals():
        if not product.price or not promo or promo.discounted_price <= 0:
            continue
        if promo.discounted_price >= product.price:
            continue
        discount = 1 - promo.discounted_price / product.price
        if discount < min_discount:
            continue
        folded = _normalise(product.name)
        if folded in known or any(folded == s or folded in s or s in folded for s in skip):
            continue
        if _needs_more_than_one(promo.description, getattr(promo, "min_qty", 1)):
            continue
        if _looks_perishable(product.name):
            continue
        if getattr(product, "is_weighted", False):
            continue
        if not _plausible_novel(product.name, product.price, discount):
            continue
        picks.append(
            DealPick(
                term=product.name,
                catalog_name=product.name,
                shelf_price=product.price,
                deal_price=promo.discounted_price,
                discount=discount,
                description=promo.description,
                familiar=False,
            )
        )
    picks.sort(key=lambda p: -p.discount)
    return picks[:limit]


def picks_for(
    storage,
    store: str,
    skip_terms: list[str] | None = None,
    limit: int = MAX_DEAL_ITEMS,
    pantryable_only: bool = True,
    novel_limit: int = MAX_NOVEL_ITEMS,
    novel_min_discount: float = NOVEL_MIN_DISCOUNT,
) -> list[DealPick]:
    """Deals to add to `store`'s cart beyond the list the user asked for.

    `skip_terms` are what the cycle is already adding (standing list and
    ad-hoc requests). A deal on something already going in would arrive
    as a duplicate line, which reads like a bug in a cart the household
    is about to scan quickly.

    Returns familiar picks first, then novel ones, each already capped —
    the caller adds them in that order so that if anything gets dropped
    it is the speculative half.
    """
    if store not in DEAL_CAPABLE_STORES:
        return []

    skip = {_normalise(t) for t in (skip_terms or []) if t}

    if store in BARCODE_DEAL_STORES:
        familiar = _barcode_picks(
            storage, store, skip, limit,
            min_discount=MIN_DISCOUNT,
            familiar_only=True,
            pantryable_only=pantryable_only,
        )
        novel = (
            _barcode_picks(
                storage, store, skip | {_normalise(p.term) for p in familiar},
                novel_limit, min_discount=novel_min_discount, familiar_only=False,
                pantryable_only=pantryable_only,
            )
            if novel_limit
            else []
        )
        return familiar + novel

    familiar: list[DealPick] = []
    for deal in find_stockup_deals(storage, store):
        if pantryable_only and not deal.pantryable:
            continue
        # Shufersal words it differently — "2ב5 פתיתים ללא גלוטן350 אסם",
        # where ₪5 buys two and one still costs ₪13.90 — but it is the
        # same mistake, so the same guard runs on both chains rather than
        # once per feed. (Its leading bare number is a *price*, not a
        # quantity: "19.90 מרק בצל/פטריות" is a single-unit deal, which
        # is why the pattern needs a digit on both sides of the ב.)
        if _needs_more_than_one(deal.description, getattr(deal, "min_qty", 1)):
            continue
        # Search on the name the household's own purchase produced, not
        # the catalogue's — that is the term the cart matcher and the
        # product memory are both already keyed on.
        term = deal.bought_name
        folded = _normalise(term)
        if any(folded == s or folded in s or s in folded for s in skip):
            continue
        familiar.append(
            DealPick(
                term=term,
                catalog_name=deal.catalog_name,
                shelf_price=deal.shelf_price,
                deal_price=deal.deal_price,
                discount=deal.discount,
                description=deal.description,
            )
        )
        if len(familiar) >= limit:
            break

    novel = (
        _novel_shufersal_picks(
            storage,
            skip | {_normalise(p.term) for p in familiar},
            novel_limit,
            novel_min_discount,
        )
        if novel_limit
        else []
    )
    return familiar + novel


@dataclass(frozen=True)
class SecondUnitOffer:
    """A promotion worth knowing about that the bot will not act on.

    Not `multibuy.MultiBuyOffer`, which is a different question asked at a
    different moment: that one works out the real arithmetic for products
    **already in the cart** at Shufersal, where `min_qty` is populated and
    `discounted_price / min_qty` gives a true per-unit price. This one is
    about products *not* in the cart, at a chain where `min_qty` lies, so
    it deliberately computes nothing.
    """

    name: str
    shelf_price: float
    description: str
    familiar: bool


# How many of these are worth naming. Higher than the cart cap because
# nothing here is being bought — the cost of an extra line is a line.
MAX_MULTI_BUY_NOTES = 6


def multi_buy_offers(
    storage, store: str, limit: int = MAX_MULTI_BUY_NOTES
) -> list[SecondUnitOffer]:
    """Deals that need a second unit, on things the household buys.

    Reported, never added. Two reasons it stops at reporting:

    Buying two of something to earn a discount is a different purchase
    from the one that was asked for, and the whole design keeps that
    decision with the person.

    And the arithmetic genuinely cannot be done from the feeds. The two
    chains mean opposite things by the same field: at Tiv Taam "מבצע
    השני ב 50%" with `discounted_price` 12.45 against a ₪24.90 shelf
    means the *second* bag costs ₪12.45, so two cost ₪37.35; at
    Shufersal "2ב5" with `discounted_price` 5.00 against a ₪13.90 shelf
    means *both* cost ₪5.00 together. Printing one saving figure for
    both would be wrong half the time, so the promotion's own wording is
    passed through verbatim and the household reads the condition.

    Limited to things already bought before: a multi-buy on a stranger is
    two units of a guess.
    """
    if store in BARCODE_DEAL_STORES:
        promotions = storage.live_store_promotions(store)
        if not promotions:
            return []
        prices = storage.latest_store_prices(store)
        bought = storage.bought_barcodes(store)
        offers = []
        for barcode, promo in promotions.items():
            if barcode not in bought:
                continue
            shelf = prices.get(barcode)
            name = (shelf or {}).get("name") or ""
            if not shelf or not shelf.get("price") or not name:
                continue
            if not _needs_more_than_one(promo.get("description"), promo.get("min_qty")):
                continue
            if _looks_perishable(name):
                continue
            offers.append(SecondUnitOffer(
                name=name, shelf_price=float(shelf["price"]),
                description=promo.get("description", ""), familiar=True,
            ))
        offers.sort(key=lambda o: -o.shelf_price)
        return offers[:limit]

    known = {_normalise(row["product_name"]) for row in storage.list_stock_items(store)}
    offers = []
    for product, promo in storage.catalog_deals():
        if not product.price or not promo:
            continue
        if not _needs_more_than_one(promo.description, getattr(promo, "min_qty", 1)):
            continue
        folded = _normalise(product.name)
        if not any(folded == k or folded in k or k in folded for k in known):
            continue
        if _looks_perishable(product.name):
            continue
        offers.append(SecondUnitOffer(
            name=product.name, shelf_price=float(product.price),
            description=promo.description or "", familiar=True,
        ))
    offers.sort(key=lambda o: -o.shelf_price)
    return offers[:limit]


def format_multi_buy_offers(offers: list[SecondUnitOffer]) -> str:
    """The "worth taking two" block. Deliberately not a saving figure.

    HTML, not Markdown, and every name escaped: this goes out with the
    cycle summary, and 349 products on this branch carry a `*` as a
    multiplication sign ("400*3ג") — which is what silently killed a
    whole order's summary on 2026-09-07.
    """
    if not offers:
        return ""
    from .htmltext import bold as _b, escape as _esc

    lines = [_b("🔁 מבצעים שדורשים יותר מיחידה אחת") + " — לא נוספו:"]
    for offer in offers:
        lines.append(
            f"   • {_esc(offer.name)} — מדף {offer.shelf_price:.2f}₪"
            f"\n      <i>{_esc(offer.description)}</i>"
        )
    lines.append("   <i>הבוט לא מוסיף שתיים ביוזמתו. אם שווה לכם — הוסיפו באתר.</i>")
    return "\n".join(lines)


def format_picks(picks: list[DealPick]) -> str:
    """The deal block for the end-of-cycle summary.

    Kept separate from the added/not-found buckets on purpose: these are
    the lines the household did not ask for, so they are the lines most
    likely to be deleted, and they need to be findable in one glance
    rather than mixed into a list of forty.
    """
    if not picks:
        return ""
    familiar = [p for p in picks if p.familiar]
    novel = [p for p in picks if not p.familiar]
    lines: list[str] = []
    if familiar:
        lines.append("🏷️ *נוספו בגלל מבצע חריג* — מחקו מה שלא צריך:")
        for pick in familiar:
            lines.append(f"• {pick.catalog_name}\n   _{pick.label}_")
    if novel:
        if lines:
            lines.append("")
        # Named as unfamiliar on purpose. These are the ones bought on
        # price alone, so they carry the higher risk of ending up as
        # waste, and the household should be able to see which is which
        # without reading the prices.
        lines.append("✨ *לא קונים בדרך כלל, אבל בהנחה עמוקה:*")
        for pick in novel:
            lines.append(f"• {pick.catalog_name}\n   _{pick.label}_")
    total = sum(p.shelf_price - p.deal_price for p in picks)
    lines.append("")
    lines.append(f"_סה\"כ חיסכון אם נשארים: {total:.2f}₪_")
    return "\n".join(lines)
