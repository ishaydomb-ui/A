"""Decide when a search result is clear enough not to ask about.

A Shufersal search for an everyday word returns ~20 tiles, so treating
"more than one result" as ambiguous turns a single cycle into a dozen
questions — the opposite of what this project is for. Worse, the tiles
often repeat a name: searching קוטג' returns three products all called
"קוטג' 5% שומן" (Tnuva, Strauss, Tara), which as buttons are three
identical-looking choices.

Most of those questions have an obvious answer already sitting in the
data. Against the 11 questions from a real cycle, this resolves 10:
eight had exactly one candidate the household has bought before, and
three matched the requested name exactly.

The rules are deliberately narrow — each one is a case where asking
would be asking something already known:

1. **Bought before.** Exactly one candidate appears in the purchase
   history. That is the household's own past decision; re-asking it is
   the interrogation this project set out to remove.
2. **Exact name.** A candidate's name is exactly the term requested.
3. Anything else is a real question, and gets asked properly — with the
   brand, size and price that make the options distinguishable.

What is *not* done here on purpose: guessing by price. Picking the
cheapest looks helpful and is occasionally right, but it silently
substitutes products (a 100g tub for the usual 250g one), and a wrong
silent substitution costs far more trust than one extra question.
"""
from __future__ import annotations

from dataclasses import dataclass

from .mdtext import escape as md


@dataclass
class Resolution:
    """Either a confident pick, or the reason it has to be asked."""

    card: dict | None
    reason: str  # "history" | "exact_name" | "ask"

    @property
    def resolved(self) -> bool:
        return self.card is not None


def _normalise(text: str) -> str:
    """Fold the punctuation Hebrew product names vary on.

    The geresh alone has three common spellings (קוטג׳ / קוטג' / קוטג),
    and they are not interchangeable to a string comparison.
    """
    return (
        (text or "")
        .replace("׳", "")
        .replace("'", "")
        .replace("״", "")
        .replace('"', "")
        .strip()
    )


def resolve(
    term: str,
    cards: list[dict],
    known_product_names: set[str],
    known_product_codes: set[str] | None = None,
) -> Resolution:
    """Pick a card when the answer is already known, else say to ask.

    `known_product_names` / `known_product_codes` are the products the
    household has actually bought. Codes are checked first and settle
    cases names cannot: a search for קוטג' returns three tiles all named
    "קוטג' 5% שומן" (Tnuva, Strauss, Tara), so the name proves nothing
    while the code identifies exactly the one bought for years.
    """
    if not cards:
        return Resolution(None, "ask")
    if len(cards) == 1:
        return Resolution(cards[0], "exact_name")

    codes = known_product_codes or set()
    by_code = [c for c in cards if c.get("code") and c["code"] in codes]
    if len(by_code) == 1:
        return Resolution(by_code[0], "history")

    wanted = _normalise(term)

    exact = [c for c in cards if _normalise(c.get("name", "")) == wanted]
    if len(exact) == 1:
        return Resolution(exact[0], "exact_name")

    known = {_normalise(n) for n in known_product_names}
    seen_before = [c for c in cards if _normalise(c.get("name", "")) in known]
    # Only when it is unambiguous: two products bought before is exactly
    # the case where the household's own history cannot settle it.
    if len(seen_before) == 1:
        return Resolution(seen_before[0], "history")

    return Resolution(None, "ask")


def describe_card(card: dict) -> str:
    """One line identifying a product well enough to choose between them.

    Name alone is not enough — it is routinely duplicated across brands.
    """
    bits = [md(card.get("name", "").strip())]
    detail = " ".join(part for part in (card.get("size", ""), card.get("brand", "")) if part)
    if detail:
        bits.append(md(detail))
    price = card.get("price", "")
    if price:
        try:
            bits.append(f"₪{float(price):.2f}")
        except (TypeError, ValueError):
            pass
    # The per-unit ratio settles "which is actually cheaper" between two
    # pack sizes — the arithmetic the user was doing in their head.
    ratio, label = card.get("unitPrice"), (card.get("unitLabel") or "").strip()
    if ratio and label:
        try:
            bits.append(f"{float(ratio):.2f}₪/{label}")
        except (TypeError, ValueError):
            pass
    return " · ".join(bits)
