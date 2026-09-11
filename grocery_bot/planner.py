"""Experimental: understand the request, don't classify it.

Set up by Ishay 2026-09-11. The question this branch exists to answer:

> האם מודל שמקבל את השיחה, ההקשר המשפחתי, מצב מחזור הקנייה וה-tools
> הרלוונטיים יכול להבין ישירות את הבקשה ולהפיק plan/action מובנה, בלי
> שהמערכת מנסה קודם לצמצם כל הודעה ל-intent קשיח.

The suspicion is that our own rules are a source of friction: every
message is squeezed into one of thirteen intents, and anything that does
not fit becomes `unclear` — which reads to the household as the bot not
understanding, when in fact the bot understood and the taxonomy did not
have a slot. A message carrying two requests, a correction that refers to
the previous turn, a scope ("just this once") — each needed its own rule
because the shape was fixed first and the language second.

So here the model is given the conversation, the household's real state
and a catalogue of **tools**, and asked for a plan. No intent field.

**What this does not change — the deterministic layer stays exactly where
it is.** The model proposes; this module disposes:

- No checkout and no payment exists as a tool, so no plan can contain one.
- The list and the cart are different tools. A plan cannot blur them,
  because there is no argument that means "either".
- Quantities, product identity, prices, sessions and idempotency are
  resolved in code, from the real feeds and the real cart. The model
  never supplies a product code or a price.
- Cart changes go through the same adapters as before.
- A genuine ambiguity still stops and asks; the plan can carry a
  question, and a question suspends the steps after it.

`validate()` is the barrier and is applied to whatever comes back, so a
model that ignores every instruction in the prompt still cannot produce
an action this project does not allow. That is the same principle as
`loop.sanitise`, and for the same reason: the prompt is a request, the
validator is the rule.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

PLANNER_TIMEOUT_SECONDS = 90


@dataclass(frozen=True)
class Tool:
    """One thing the bot can actually do, as the model is told about it."""

    name: str
    description: str
    args: dict          # name -> short description, for the prompt
    required: tuple = ()
    touches_cart: bool = False


# The catalogue. This *is* the interface: a tool that is not here cannot
# be planned, however the request is phrased. Note what is deliberately
# absent — anything past "the cart is ready".
TOOLS: dict[str, Tool] = {
    "add_to_list": Tool(
        name="add_to_list",
        description="הוספת מוצר לרשימה הפנימית, שתיכנס לעגלה במחזור הבא",
        args={"item": "שם המוצר", "quantity": "מספר יחידות, ברירת מחדל 1",
              "amount": "כמות במידה (400) אם נאמרה", "unit": "גרם/קילו/ליטר אם נאמרה"},
        required=("item",),
    ),
    "remove_from_list": Tool(
        name="remove_from_list",
        description="הסרת מוצר מהרשימה הפנימית",
        args={"item": "שם המוצר"},
        required=("item",),
    ),
    "add_to_cart": Tool(
        name="add_to_cart",
        description="הוספת מוצר לעגלה האמיתית באתר, עכשיו",
        args={"item": "שם המוצר", "quantity": "מספר יחידות",
              "store": "shufersal או tivtaam, אם נאמרה רשת"},
        required=("item",), touches_cart=True,
    ),
    "set_cart_quantity": Tool(
        name="set_cart_quantity",
        description="שינוי הכמות של מוצר שכבר בעגלה ('בעצם שניים')",
        args={"item": "שם המוצר", "quantity": "הכמות החדשה",
              "store": "shufersal או tivtaam"},
        required=("item", "quantity"), touches_cart=True,
    ),
    "replace_in_cart": Tool(
        name="replace_in_cart",
        description="החלפת מוצר בעגלה באחר — תיקון, לא קנייה נוספת",
        args={"old": "המוצר שיוצא", "new": "המוצר שנכנס",
              "store": "shufersal או tivtaam"},
        required=("new",), touches_cart=True,
    ),
    "remove_from_cart": Tool(
        name="remove_from_cart",
        description="הסרת מוצר מהעגלה האמיתית",
        args={"item": "שם המוצר", "store": "shufersal או tivtaam"},
        required=("item",), touches_cart=True,
    ),
    "fill_cart": Tool(
        name="fill_cart",
        description="מחזור מלא: כל הרשימה והבקשות הממתינות נכנסות לעגלות",
        args={}, touches_cart=True,
    ),
    "report_shopped": Tool(
        name="report_shopped",
        description="המשתמש מדווח שהקנייה הושלמה. store אם נאמרה רשת מסוימת",
        args={"store": "shufersal או tivtaam, אם נאמרה"},
        touches_cart=True,
    ),
    "price_check": Tool(
        name="price_check",
        description="כמה עולה מוצר, ואיפה הוא זול יותר",
        args={"item": "שם המוצר"}, required=("item",),
    ),
    "show_deals": Tool(
        name="show_deals",
        description="מבצעים רלוונטיים",
        args={"item": "מוצר מסוים, אם נשאל על מוצר"},
    ),
    "show_list": Tool(
        name="show_list", description="הצגת הרשימה הפנימית", args={},
    ),
    "show_cart": Tool(
        name="show_cart", description="מה יש עכשיו בעגלה", args={},
    ),
    "recipe": Tool(
        name="recipe", description="פירוק מנה למצרכים",
        args={"dish": "שם המנה", "servings": "מספר סועדים אם נאמר"},
        required=("dish",),
    ),
    "meal_plan": Tool(
        name="meal_plan", description="תפריט שבועי",
        args={"note": "העדפות אם נאמרו"},
    ),
    "report_waste": Tool(
        name="report_waste", description="דיווח על מוצר שנזרק",
        args={"item": "שם המוצר", "note": "כמה/למה, אם נאמר"},
        required=("item",),
    ),
    "remember_preference": Tool(
        name="remember_preference",
        description="לזכור בחירת מוצר למונח מסוים מכאן והלאה",
        args={"term": "המונח", "item": "המוצר הנבחר",
              "scope": "always או once"},
        required=("term", "item"),
    ),
}

# Names a model might reach for that must never resolve to anything. Kept
# explicit so a refusal is logged as a refusal rather than as a typo, and
# so the test suite can assert on the list.
FORBIDDEN = frozenset({
    "checkout", "pay", "place_order", "submit_order", "confirm_purchase",
    "enter_payment", "update_account", "change_address", "apply_coupon",
    "join_club", "cancel_order",
})

MAX_STEPS = 6


@dataclass
class Step:
    tool: str
    args: dict = field(default_factory=dict)
    why: str = ""


@dataclass
class Plan:
    steps: list[Step] = field(default_factory=list)
    question: str = ""      # asked instead of guessing
    reply: str = ""         # what to say back
    refusals: list[str] = field(default_factory=list)
    raw: str = ""
    model_calls: int = 0
    seconds: float = 0.0


def _tool_catalogue() -> str:
    lines = []
    for tool in TOOLS.values():
        args = ", ".join(f"{k} ({v})" for k, v in tool.args.items()) or "ללא"
        lines.append(f"- {tool.name}: {tool.description}. פרמטרים: {args}")
    return "\n".join(lines)


_PROMPT_HEADER = """אתה שכבת ההבנה של בוט קניות משפחתי בעברית. אתה לא מסווג הודעות לקטגוריות — אתה מבין מה המשפחה רוצה ומתרגם את זה לתוכנית פעולה.

