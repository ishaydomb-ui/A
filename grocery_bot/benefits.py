"""What a shekel at Tiv Taam actually costs, once the benefits are counted.

A sticker price is not what the household pays. Two separate schemes sit
on top of it, and they were tangled together until the payment records
were read line by line:

**TivCoins** — the TivClub loyalty scheme. 3 coins per ₪100 spent, each
coin worth ₪1, redeemable against a later basket or at partner
restaurants. Published rate is 3%; measured against this household's own
orders it came out at 3.14% over six months, so the published figure is
honest. Coins appear on an order as payment method 11.

**The loadable benefits card** — up to ₪700 a month loaded at 7% off, so
₪651 buys ₪700 of groceries. The discount is earned *when the card is
loaded*, not when it is spent, which matters: it is a fixed monthly
allowance, not a per-basket rate. It appears as payment method 30.

The crucial asymmetry
---------------------
Both schemes exist at Tiv Taam and neither exists at Shufersal — the
large chains do not take the loadable card. So unlike a promotion, these
do not cancel out when comparing chains: they are a permanent discount on
one side of the comparison, and a basket comparison that ignores them
understates Tiv Taam by several per cent.

The allowance is the constraint that matters. At roughly ₪1,500 a month
of Tiv Taam spend, ₪700 of card covers under half of it, so the 7% is
worth about 3% across the whole basket — real, but smaller than the
price gap between the chains, and therefore not the thing to optimise
first.
"""
from __future__ import annotations

from dataclasses import dataclass

# TivClub's published rate: 3 coins per ₪100, each coin worth ₪1.
# Confirmed against this household's own history at 3.14% over six
# months — close enough that the published number is the one to use, and
# the small excess is likely occasional bonus-coin promotions.
TIVCOIN_RATE = 0.03
TIVCOIN_VALUE = 1.0

# Coins cannot be spent one at a time; the app releases them for
# redemption at this threshold. This is why a basket closing without any
# coins applied is usually *not* a missed saving — the balance simply had
# not reached the bar yet.
TIVCOIN_REDEMPTION_THRESHOLD = 9.0

# The loadable card: monthly ceiling and the discount earned on loading.
CARD_MONTHLY_CEILING = 700.0
CARD_DISCOUNT_RATE = 0.07

# Payment method ids as they appear in Self-Point order payloads.
PAYMENT_METHOD_TIVCOINS = 11
PAYMENT_METHOD_BENEFIT_CARD = 30


@dataclass(frozen=True)
class BenefitPosition:
    """How much of a month's benefit capacity has actually been used."""

    month: str                 # YYYY-MM
    spend: float               # billed at Tiv Taam this month
    card_used: float           # paid with the loadable card
    coins_redeemed: float      # paid with TivCoins

    @property
    def card_remaining(self) -> float:
        """Allowance left this month. Never negative."""
        return round(max(0.0, CARD_MONTHLY_CEILING - self.card_used), 2)

    @property
    def card_saved(self) -> float:
        """Shekels saved by loading what was actually used."""
        return round(self.card_used * CARD_DISCOUNT_RATE, 2)

    @property
    def card_forgone(self) -> float:
        """Shekels left on the table by not loading the full allowance.

        Only counts allowance the household could plausibly have spent —
        there is no saving in loading a card for groceries it did not buy.
        """
        usable = min(self.card_remaining, max(0.0, self.spend - self.card_used))
        return round(usable * CARD_DISCOUNT_RATE, 2)

    @property
    def coins_earned(self) -> float:
        return round(self.spend * TIVCOIN_RATE, 2)


def coins_earned_on(amount: float) -> float:
    """Coins a basket of this size earns."""
    return round(amount * TIVCOIN_RATE, 2)


