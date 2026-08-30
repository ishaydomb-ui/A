"""Build paste-ready shopping lists for Shufersal's own bulk entry.

The user's proposed flow is: keep adding items through the week, ask for
a full list, then finish in the Shufersal app — filtering there, where
they are fast, rather than through a chat keyboard.

The obvious implementation (have the bot fill the cart) does not fit it:
adding one product takes 10-40s of browser automation, so 89 items is
half an hour and the ~300 the household actually reviews is over two.

Shufersal already solves this. Its "הזמנה מהירה" box takes a plain
newline-separated list of product names, matches them **ranked by the
household's own purchase history**, and shows each with price and unit
price to pick from. So the bot's job is not to drive a cart at all — it
is to decide *what belongs on the list* and hand over text to paste.
That is instant, mutates nothing, and leaves every decision with the
user.

**Thresholds.** Two are offered because they answer different questions,
and the split is visible in the data (19 orders):

    35%+ -> 22 products, 86% of them fresh produce and dairy
    15%+ -> 89 products, spanning every department

The first is a weekly top-up, the second is closer to the full review
the household does today.

**Splitting by rhythm.** Departments replenish at genuinely different
rates — produce every 1.5 orders, frozen every 4.2, a threefold spread —
so "fresh" and "pantry" are offered separately. Buying pantry goods on a
weekly cadence is how a cupboard ends up with four bottles of soy sauce.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Departments that turn over weekly, versus those bought every few shops.
FRESH_DEPARTMENTS = {"פירות וירקות", "מוצרי חלב וקירור"}


@dataclass
class ListSpec:
    """One named list: which products, and why."""

    key: str
    title: str
    description: str
    min_share: float
    departments: set[str] | None = None  # None = every department
    items: list = field(default_factory=list)


def available_lists() -> list[ListSpec]:
    """The list shapes on offer, before any products are attached."""
    return [
        ListSpec(
            key="core",
            title="ליבה (35%+)",
            description="מה שנקנה כמעט בכל הזמנה — רובו טרי",
            min_share=0.35,
        ),
        ListSpec(
            key="full",
            title="מלאה (15%+)",
            description="קרוב לרשימה המלאה שאתם עוברים עליה היום",
            min_share=0.15,
        ),
        ListSpec(
            key="fresh",
            title="טרי בלבד (15%+)",
            description="ירקות, פירות וחלב — מתחדש כל 1.5-2 הזמנות",
            min_share=0.15,
            departments=FRESH_DEPARTMENTS,
        ),
        ListSpec(
            key="pantry",
            title="מזווה ובית (15%+)",
            description="יבשים, שימורים, קפואים וניקיון — כל 2.5-4 הזמנות",
            min_share=0.15,
            departments=None,  # filled below as "everything except fresh"
        ),
    ]


def build(spec: ListSpec, stock_rows: list[dict]) -> ListSpec:
    """Attach the products matching a spec, most-frequent first."""
    chosen = []
    for row in stock_rows:
        if row["share"] < spec.min_share:
            continue
        department = row.get("department", "")
        if spec.key == "pantry" and department in FRESH_DEPARTMENTS:
            continue
        if spec.departments is not None and department not in spec.departments:
            continue
        chosen.append(row)
    spec.items = sorted(chosen, key=lambda row: -row["share"])
    return spec


def as_paste_text(spec: ListSpec) -> str:
    """Just the product names, one per line — what the box expects.

    Deliberately bare: no counts, no prices, no bullets. Anything else
    gets matched as part of a product name and quietly ruins a row.
    """
    return "\n".join(row["product_name"] for row in spec.items)


def summarise(spec: ListSpec) -> str:
    """A human summary of a list, grouped the way the shop is laid out."""
    if not spec.items:
        return f"*{spec.title}* — אין מוצרים שעונים על הסף."
    by_department: dict[str, list[dict]] = {}
    for row in spec.items:
        by_department.setdefault(row.get("department") or "שונות", []).append(row)

    lines = [f"*{spec.title}* — {len(spec.items)} מוצרים", f"_{spec.description}_", ""]
    for department, rows in sorted(by_department.items(), key=lambda pair: -len(pair[1])):
        lines.append(f"▸ {department} ({len(rows)})")
    return "\n".join(lines)
