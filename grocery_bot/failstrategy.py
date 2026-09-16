"""What to do differently about an item that keeps failing.

A counter is not a strategy. `cart_failures` has recorded every miss
since 2026-09-10 and nothing ever read it back to change an approach, so
the same search ran again the next cycle and failed the same way.

The remedies here are not heuristics invented in advance — each one comes
from looking at the eight real failures on record (2026-09-16) and asking
what evidence would have settled them. All eight turned out to be
answerable from data this project already holds.

**Two of them show the bot's own diagnosis was wrong**, which is the
reason this module leads with disproof rather than with retries:

- `טבעפרוסט תרד 800 גרם` was recorded at Tiv Taam with
  "no add control on the row — probably out of stock" on 09-10. Tiv
  Taam's own price feed carries `טבעפרוסט תרד` at ₪11.90, and the
  household **had it delivered on 09-12**. It was in stock throughout.
  The search term carried a size the feed's name does not.
- `פלפל צהוב` failed with "the click did not change the cart" while the
  feed lists it at ₪12.90. Nothing to do with availability.

So "probably out of stock" is a guess the adapter makes from a missing
button, and it is checkable. Treating it as fact is what kept these items
failing: an out-of-stock item is someone else's problem, while an
over-specified search term is ours.

The four strategies, in the order they are tried:

1. **`retry` — the chain carries this exact product.** Its own feed lists
   the name. The failure is ours, and any "out of stock" note is
   disproven. (`ציפס אמריקאי 7ממ בארטס גורמה 1 ק"ג` is listed at ₪17.90.)
2. **`shorten` — the term is over-specified.** A shorter prefix matches
   the feed where the full string does not, almost always because the
   term carries a pack size the catalogue keeps in a separate field.
3. **`switch_chain` — another chain has it and this one does not.**
   (`טונה בהירה בשמן 3*80 גרם`: nothing in Shufersal's feed, 145 rows in
   Tiv Taam's.)
4. **`unavailable` — nobody lists it.** The only case where giving up is
   the right answer, and the only one where "out of stock" survives.

Nothing here edits a list or a cart. It produces a recommendation with
the evidence attached, because every one of these changes what the
household ends up buying.
"""
from __future__ import annotations

import logging
from contextlib import closing
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# A word shorter than this carries no search signal ("1", "גר", "ק").
MIN_WORD = 3

# How much of the term to keep when shortening. Two content words is
# what distinguishes "טבעפרוסט תרד" (a product) from "טבעפרוסט" (a brand
# with 190 rows) — one word under-specifies as badly as five over-specify.
SHORTEN_WORDS = 2

# Enough rows elsewhere to call it stocked rather than a stray match.
OTHER_CHAIN_MIN_ROWS = 5


@dataclass
class Strategy:
    """One recommendation, with what it rests on."""

    item_name: str
    store: str
    action: str  # retry | shorten | switch_chain | unavailable
    reason: str
    suggested_term: str = ""
    suggested_store: str = ""
    evidence: list = field(default_factory=list)
    # Whether the recorded failure detail blamed stock. Set by `review`
    # from the stored note, never inferred from the strategy.
    blamed_on_stock: bool = False

    def describe(self) -> str:
        from .chains import display_name

        bits = {
            "retry": f"הרשת מוכרת את זה — לנסות שוב ({self.reason})",
            "shorten": f"לחפש `{self.suggested_term}` במקום השם המלא",
            "switch_chain": (
                f"לקנות ב{display_name(self.suggested_store)} — כאן זה לא קיים"
            ),
            "unavailable": "לא קיים באף רשת שאנחנו קוראים",
        }
        return bits.get(self.action, self.action)


def _content_words(name: str) -> list:
    return [w for w in str(name or "").split() if len(w) >= MIN_WORD]


