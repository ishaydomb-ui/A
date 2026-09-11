"""The household's real state, gathered for the planner.

The experiment's premise is that a message is understandable because of
*this*, not because of a taxonomy: "בעצם שניים" is unambiguous when you
know what was just added, "סיימתי" is unambiguous when you know which
cart has something in it, and "תוריד את זה" is unambiguous when you know
what went in last.

Everything here is read, never inferred. A cart reading that fails is
absent rather than guessed, because a plan built on an imagined cart is
worse than a plan built on no cart at all — the model would "know" the
milk is there and decline to add it.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MAX_PENDING = 25
MAX_CART_LINES = 12


def build(storage, factories: dict | None = None, read_carts: bool = False) -> dict:
    """One dict of facts. Cart reads are opt-in: they cost a page load."""
    from . import convo, standingcart

    context: dict = {}

    prior = convo.recall(storage)
    if prior.get("subject"):
        context["last_subject"] = prior["subject"]
        if prior.get("store"):
            context["last_store"] = prior["store"]

    try:
        context["pending"] = [r.text for r in storage.list_pending_adhoc()][:MAX_PENDING]
    except Exception:  # noqa: BLE001
        logger.exception("Could not read pending requests for the plan context")

    try:
        open_questions = [
            row for row in storage.list_pending_ambiguities()
            if storage.preferred_for(row["store"], row["original_term"]) is None
        ]
        if open_questions:
            context["open_questions"] = len(open_questions)
    except Exception:  # noqa: BLE001
        logger.exception("Could not count open questions for the plan context")

    try:
        last = standingcart.last_shop(storage)
        if last:
            context["last_shop"] = last
    except Exception:  # noqa: BLE001
        logger.exception("Could not read the last shop date")

    if factories:
        context["stores"] = sorted(factories)
    if factories and read_carts:
        context["carts"] = _read_carts(factories)
    return context


def _read_carts(factories: dict) -> dict:
    carts: dict = {}
    for store, factory in factories.items():
        try:
            with factory() as adapter:
                reader = getattr(adapter, "cart_summary", None)
                if reader is None:
                    continue
                summary = reader() or {}
            if not summary.get("ok"):
                continue
            items = summary.get("items") or []
            carts[store] = {
                "count": len(items),
                "items": [(i.get("name") or "").strip() for i in items[:MAX_CART_LINES]],
                "total": summary.get("total"),
            }
        except Exception:  # noqa: BLE001
            # Absent, not guessed: a plan built on an imagined cart would
            # decline to add the milk because it "knows" it is there.
            logger.exception("Could not read the %s cart for the plan context", store)
    return carts
