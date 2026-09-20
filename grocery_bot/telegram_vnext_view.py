"""Presentation helpers for the future simplified UX — vNext Phase 1.

Pure functions from a `ShoppingPlan` / `Readiness` to message text. Not
wired into `telegram_bot.py`; nothing here sends. Plain text (no parse
mode) so a product name with `*` or `<` cannot break a send — the trap
`mdtext.py`/`htmltext.py` exist for.

Hebrew count phrasing: 1 → singular form, 2+ → plural with the number.
"""
from __future__ import annotations

from .shopping_plan import ShoppingPlan
from .shopping_readiness import Readiness

PLAN_READY = "הקנייה מוכנה"
READINESS_HEAD = "כדאי להכין קנייה"
READINESS_SOON = "בקרוב כדאי להכין קנייה"
READINESS_WAIT = "אין צורך בקנייה כרגע"


def count(n: int, singular: str, plural: str, none: str = "") -> str:
    """'דבר אחד' / '6 דברים'; `none` for zero (empty string drops the line)."""
    n = int(n)
    if n == 0:
        return none
    if n == 1:
        return singular
    return f"{n} {plural}"


def readiness_message(r: Readiness, plan: ShoppingPlan | None = None) -> str:
    s = r.signals
    if r.suggested_action == "prepare_now":
        head = READINESS_HEAD
    elif r.suggested_action == "prepare_soon":
        head = READINESS_SOON
    else:
        head = READINESS_WAIT
    lines = [head]
    parts = [
        count(s.get("essentials_due", 0), "דבר אחד כנראה עומד להיגמר", "דברים כנראה עומדים להיגמר"),
        count(s.get("explicit_pending", 0), "בקשה פתוחה אחת", "בקשות פתוחות"),
    ]
    meal_items = s.get("meal_items", 0)
    if meal_items and plan is not None:
        meal = next((i.meal_or_event for i in plan.items if i.meal_or_event), "")
        parts.append(f"{meal} מוסיפה {count(meal_items, 'פריט אחד', 'פריטים')}" if meal else
                     count(meal_items, "פריט אחד לארוחה מתוכננת", "פריטים לארוחות מתוכננות"))
    parts.append(count(s.get("savings_opportunities", 0),
                       "מצאתי הזדמנות חיסכון טובה אחת", "הזדמנויות חיסכון טובות", ""))
    if s.get("savings_opportunities", 0) > 1:
        parts[-1] = "מצאתי " + parts[-1]
    lines += [p for p in parts if p]
    return "\n".join(lines)


def plan_message(plan: ShoppingPlan) -> str:
    s = plan.summary
    total = s["auto_include"]
    lines = [PLAN_READY, count(total, "פריט אחד", "פריטים", "אין פריטים")]
    routine = s.get("routine", 0)
    explicit = len([i for i in plan.auto_items if i.mandatory])
    meal = len([i for i in plan.auto_items if i.meal_or_event])
    stock = len([i for i in plan.auto_items if i.stock_up])
    meal_name = next((i.meal_or_event for i in plan.auto_items if i.meal_or_event), "")
    meal_one = f"אחד ל{meal_name}" if meal_name else "אחד לארוחה מתוכננת"
    meal_many = f"ל{meal_name}" if meal_name else "לארוחות מתוכננות"
    for n, one, many in (
        (routine, "אחד לצריכה שוטפת", "צריכה שוטפת"),
        (explicit, "בקשה אחת", "בקשות"),
        (meal, meal_one, meal_many),
        (stock, "אחד אגירה בגלל מחיר טוב", "אגירה בגלל מחיר טוב"),
    ):
        text = count(n, one, many)
        if text:
            lines.append(text)
    return "\n".join(lines)


def exception_message(plan: ShoppingPlan, limit: int = 8) -> str:
    decisions = plan.exceptions
    n = len(decisions)
    if n == 0:
        return "אין החלטות פתוחות — הכל ברור."
    head = "צריך ממך החלטה אחת בלבד" if n == 1 else f"צריך ממך {n} החלטות בלבד"
    lines = [head, ""]
    for item in decisions[:limit]:
        why = item.unresolved_decisions[0] if item.unresolved_decisions else item.reason
        lines.append(f"• {item.display_name} — {_short_reason(item, why)}")
    if n > limit:
        lines.append(f"ועוד {n - limit}.")
    return "\n".join(lines)


def _short_reason(item, why: str) -> str:
    if "no known product" in why:
        return "לא יודע איזה מוצר בדיוק"
    if "inferred" in why:
        return "ניחוש של מוצר, לא אישור שלך"
    if "already in a cart" in why:
        return "אולי כבר בעגלה"
    if "more than one chain" in why:
        return "באיזו רשת?"
    if item.stock_up:
        return "מבצע טוב — לאגור?"
    if item.meal_or_event:
        return f"ל{item.meal_or_event} — יש בבית?"
    return "לא בטוח שצריך"
