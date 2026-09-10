"""A second, thinking pass for messages the classifier could not place.

Approved by Ishay 2026-09-09 as a **hybrid**, not a replacement. The
classifier is right and fast on the common path — "תוסיף חלב" is
correct in 7-9s and a second pass would only make it slower. What it
cannot do is *check* before answering, and that is exactly where this
bot fails: an ambiguous product term. The Tiv Taam flood of 39
disambiguation questions was the bot asking because it had no way to
look.

So this runs **only** when the classifier returns `unclear`, and it
returns the same `ParsedMessage` the classifier returns. Handlers,
gates and the allowlist are untouched.

## The loop proposes; only the dispatcher acts

This is a hard requirement here, not a preference. CLAUDE.md's
non-negotiable rule is that nothing may reach a checkout or payment
step, and that boundary has to live in code rather than inside a
model's judgement.

Two structural consequences, neither of them a prompt instruction —
Miri measured on 2026-09-09 that asking a prompt to behave held on the
first run and was skipped on the second, while a barrier in the
structure held every time:

1. **A cart-touching intent can never come out of here.** `start_order`,
   `add_to_cart` and `shopped` are refused whatever the model returns.
   The messages this sees are precisely the ones nobody understood, and
   turning an unparseable message into a real cart action is the wrong
   direction to resolve doubt in. `shopped` is on that list because it
   refills both carts automatically — its blast radius is a cart, not a
   log line.
2. **An unknown intent is refused, not passed through.** The result is
   validated against `INTENTS` before it leaves.

When the loop believes the household wants one of the refused actions,
it says so in `reply` and stays `unclear` — asking is the correct
outcome, and the household can then say it in words the classifier
already handles.

## Preloading, not searching

Miri measured 94s when the loop went looking for context and 11s when
the context was handed to it. The same fix applies here and is cheaper
than the loop itself: the standing list and the pending items are read
from the database in code and passed in. **A lookup done in code is a
lookup that cannot be skipped** — which is also why it is more reliable,
not merely faster.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess

from .nlu import INTENTS, ParsedItem, ParsedMessage, _claude_cli

logger = logging.getLogger(__name__)

# Measured 2026-09-10: the second pass normally takes 12-17s, but the
# 28-phrasing sweep found "נגמר" averaging 62.7s over three runs, which
# means at least one run ran to or near the old 90s ceiling. On top of
# the classifier's ~7s that is a minute and a half of silence on a phone,
# for a message whose best possible outcome is a one-line question.
#
# The asymmetry decides it: giving up early costs a clarifying question
# and falls back to a bare `unclear`, which is what the bot did before
# this module existed. Waiting costs the household staring at a typing
# indicator. So the ceiling is set just past the normal range.
LOOP_TIMEOUT_SECONDS = 30

# Intents this pass may never produce, whatever the model says. Each one
# reaches the household's real cart: start_order and add_to_cart write to
# it directly, and `shopped` triggers a full refill of both carts.
CART_INTENTS = frozenset({"start_order", "add_to_cart", "shopped"})

# How many preloaded names to show. Enough to disambiguate a term the
# household actually uses; not so many that the prompt becomes the list.
_MAX_CONTEXT_ITEMS = 60

_PROMPT = """אתה עוזר של בוט קניות משפחתי בעברית. המסווג הרגיל לא הצליח להבין את ההודעה הבאה, ולכן אתה מקבל אותה עם הקשר.

החזר JSON בלבד, בלי טקסט נוסף:
{{"intent": "...", "items": [{{"name","amount","unit","brand"}}], "query": "...", "reply": "משפט קצר בעברית"}}

intent חייב להיות אחד מ:
add_item | remove_item | price_query | deals | show_list | recipe | meal_plan | report_waste | smalltalk | unclear

הקשר — הרשימה הקבועה של משק הבית:
{standing}

הקשר — פריטים שממתינים כרגע ברשימה:
{pending}

