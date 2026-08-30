"""Make store-supplied text safe to put inside a Markdown message.

Israeli product names routinely use `*` as a multiplication sign for
multipacks — "בירה קרומבאכר 6*330 מ"ל", "גבינת בייבי בל 5*20 גרם" — and
349 of this branch's 5,807 products contain one. Telegram reads that
asterisk as the start of bold, finds no closing pair, and rejects the
*entire* message:

    BadRequest: Can't parse entities: can't find end of the entity
    starting at byte offset 929

Which is how a correctly-composed digest turned into no message at all.
The failure is silent from the user's side and total rather than
partial, so every place a product name is interpolated into Markdown has
to escape it. Underscores, brackets and backticks carry the same risk;
they are rarer in product names but cost nothing to handle.
"""
from __future__ import annotations

# Telegram's legacy Markdown parser only assigns meaning to these.
_SPECIAL = ("_", "*", "`", "[", "]")


def escape(text: str) -> str:
    """Escape Markdown control characters in store-supplied text."""
    if not text:
        return ""
    out = str(text)
    for char in _SPECIAL:
        out = out.replace(char, "\\" + char)
    return out
