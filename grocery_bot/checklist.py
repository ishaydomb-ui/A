"""Rendering for the department checklists.

The user's own routine is to add everything and delete what isn't needed
this week, which is the right call when a missing item costs a week
without milk and an unwanted one costs a tap. This keeps that shape —
everything visible, pre-ticked, removable — but replaces one 300-item
list with six department screens.

Layout follows what the earlier chooser taught: Telegram truncates a
long inline-button label at roughly 24 Hebrew characters, so the product
detail lives in the message text and the buttons stay short. Each item
gets one toggle button showing its state, and a department carries
"select all"/"clear" plus a confirm.
"""
from __future__ import annotations

TICK = "✅"
UNTICK = "⬜"


def _quantity_label(item: dict) -> str:
    amount, unit = item.get("amount"), item.get("unit") or ""
    if amount and unit:
        return f"{amount:g} {unit}"
    quantity = item.get("quantity", 1) or 1
    return f"×{quantity}" if quantity != 1 else ""


def render_department(department: str, items: list[dict], index: int, total: int) -> str:
    """The message body for one department's checklist."""
    chosen = sum(1 for item in items if item.get("selected"))
    lines = [f"*{department}* ({chosen}/{len(items)}) · מחלקה {index}/{total}", ""]
    for position, item in enumerate(items, start=1):
        mark = TICK if item.get("selected") else UNTICK
        quantity = _quantity_label(item)
        suffix = f" · {quantity}" if quantity else ""
        lines.append(f"{mark} {position}. {item['product_name']}{suffix}")
    lines.append("")
    lines.append("_הקישו על מספר כדי להוסיף או להסיר._")
    return "\n".join(lines)


def render_summary(departments: list[tuple[str, list[dict]]]) -> str:
    """What the whole proposal currently adds up to."""
    total_selected = sum(
        1 for _, items in departments for item in items if item.get("selected")
    )
    total_items = sum(len(items) for _, items in departments)
    lines = [f"🧾 *סיכום ההצעה* — {total_selected} מתוך {total_items} פריטים", ""]
    for name, items in departments:
        chosen = sum(1 for item in items if item.get("selected"))
        lines.append(f"• {name}: {chosen}/{len(items)}")
    lines.append("")
    lines.append("_אפשר עדיין לשנות בכל מחלקה. 'אישור ומילוי' ימלא את הסל._")
    return "\n".join(lines)


def render_panel(
    departments: list[tuple[str, list[dict]]], open_index: int
) -> str:
    """The whole proposal as ONE message with one department expanded.

    A real run scattered six department messages plus questions plus a
    summary, and the user reported exactly what that does: scrolling
    around to find where the process even is. Telegram cannot collapse
    messages, but it can edit one in place — so this renders an
    accordion: every department as a header line, the open one expanded,
    and tapping a header re-renders the same message.
    """
    total_selected = sum(1 for _, items in departments for i in items if i.get("selected"))
    total = sum(len(items) for _, items in departments)
    lines = [f"🛒 *הצעת קנייה* — {total_selected}/{total} מסומנים", ""]
    for index, (name, items) in enumerate(departments):
        chosen = sum(1 for i in items if i.get("selected"))
        if index == open_index:
            lines.append(f"▾ *{name}* ({chosen}/{len(items)})")
            for position, item in enumerate(items, start=1):
                mark = TICK if item.get("selected") else UNTICK
                quantity = _quantity_label(item)
                suffix = f" · {quantity}" if quantity else ""
                lines.append(f"   {mark} {position}. {item['product_name']}{suffix}")
        else:
            lines.append(f"▸ {name} ({chosen}/{len(items)})")
    lines.append("")
    lines.append("_הקישו על מחלקה לפתוח, על מספר לסמן/להסיר._")
    return "\n".join(lines)