החזר JSON בלבד:
{"steps":[{"tool":"...","args":{...},"why":"..."}],"question":"","reply":"..."}

כללים:
- steps לפי הסדר שבו צריך לבצע. אפשר כמה, אפשר אחד, אפשר אפס.
- הודעה אחת יכולה להכיל כמה בקשות — אל תוותר על אף אחת.
- question: אם יש עמימות **מהותית** שאי אפשר להכריע מההקשר (איזה מוצר בדיוק,
  איזו רשת כששתיהן פתוחות). שאלה עוצרת את הצעדים שאחריה. אל תשאל על מה
  שאפשר להסיק.
- reply: משפט קצר בעברית, מה נעשה. בלי הסברים טכניים.
- אל תמציא שמות מוצרים, מחירים, ברקודים או כמויות שלא נאמרו. הקוד מוצא את
  המוצר עצמו — אתה מוסר מה המשפחה ביקשה.
- הבחנה קבועה: "רשימה" = הרשימה הפנימית. "עגלה"/"סל" = האתר האמיתי.
- אין checkout, אין תשלום, אין שינוי פרטי חשבון. זה לא קיים ככלי.

הכלים:
"""


def build_prompt(message: str, context: dict) -> str:
    parts = [_PROMPT_HEADER, _tool_catalogue()]
    background = describe_context(context)
    if background:
        parts.append("\nמצב נוכחי (רקע, לא ההודעה):\n" + background)
    parts.append(f'\nההודעה:\n"{message}"')
    return "\n".join(parts)


def describe_context(context: dict) -> str:
    """The household's real state, as a few lines the model can read.

    Everything here is a fact read from storage or the cart, never a
    guess: what is on the list, what is waiting, what the last turn was
    about, whether a shop is in progress. The point of the experiment is
    that *this* is what makes a message understandable — not a taxonomy.
    """
    if not context:
        return ""
    lines = []
    if context.get("last_subject"):
        lines.append(f"- דובר לאחרונה על: {context['last_subject']}")
    if context.get("pending"):
        lines.append("- ממתין ברשימה: " + ", ".join(context["pending"][:20]))
    for store, state in (context.get("carts") or {}).items():
        lines.append(
            f"- עגלת {store}: {state.get('count', 0)} פריטים"
            + (f", כולל {', '.join(state.get('items', [])[:8])}" if state.get("items") else "")
        )
    if context.get("open_questions"):
        lines.append(f"- שאלות בחירה פתוחות: {context['open_questions']}")
    if context.get("last_shop"):
        lines.append(f"- הקנייה האחרונה דווחה ב-{context['last_shop']}")
    if context.get("stores"):
        lines.append("- רשתות פעילות: " + ", ".join(context["stores"]))
    return "\n".join(lines)


def validate(payload: dict) -> Plan:
    """Turn whatever came back into a plan this project is allowed to run.

    The barrier, not a formality. Applied to the parsed JSON regardless of
    what the prompt said, so an ignored instruction cannot become an
    action. Anything rejected is recorded in `refusals` rather than
    dropped silently — a refusal nobody can see is indistinguishable from
    a model that never proposed it, and that ambiguity has already cost
    this project two rounds of guessing elsewhere.
    """
    plan = Plan(
        question=str(payload.get("question") or "").strip(),
        reply=str(payload.get("reply") or "").strip(),
    )
    for raw in (payload.get("steps") or [])[:MAX_STEPS]:
        if not isinstance(raw, dict):
            plan.refusals.append("step is not an object")
            continue
        name = str(raw.get("tool") or "").strip()
        if name in FORBIDDEN:
            logger.warning("planner: refused forbidden tool %r", name)
            plan.refusals.append(f"forbidden:{name}")
            continue
        tool = TOOLS.get(name)
        if tool is None:
            logger.info("planner: unknown tool %r", name)
            plan.refusals.append(f"unknown:{name}")
            continue
        args = raw.get("args") if isinstance(raw.get("args"), dict) else {}
        clean = _clean_args(tool, args, plan)
        if clean is None:
            continue
        plan.steps.append(Step(tool=name, args=clean, why=str(raw.get("why") or "")))
    return plan


def _clean_args(tool: Tool, args: dict, plan: Plan) -> dict | None:
    """Only declared arguments, only sane values, or no step at all."""
    clean: dict = {}
    for key, value in args.items():
        if key not in tool.args:
            continue  # an argument nobody declared cannot mean anything
        if key in ("quantity",):
            try:
                number = int(float(value))
            except (TypeError, ValueError):
                continue
            # A quantity is a person's decision about their own kitchen;
            # what it must not be is a model's stray zero or a typo that
            # orders forty. Out of range means "unstated", not "forty".
            if 1 <= number <= 12:
                clean[key] = number
            continue
        if key == "store":
            store = str(value or "").strip()
            if store in ("shufersal", "tivtaam"):
                clean[key] = store
            continue
        if key == "scope":
            scope = str(value or "").strip()
            if scope in ("once", "always"):
                clean[key] = scope
            continue
        if key == "amount":
            try:
                clean[key] = float(value)
            except (TypeError, ValueError):
                pass
            continue
        text = str(value or "").strip()
        if text:
            clean[key] = text[:120]
    missing = [key for key in tool.required if not clean.get(key)]
    if missing:
        plan.refusals.append(f"{tool.name}:missing:{','.join(missing)}")
        return None
    return clean


def _ask_model(prompt: str) -> str:
    from .nlu import _claude_cli

    result = subprocess.run(
        [_claude_cli(), "-p", prompt],
        capture_output=True, text=True,
        timeout=PLANNER_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise RuntimeError(f"planner CLI failed: {result.stderr[:200]}")
    return result.stdout


def plan_message(message: str, context: dict | None = None) -> Plan:
    """One model call, one validated plan. Never raises.

    A failure returns an empty plan carrying a question, which is the
    honest outcome: the bot does not know what was meant and says so,
    rather than filing the sentence as a product the way the old
    rule-based fallback did.
    """
    import time

    from .nlu import _extract_json

    text = (message or "").strip()
    if not text:
        return Plan(question="")
    started = time.monotonic()
    try:
        raw = _ask_model(build_prompt(text, context or {}))
        payload = _extract_json(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("planner: no usable plan (%s)", exc)
        return Plan(
            question="לא הצלחתי להבין את ההודעה כרגע. אפשר לנסח שוב?",
            model_calls=1, seconds=time.monotonic() - started,
        )
    plan = validate(payload if isinstance(payload, dict) else {})
    plan.raw = raw[:2000]
    plan.model_calls = 1
    plan.seconds = time.monotonic() - started
    return plan