def effective_cost(amount: float, card_available: float = 0.0) -> float:
    """What a Tiv Taam basket really costs, after both benefits.

    The card discount applies only to the portion the remaining allowance
    can cover; coins accrue on the whole basket but are a *future* saving,
    so they are counted at face value rather than discounted — the
    household reliably spends them, which the six-month record confirms.
    """
    if amount <= 0:
        return 0.0
    covered = min(max(card_available, 0.0), amount)
    card_saving = covered * CARD_DISCOUNT_RATE
    return round(amount - card_saving - coins_earned_on(amount), 2)


def effective_discount_rate(amount: float, card_available: float = 0.0) -> float:
    """The combined benefit rate on a basket of this size, as a fraction."""
    if amount <= 0:
        return 0.0
    return round(1 - effective_cost(amount, card_available) / amount, 4)


def payments_from_order_detail(detail: dict) -> list[tuple[int, float]]:
    """(method, amount) pairs for one order, from the *detail* payload.

    The order **summary** lists payment methods without their amounts —
    ``[{"paymentMethodId": 11}, {"paymentMethodId": 2}]`` — so totalling
    from the summary silently produces zero for every benefit and reads
    like "the card was never used". The amounts live only in the detail,
    where the primary payment sits at the top of the payment block and the
    rest are under ``secondaryPayments``.
    """
    block = _find_payment_block(detail)
    if not block:
        return []
    payments = []
    if block.get("paymentMethodId") is not None:
        payments.append(
            (int(block["paymentMethodId"]), float(block.get("amountCharged") or 0))
        )
    for secondary in block.get("secondaryPayments") or []:
        if secondary.get("paymentMethodId") is None:
            continue
        payments.append(
            (
                int(secondary["paymentMethodId"]),
                float(secondary.get("amountCharged") or 0),
            )
        )
    return payments


def _find_payment_block(node):
    """Locate the dict carrying secondaryPayments, wherever it is nested."""
    if isinstance(node, dict):
        if "secondaryPayments" in node:
            return node
        for value in node.values():
            found = _find_payment_block(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_payment_block(value)
            if found:
                return found
    return None


def positions_from_orders(orders: list[dict]) -> dict[str, BenefitPosition]:
    """Build a per-month benefit position from Tiv Taam orders.

    Each order needs ``timePlaced``, ``totalAmount`` and a ``payments``
    list whose entries carry ``amountCharged`` — see
    :func:`payments_from_order_detail` for why the summary alone is not
    enough.
    """
    buckets: dict[str, dict[str, float]] = {}
    for order in orders:
        month = (order.get("timePlaced") or "")[:7]
        if not month:
            continue
        bucket = buckets.setdefault(month, {"spend": 0.0, "card": 0.0, "coins": 0.0})
        bucket["spend"] += float(order.get("totalAmount") or 0)
        for payment in order.get("payments") or []:
            method = payment.get("paymentMethodId")
            paid = float(payment.get("amountCharged") or 0)
            if method == PAYMENT_METHOD_BENEFIT_CARD:
                bucket["card"] += paid
            elif method == PAYMENT_METHOD_TIVCOINS:
                bucket["coins"] += paid
    return {
        month: BenefitPosition(
            month=month,
            spend=round(values["spend"], 2),
            card_used=round(values["card"], 2),
            coins_redeemed=round(values["coins"], 2),
        )
        for month, values in buckets.items()
    }


def redeemable(coin_balance: float) -> bool:
    """Can coins be spent right now?"""
    return coin_balance >= TIVCOIN_REDEMPTION_THRESHOLD


def coins_needed(coin_balance: float) -> float:
    """How many more coins before the balance unlocks."""
    return round(max(0.0, TIVCOIN_REDEMPTION_THRESHOLD - coin_balance), 2)


def spend_to_unlock(coin_balance: float) -> float:
    """Shekels of further spend before coins become redeemable.

    Reported so the bot can say "one more basket" rather than nagging
    about a balance that cannot be touched yet.
    """
    return round(coins_needed(coin_balance) / TIVCOIN_RATE, 2)
