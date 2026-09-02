"""A live cart view for a chat-only workflow.

Everything here happens through Telegram on a phone, and a full cycle is
minutes of page loads. Without a live view the user sends "/start_order"
and then watches nothing at all until a summary lands — a slow run and a
stuck one look identical, which is the worst property a long job can
have.

So the bot keeps **one** message and rewrites it as the run proceeds,
rather than emitting a message per item (twenty notifications for one
shop). The same renderer draws the finished state, so the thing the user
watched fill up is the thing left behind as the receipt.

**On the total.** The running figure is the sum of shelf prices of what
went in, and it is labelled an estimate because it genuinely is one: the
real bill includes delivery, club discounts, and the weight the store
actually recorded for loose produce. The authoritative number is read
back from the cart page at the end. Showing our own sum as if it were
final would be a small lie that surfaces at checkout.

**On staleness.** The cart lives behind the Israeli exit node, which is a
TV box that gets switched off. A view that silently freezes is worse than
no view, so every render carries the time it was made, and an unreadable
cart says so instead of showing the last good numbers as if current.
"""
from __future__ import annotations

from datetime import datetime

from .mdtext import escape as md

# Telegram rejects rapid edits to the same message (and eventually rate
# limits the bot), so progress is redrawn at most this often. Two seconds
# is comfortably under the limit and still reads as live.
MIN_EDIT_INTERVAL_SECONDS = 2.0

# A long cycle would otherwise produce an unreadably long message.
MAX_VISIBLE_ROWS = 14

_STATUS_ICON = {
    "added": "✅",
    "ambiguous": "❓",
    "not_found": "⚠️",
    "error": "🛑",
}


def _money(value: float | None) -> str:
    return f"{value:,.2f}₪" if value is not None else "—"


def _row(result) -> str:
    icon = _STATUS_ICON.get(result.status, "•")
    name = md((result.item_name or "").strip())
    if result.status == "added":
        price = getattr(result, "price", None)
        quantity = getattr(result, "quantity", 1) or 1
        line = f"{icon} {name}"
        # Loose produce is bought by weight, so "×1" says nothing useful:
        # what matters is half a kilo versus two kilos.
        amount = getattr(result, "amount", None)
        unit = getattr(result, "unit", "") or ""
        if amount and unit:
            line += f" · {amount:g} {unit}"
        elif quantity and quantity != 1:
            line += f" ×{quantity}"
        if price is not None:
            line += f" — {_money(price * quantity)}"
        # Mark a personal request so it is distinguishable from a standing
        # -list item at a glance.
        asked_by = getattr(result, "requested_by", "")
        if asked_by:
            line += f" 🙋{md(asked_by)}"
        return line
    if result.status == "ambiguous":
        return f"{icon} {name} — צריך בחירה"
    if result.status == "not_found":
        return f"{icon} {name} — לא נמצא"
    return f"{icon} {name} — שגיאה"


def render_progress(results: list, done: int, total: int, when: datetime | None = None) -> str:
    """The in-flight view: what has gone in so far, and how far along."""
    stamp = (when or datetime.now()).strftime("%H:%M")
    lines = [f"🛒 *ממלא את העגלה…* ({done}/{total}) · {stamp}", ""]

    visible = results[-MAX_VISIBLE_ROWS:]
    if len(results) > MAX_VISIBLE_ROWS:
        lines.append(f"_…ועוד {len(results) - MAX_VISIBLE_ROWS} פריטים קודמים_")
    lines += [_row(r) for r in visible]

    estimate = sum(
        (getattr(r, "price", None) or 0) * (getattr(r, "quantity", 1) or 1)
        for r in results
        if r.status == "added"
    )
    added = sum(1 for r in results if r.status == "added")
    lines += ["", f"*בערך {_money(estimate)}* · {added} פריטים"]
    lines.append("_הערכה לפי מחירי מדף — הסכום הסופי בסוף התהליך._")
    return "\n".join(lines)


def _cart_body(results: list, cart: dict | None) -> list[str]:
    """One chain's rows and its total. Shared by both final renderers."""
    added = [r for r in results if r.status == "added"]
    problems = [r for r in results if r.status != "added"]

    lines = [_row(r) for r in added[:MAX_VISIBLE_ROWS]]
    if len(added) > MAX_VISIBLE_ROWS:
        lines.append(f"_…ועוד {len(added) - MAX_VISIBLE_ROWS} פריטים_")

    if problems:
        lines.append("")
        lines += [_row(r) for r in problems]

    lines.append("")
    if cart and cart.get("ok") and cart.get("total") is not None:
        lines.append(f"*סה\"כ לתשלום: {_money(cart['total'])}* · {len(cart.get('items', []))} פריטים")
        lines.append("_כולל דמי משלוח/שירות, לפי הסל באתר._")
    else:
        estimate = sum(
            (getattr(r, "price", None) or 0) * (getattr(r, "quantity", 1) or 1) for r in added
        )
        lines.append(f"*בערך {_money(estimate)}* · {len(added)} פריטים")
        # Say why the real number is missing rather than passing an
        # estimate off as the total.
        lines.append("_לא הצלחתי לקרוא את הסל באתר, אז זו הערכה בלבד._")
    return lines


def render_final(results: list, cart: dict | None, when: datetime | None = None) -> str:
    """The finished view for a single chain."""
    stamp = (when or datetime.now()).strftime("%H:%M")
    lines = [f"🛒 *העגלה מוכנה* · {stamp}", ""]
    lines += _cart_body(results, cart)

    # The hand-off matters as much as the list: this is the moment the
    # user switches to the store's app, and without saying so explicitly
    # they are left guessing whether anything else is expected of them.
    lines.append("")
    lines.append("*מה עכשיו:* להיכנס לשופרסל, לעבור על הסל, ולשלם.")
    lines.append("⚠️ _לא בוצעה קנייה — הסל מוכן לבדיקה ותשלום שלך._")
    return "\n".join(lines)


def render_final_by_store(
    reports: dict, carts: dict | None = None, when: datetime | None = None
) -> str:
    """The finished view when more than one chain was filled.

    A cycle runs against every enabled chain, so it leaves a real cart at
    each one. The earlier version of this message flattened them into a
    single list with a single total, which described no cart that actually
    existed: the user was handed one number for two baskets and one link
    to one of them. Each chain now gets its own section, its own total and
    its own missing items, because choosing between them is the decision
    this message exists to support.

    Falls through to the single-chain view when only one chain was filled,
    so an ordinary Shufersal-only run does not grow a redundant heading.
    """
    from .chains import display_name

    carts = carts or {}
    stores = [store for store, report in reports.items() if report.results]
    if len(stores) <= 1:
        store = stores[0] if stores else None
        results = list(reports[store].results) if store else []
        return render_final(results, carts.get(store), when)

    stamp = (when or datetime.now()).strftime("%H:%M")
    lines = [f"🛒 *הסלים מוכנים* · {stamp}"]
    for store in stores:
        lines.append("")
        lines.append(f"*{display_name(store)}*")
        lines += _cart_body(list(reports[store].results), carts.get(store))

    lines.append("")
    lines.append("*מה עכשיו:* לבחור רשת, לעבור על הסל שלה, ולשלם.")
    lines.append("⚠️ _לא בוצעה קנייה — הסלים מוכנים לבדיקה ותשלום שלך._")
    return "\n".join(lines)
