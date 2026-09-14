"""When the classifier has no slot, plan instead of giving up.

This is the conclusion of the `claude/experiment-direct-planner`
comparison, run 2026-09-11 on 25 real message shapes. Neither path won:

- The classifier answers the common cases in 7-10s and is better at
  terse follow-ups — "תעשה 3", "השני במקום הראשון" — where the
  open-ended planner hesitates and asks.
- The planner is better at everything the taxonomy has no slot for.
  Two of those the classifier does not merely miss: it **files a whole
  sentence as a grocery item**. "כמו בפעם שעברה אבל לאירוח" became a
  product. So did "את זה רק הפעם".
- And the planner alone is not safe to run as the only path: it
  returned neither a step nor a question for "נגמר הקוטג", the plainest
  message in the set. Silence is worse than a wrong guess, because
  nothing surfaces.

So the planner takes over at exactly one point — where `unclear` used to
be returned — and nowhere else. The common path pays none of its latency,
a confident classification is never overturned by it, and the measured
model-call count goes *down*, because the call that used to be spent on
a second classifier is spent on something that can actually plan.

**The cart barrier is the same one, for the same reason.** This is the
guessing path. A plan reached from a message nobody could classify must
not write to the household's real cart, exactly as `loop.CART_INTENTS`
refuses those intents from the second pass. The tools are refused here
rather than removed from the catalogue, so the planner still understands
"תוסיף חלב לעגלה" well enough to say so — it just cannot do it from
this path, and says what it understood instead.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Tools that reach the real cart. Refused on this path, and the list is
# checked against the catalogue by a test so a new cart tool cannot be
# added without deciding about this.
CART_TOOLS = frozenset({
    "add_to_cart", "set_cart_quantity", "replace_in_cart",
    "remove_from_cart", "fill_cart", "report_shopped",
})

# How a plan step maps back onto the intents the bot already executes.
# Deliberately partial: a tool with no mapping is reported, never run.
TOOL_TO_INTENT = {
    "add_to_list": "add_item",
    "remove_from_list": "remove_item",
    "price_check": "price_query",
    "show_deals": "deals",
    "show_list": "show_list",
    "recipe": "recipe",
    "meal_plan": "meal_plan",
    "report_waste": "report_waste",
}

# Which argument carries the thing being talked about, per tool.
_ITEM_ARG = {
    "add_to_list": "item",
    "remove_from_list": "item",
    "price_check": "item",
    "show_deals": "item",
    "recipe": "dish",
    "report_waste": "item",
}


def reconsider(text: str, storage, factories: dict | None = None):
    """A ParsedMessage from a plan, or None to keep `unclear`.

    Never raises: an unavailable planner leaves the bot exactly where it
    was before this existed.
    """
    from .nlu import ParsedAction, ParsedItem, ParsedMessage

    try:
        from . import plancontext, planner
    except Exception:  # noqa: BLE001
        return None

    try:
        context = plancontext.build(storage, factories) if storage else {}
    except Exception:  # noqa: BLE001
        logger.exception("hybrid: could not build the plan context")
        context = {}

    try:
        plan = planner.plan_message(text, context)
    except Exception:  # noqa: BLE001
        logger.warning("hybrid: planner unavailable, keeping unclear", exc_info=True)
        return None

    actions, refused = [], []
    for step in plan.steps:
        if step.tool in CART_TOOLS:
            # Understood, deliberately not done. Reported so the household
            # can repeat it in a form the classifier will take, rather
            # than being told it was not understood.
            refused.append(step.tool)
            continue
        intent = TOOL_TO_INTENT.get(step.tool)
        if intent is None:
            continue
        name = step.args.get(_ITEM_ARG.get(step.tool, ""), "")
        items = []
        if name and step.tool not in ("price_check", "recipe", "show_deals"):
            items = [ParsedItem(
                name=name,
                amount=step.args.get("amount"),
                unit=step.args.get("unit", ""),
            )]
        actions.append(ParsedAction(intent=intent, items=items, query=name))

    if not actions:
        if plan.question or refused:
            return ParsedMessage(
                intent="unclear",
                reply=plan.question or _refusal_reply(refused),
            )
        return None

    logger.info(
        "hybrid: planned %r -> %s", text[:40], ", ".join(a.intent for a in actions)
    )
    first = actions[0]
    return ParsedMessage(
        intent=first.intent, items=first.items, query=first.query,
        reply=plan.reply or "", actions=actions,
    )


def _refusal_reply(refused: list) -> str:
    """Say what was understood, and why it was not done."""
    return (
        "הבנתי שהכוונה לעגלה עצמה, אבל לא הייתי בטוח מספיק כדי לגעת בה. "
        "אפשר לכתוב את זה ישירות — למשל 'תוסיף חלב לעגלה'?"
    )
