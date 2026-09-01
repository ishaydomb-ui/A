"""Ask, once a month, whether the benefit card was loaded.

This is the largest measured money gap in the project: ₪221.85 captured
of a possible ₪539 over twelve months, with seven months loading nothing
at all while the household spent ₪1,000–2,800 in each of them. The
discount is 7% on up to ₪700, earned when the card is *loaded*, so a
month that ends unloaded is ₪49 that simply never existed.

It cannot be detected in advance. Payment method 30 only appears on an
order that has already been paid, so by the time the data proves the card
was unused, the money is gone. The only mechanism that works is to ask
before the shop — which is why this rides alongside the six-day nudge
rather than being its own channel.

Answering "yes" ends the question for that month. Asking again after a
person has said they did it is the fastest way to make them stop reading
the reminders that matter.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from .benefits import CARD_DISCOUNT_RATE, CARD_MONTHLY_CEILING

# Confirmations are per calendar month because the allowance is: an
# unused ₪700 does not roll over, so each month is its own decision.
KIND = "benefit_card_loaded"


def current_month(today: date | None = None) -> str:
    return (today or datetime.now(timezone.utc).date()).strftime("%Y-%m")


@dataclass(frozen=True)
class CardPrompt:
    month: str
    should_ask: bool
    reason: str = ""

    @property
    def text(self) -> str:
        saving = CARD_MONTHLY_CEILING * CARD_DISCOUNT_RATE
        return (
            f"💳 *הטענת את הכרטיס החודש?*\n"
            f"אפשר להטעין עד ₪{CARD_MONTHLY_CEILING:.0f} ב-7% הנחה — "
            f"כלומר ₪{saving:.0f} שנשארים אצלכם.\n"
            f"_תענו 'הטענתי' ולא אשאל שוב החודש._"
        )


def confirmed(storage, month: str | None = None, today: date | None = None) -> bool:
    """Has the household already said they loaded it this month?"""
    return storage.benefit_confirmed(KIND, month or current_month(today))


def confirm(storage, month: str | None = None, today: date | None = None) -> None:
    """Record that they did, so the question stops for this month."""
    storage.confirm_benefit(KIND, month or current_month(today))


def decide(storage, today: date | None = None) -> CardPrompt:
    month = current_month(today)
    if confirmed(storage, month):
        return CardPrompt(month, False, "already confirmed this month")
    return CardPrompt(month, True)


# Phrases that count as "yes, I loaded it". Deliberately generous: a
# person answering a yes/no question should not have to guess the wording,
# and the cost of accepting a loose "yes" is one unasked question.
_AFFIRMATIVE = (
    "הטענתי", "טענתי", "כן", "עשיתי", "ביצעתי", "הטענו", "טענו", "סידרתי",
)


def looks_confirmed(text: str) -> bool:
    """Does this reply mean the card was loaded?"""
    lowered = (text or "").strip()
    if not lowered:
        return False
    # A negation anywhere flips the meaning: "לא הטענתי" must never be
    # read as a confirmation because it contains "הטענתי".
    if "לא " in lowered or lowered.startswith("לא"):
        return False
    return any(word in lowered for word in _AFFIRMATIVE)