כללים:
- השתמש בהקשר כדי לפתור מונח עמום. אם ההודעה מזכירה משהו שדומה לפריט בהקשר, זה כנראה אותו פריט — החזר את השם המלא מההקשר בתוך items.
- אם עדיין לא ברור למה הכוונה, החזר intent=unclear ובתוך reply שאלה קצרה וממוקדת. עדיף לשאול מאשר לנחש.
- אם נראה שהמשתמש מבקש לגעת בעגלה/בסל האמיתי, או מדווח שסיים לקנות — אל תבחר intent לפעולה. החזר intent=unclear, ובתוך reply בקש ממנו לנסח את זה במפורש.
- אל תמציא מוצרים שלא נזכרו בהודעה ולא מופיעים בהקשר.

ההודעה: "{message}\""""


def _context_lines(names) -> str:
    names = [str(n).strip() for n in names if str(n or "").strip()]
    if not names:
        return "(ריק)"
    return ", ".join(names[:_MAX_CONTEXT_ITEMS])


def preload(storage) -> dict:
    """Read the context in code, so the loop cannot skip reading it.

    Never raises: context is an improvement to the answer, not a
    precondition for one. A database hiccup should degrade the loop to
    a context-free pass, not remove the answer.
    """
    standing: list = []
    pending: list = []
    if storage is None:
        return {"standing": standing, "pending": pending}
    try:
        standing = [item.name for item in storage.list_active_base_items()]
    except Exception:  # noqa: BLE001
        logger.warning("loop: standing list unavailable", exc_info=True)
    try:
        pending = [item.text for item in storage.list_pending_adhoc()]
    except Exception:  # noqa: BLE001
        logger.warning("loop: pending items unavailable", exc_info=True)
    return {"standing": standing, "pending": pending}


def _ask(prompt: str) -> str:
    result = subprocess.run(
        [_claude_cli(), "-p", prompt],
        capture_output=True, text=True, timeout=LOOP_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude exited {result.returncode}: {result.stderr[:200]}")
    return result.stdout


def _extract_json(text: str) -> dict:
    found = re.search(r"\{.*\}", text or "", re.S)
    if not found:
        raise ValueError("no JSON in model output")
    return json.loads(found.group(0))


def sanitise(payload: dict) -> ParsedMessage:
    """Turn raw model output into a result the dispatcher may act on.

    This is the barrier. It is applied to whatever comes back, so a
    model that ignores every instruction in the prompt still cannot
    produce a cart action or an intent that does not exist.
    """
    intent = str(payload.get("intent") or "").strip()
    reply = str(payload.get("reply") or "").strip()

    if intent in CART_INTENTS:
        logger.info("loop: refused cart intent %r from an unclear message", intent)
        return ParsedMessage(
            intent="unclear",
            reply=reply or "לא הבנתי בדיוק — אם הכוונה לעגלה עצמה, תוכל לנסח את זה במפורש?",
        )
    if intent not in INTENTS:
        logger.warning("loop: refused unknown intent %r", intent)
        return ParsedMessage(intent="unclear", reply=reply)

    items = []
    for raw in payload.get("items") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        amount = raw.get("amount")
        try:
            amount = float(amount) if amount is not None else None
        except (TypeError, ValueError):
            amount = None
        items.append(ParsedItem(
            name=name, amount=amount,
            unit=str(raw.get("unit") or "").strip(),
            brand=str(raw.get("brand") or "").strip(),
        ))

    return ParsedMessage(
        intent=intent, items=items,
        query=str(payload.get("query") or "").strip(),
        reply=reply,
    )


def reconsider(message: str, storage=None) -> ParsedMessage | None:
    """A second pass at a message the classifier gave up on.

    Returns None when the loop could not run or added nothing, so the
    caller keeps the classifier's own result. **The loop can only ever
    improve on `unclear`, never replace a confident answer** — it is
    not asked about one.
    """
    text = (message or "").strip()
    if not text:
        return None

    context = preload(storage)
    prompt = _PROMPT.format(
        standing=_context_lines(context["standing"]),
        pending=_context_lines(context["pending"]),
        message=text,
    )
    try:
        parsed = sanitise(_extract_json(_ask(prompt)))
    except Exception:  # noqa: BLE001
        logger.warning("loop: unavailable, keeping the classifier result", exc_info=True)
        return None

    # An `unclear` carrying a real question is still an improvement on a
    # bare `unclear`; an empty one is not, so say so with None.
    if parsed.intent == "unclear" and not parsed.reply:
        return None
    return parsed