def _feed_matches(conn, store: str, words: list, limit: int = 3) -> list:
    if not words:
        return []
    clause = " AND ".join(["name LIKE ?"] * len(words))
    rows = conn.execute(
        f"SELECT name, price FROM store_prices WHERE store = ? AND {clause} "
        f"LIMIT {int(limit)}",
        [store] + [f"%{w}%" for w in words],
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _other_chains(conn, store: str, words: list) -> list:
    if not words:
        return []
    clause = " AND ".join(["name LIKE ?"] * len(words))
    rows = conn.execute(
        f"SELECT store, COUNT(*) FROM store_prices WHERE store != ? AND {clause} "
        "GROUP BY store ORDER BY COUNT(*) DESC LIMIT 3",
        [store] + [f"%{w}%" for w in words],
    ).fetchall()
    return [(r[0], r[1]) for r in rows if r[1] >= OTHER_CHAIN_MIN_ROWS]


def decide(storage, item_name: str, store: str) -> Strategy:
    """What to do differently about this item at this chain."""
    words = _content_words(item_name)
    short = words[:SHORTEN_WORDS]

    with closing(storage._connect()) as conn:  # noqa: SLF001 - storage-internal
        full = _feed_matches(conn, store, words)
        partial = _feed_matches(conn, store, short) if short != words else full
        elsewhere = _other_chains(conn, store, short)

    if full:
        return Strategy(
            item_name, store, "retry",
            reason=f"מופיע בפיד של הרשת ב-₪{full[0][1]}",
            suggested_term=item_name,
            evidence=[f"{name} ₪{price}" for name, price in full],
        )
    if partial:
        term = " ".join(short)
        return Strategy(
            item_name, store, "shorten",
            reason="השם המלא לא נמצא, אבל מונח קצר יותר כן",
            suggested_term=term,
            evidence=[f"{name} ₪{price}" for name, price in partial],
        )
    if elsewhere:
        best = elsewhere[0]
        return Strategy(
            item_name, store, "switch_chain",
            reason=f"{best[1]} תוצאות ב{best[0]}, אפס כאן",
            suggested_store=best[0],
            evidence=[f"{name}: {count} שורות" for name, count in elsewhere],
        )
    return Strategy(
        item_name, store, "unavailable",
        reason="אין תוצאות באף פיד",
    )


def review(storage, days: int = 30, min_runs: int = 1) -> list:
    """A strategy for every item that has failed repeatedly."""
    try:
        failures = storage.repeat_failures(days=days, min_runs=min_runs)
    except Exception:  # noqa: BLE001
        logger.exception("Could not read repeat failures")
        return []
    out = []
    for row in failures:
        try:
            strategy = decide(storage, row["item_name"], row["store"])
        except Exception:  # noqa: BLE001
            logger.exception("Could not plan for %s", row.get("item_name"))
            continue
        detail = row.get("detail") or ""
        strategy.evidence.append(f"נכשל {row['runs']}× · {detail or 'ללא פירוט'}")
        # Kept so the closing line can count only the items that really
        # were blamed on stock. Saying "7 were marked out of stock" when
        # 5 were is the same class of error this module exists to catch.
        strategy.blamed_on_stock = "אזל" in detail or "out of stock" in detail
        out.append(strategy)
    return out


def format_review(strategies: list) -> str:
    """The review as a message. Silence when there is nothing to change."""
    if not strategies:
        return ""
    from .chains import display_name

    order = {"switch_chain": 0, "shorten": 1, "retry": 2, "unavailable": 3}
    lines = ["*פריטים שנכשלו — מה לשנות*", ""]
    for s in sorted(strategies, key=lambda s: order.get(s.action, 9)):
        lines.append(f"• *{s.item_name}* ({display_name(s.store)})")
        lines.append(f"   {s.describe()}")
    # The disproof is the point, so it is said out loud rather than left
    # for someone to notice in a log.
    wrong = [
        s for s in strategies
        if s.blamed_on_stock and s.action in ("retry", "shorten")
    ]
    if wrong:
        lines += ["", f"_{len(wrong)} מהם סומנו כ\"כנראה אזל\" — והרשת מוכרת אותם._"]
    return "\n".join(lines)
