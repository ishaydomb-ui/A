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


# Legacy Markdown has no backslash escape. `escape` still helps in plain
# text — the reader sees a stray backslash but the message survives — but
# inside a *bold* span it is actively harmful: the escaped asterisk closes
# the bold early, the next one opens an entity that never closes, and
# Telegram rejects the whole message. That is how the cross-chain deals
# button did nothing at all: the handler ran and the send failed.
#
# For names that go inside an entity, the asterisk is replaced rather than
# escaped. In Israeli product names it is a multiplication sign —
# "2*75 מ\"ל" means two of 75ml — so × is what it actually meant, and the
# line reads better than the escaped form ever did.
_MULTIPLY = "×"


def safe_name(text: str) -> str:
    """Store text that is safe *inside* bold or italic, not just beside it."""
    if not text:
        return ""
    out = str(text).replace("*", _MULTIPLY)
    for char in ("_", "`", "[", "]"):
        out = out.replace(char, " ")
    return out
