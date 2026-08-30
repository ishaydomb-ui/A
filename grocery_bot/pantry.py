"""Which recipe ingredients the household probably already has.

A recipe expander that dumps every ingredient onto the shopping list is
worse than none: an apple-pie breakdown adds flour, sugar and cinnamon
to the cart of a household that obviously owns flour, sugar and
cinnamon, and the user then deletes them by hand — the exact chore this
project exists to remove.

Two signals, both conservative:

1. **Their own purchase history.** An ingredient matching a product they
   buy regularly (tier A/B) is something the kitchen usually stocks.
2. **Universal pantry staples** — salt, oil, flour, sugar and the like —
   that most kitchens simply have, and which show up rarely in online
   history precisely *because* one bag lasts months.

"Probably have" is a label, never a silent removal: the user sees every
ingredient and decides. Guessing wrong quietly costs a missing
ingredient mid-cooking, which is the worst possible time to find out.
"""
from __future__ import annotations

from .disambiguate import _normalise

# Things almost every kitchen keeps. Deliberately short and boring —
# each entry here silently biases a recipe toward "you have this", so
# only items where that is overwhelmingly true belong.
PANTRY_STAPLES = (
    "מלח",
    "פלפל שחור",
    "סוכר",
    "קמח",
    "שמן",
    "שמן זית",
    "מים",
    "אבקת אפייה",
    "סודה לשתייה",
    "וניל",
    "קינמון",
    "פפריקה",
    "כמון",
    "אורז",
    "פסטה",
    "קטשופ",
    "מיונז",
    "חרדל",
    "רוטב סויה",
    "דבש",
    "חומץ",
)


def likely_have(storage, ingredient_name: str, store: str = "shufersal") -> bool:
    """Does the household probably already have this ingredient?"""
    wanted = _normalise(ingredient_name)
    if not wanted:
        return False

    # Whole-word matching, not substring: "שמן" as a substring happily
    # matches "טונה בשמן קנולה", declaring the household owns tuna because
    # it owns oil. Every staple token must appear as a standalone word.
    wanted_words = set(wanted.split())
    for staple in PANTRY_STAPLES:
        staple_words = _normalise(staple).split()
        if staple_words and all(word in wanted_words for word in staple_words):
            return True

    for row in storage.list_stock_items(store):
        if row.get("tier") not in ("A", "B"):
            continue
        name = _normalise(row.get("product_name", ""))
        if name and (wanted in name or name in wanted):
            return True
    return False


def split_ingredients(storage, ingredients, store: str = "shufersal"):
    """Partition ingredients into (probably_missing, probably_have)."""
    missing, have = [], []
    for ingredient in ingredients:
        (have if likely_have(storage, ingredient.name, store) else missing).append(ingredient)
    return missing, have
