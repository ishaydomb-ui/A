"""A number the bot says must be a number the bot computed.

Every figure in this project's output has a source: a saving is summed
from deal lines that really went into the cart, a total is read from the
chain's own cart page, a coverage percentage is counted. One kind of text
has no source at all — the `reply` field the classifier writes, which is
free-form Hebrew from a model and goes to the household verbatim.

Nothing stops that sentence containing "חסכתי לך 40 ₪" or "זה 30% יותר
זול". It would be fluent, plausible, and invented, and it would sit in
the same chat as figures that are real — which is what makes it
expensive: once one number in the conversation is made up, none of them
can be trusted without checking, and checking is the work this bot
exists to remove.

**Stated as a rule rather than asked for as a favour.** The instruction
"don't invent numbers" is already in the prompt. This is the same
difference as `loop.sanitise` and `planner.validate`: the prompt is a
request, the code is the rule, and a measurement from the household's
other project put the gap at one-in-three versus six-in-six.

Numbers are redacted, not the whole sentence dropped: "הוספתי חלב" is
useful and "הוספתי חלב ב-7.90 ₪" is useful minus a claim, so the claim
goes and the sentence stays.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# A money figure — "7.90 ₪", "₪7.90", "7.9 שקל", "12 ש"ח — or a
# percentage. Deliberately narrow: a quantity ("2 יחידות"), a size ("400
# גרם") and a date are not claims about price and must survive, because
# they are the words a person actually said.
_MONEY = re.compile(
    r"(?:₪\s*\d+(?:[.,]\d+)?)"
    r"|(?:\d+(?:[.,]\d+)?\s*(?:₪|ש[\"״']?ח|שקלים|שקל))"
)
_PERCENT = re.compile(r"\d+(?:[.,]\d+)?\s*%|%\s*\d+(?:[.,]\d+)?")

# A percentage is only a claim when the sentence is about a saving.
# "קוטג 5% שומן" is the product's name — redacting it would mangle the
# thing the household actually said, which is worse than the risk it
# guards against. Money needs no such test: a product name does not carry
# a shekel figure, a claim about price always does.
_DISCOUNT_WORDS = (
    "הנחה", "הנחות", "חיסכון", "חסכ", "זול", "יקר", "מבצע", "הוזל",
    "פחות", "יותר",
)


def _is_about_price(text: str) -> bool:
    return any(word in (text or "") for word in _DISCOUNT_WORDS)

REDACTION = "…"


def _figures(text: str) -> list[str]:
    found = _MONEY.findall(text or "")
    if _is_about_price(text):
        found += _PERCENT.findall(text or "")
    return found


def verify(text: str, computed=()) -> str:
    """Model-authored text with any unbacked figure redacted.

    `computed` is whatever this turn actually worked out — pass the
    numbers the code produced and they are allowed through, so a reply
    that quotes a real price stays intact. Everything else is redacted
    and logged, because a model inventing prices is worth knowing about
    even once.
    """
    if not text:
        return text or ""
    allowed = {_normalise_number(str(value)) for value in (computed or ())}
    dropped = []

    def _check(match):
        figure = match.group(0)
        if _normalise_number(figure) in allowed:
            return figure
        dropped.append(figure)
        return REDACTION

    out = _MONEY.sub(_check, text)
    if _is_about_price(text):
        out = _PERCENT.sub(_check, out)
    if dropped:
        logger.warning(
            "readback: redacted %d unbacked figure(s) from model text: %s",
            len(dropped), ", ".join(dropped[:5]),
        )
    return out


def _normalise_number(value: str) -> str:
    """Just the digits, so "₪7.90", "7.90 ₪" and "7.9" compare equal."""
    digits = re.sub(r"[^\d.,]", "", str(value)).replace(",", ".")
    if not digits:
        return ""
    try:
        return f"{float(digits):.2f}"
    except ValueError:
        return digits


def has_unbacked_figure(text: str, computed=()) -> bool:
    """Whether `verify` would change anything. For tests and logging."""
    return verify(text, computed) != (text or "")
