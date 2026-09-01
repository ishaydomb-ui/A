"""One picture of what the household consumes, across every chain.

Until now each chain was its own world. The same carton of milk bought at
Shufersal and at Tiv Taam appeared as two unrelated products, each with
half the evidence, so both looked like occasional purchases and neither
carried a believable interval. A household that buys milk weekly, split
between two shops, looked like one that buys milk fortnightly at each.

The join is the EAN barcode, as everywhere else here. That works cleanly
for packaged goods — which is where the money and the stocking decisions
are — and not at all for loose produce, because Tiv Taam identifies
weighable items by an internal id and publishes no barcode for them. So
produce is matched by name as a fallback, and the result says which
method it used rather than pretending to one accuracy throughout.

What this is *for*: an interval that reflects real consumption rather
than one shop's share of it, so `shelflife` stops proposing things the
household already bought elsewhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .disambiguate import _normalise

# Chains whose stock rows describe this household's own buying.
HOUSEHOLD_STORES = ("shufersal", "tivtaam", "victory")


@dataclass
class Habit:
    """One product as the household actually consumes it, chain-agnostic."""

    name: str
    barcode: str = ""
    stores: dict = field(default_factory=dict)      # store -> share
    # store -> every measured interval for this product at that chain.
    # A list rather than one value because a chain can list the same
    # barcode under two product ids — Tiv Taam has cottage cheese twice,
    # at 35 days and 6 — and keeping only the last silently threw away
    # half the evidence.
    intervals: dict = field(default_factory=dict)
    department: str = ""
    matched_by: str = "barcode"

    @property
    def chains(self) -> list[str]:
        return sorted(self.stores)

    @property
    def split(self) -> bool:
        """Bought at more than one chain — the case that was invisible."""
        return len(self.stores) > 1

    @property
    def interval_days(self) -> float | None:
        """How often the household buys it, counting every chain.

        Rates add: buying something every 40 days at one shop and every 60
        at another is buying it every 24, not every 50. Averaging the two
        intervals — the obvious thing — would understate consumption by
        more than half.
        """
        measured = [
            value
            for values in self.intervals.values()
            for value in (values if isinstance(values, list) else [values])
            if value
        ]
        if not measured:
            return None
        rate = sum(1.0 / value for value in measured)
        return round(1.0 / rate, 1) if rate else None

    @property
    def share(self) -> float:
        """Best available confidence that this is a staple."""
        return max(self.stores.values()) if self.stores else 0.0


def _key(row: dict, barcodes: dict) -> tuple[str, str]:
    """Identify a stock row across chains: barcode first, name as fallback."""
    code = barcodes.get(row["product_code"])
    if code:
        return code, "barcode"
    return _normalise(row.get("product_name", "")), "name"


def build(storage, stores=HOUSEHOLD_STORES) -> list[Habit]:
    """Merge every chain's stock rows into one view per product."""
    # Two ways a stock row learns its barcode. Chains that record one
    # directly (Tiv Taam, after the smart-list sync) already carry it.
    # Shufersal does not: its codes are "P_<sku>" and the catalogue keys
    # on the EAN, which *ends* with that sku — the same join the order
    # history uses.
    barcodes: dict[str, str] = {}
    for store in stores:
        for row in storage.list_stock_items(store):
            existing = row.get("barcode")
            if existing:
                barcodes[row["product_code"]] = str(existing)
                continue
            code = str(row["product_code"]).removeprefix("P_")
            if code.isdigit():
                found = storage.catalog_price_by_suffix(code)
                if found:
                    barcodes[row["product_code"]] = found["item_code"]

    merged: dict[str, Habit] = {}
    for store in stores:
        for row in storage.list_stock_items(store):
            key, how = _key(row, barcodes)
            if not key:
                continue
            habit = merged.get(key)
            if habit is None:
                habit = Habit(
                    name=row.get("product_name", ""),
                    barcode=barcodes.get(row["product_code"], ""),
                    department=row.get("department", "") or "",
                    matched_by=how,
                )
                merged[key] = habit
            habit.stores[store] = float(row.get("share") or 0)
            if row.get("interval_days"):
                habit.intervals.setdefault(store, []).append(float(row["interval_days"]))
            if not habit.department:
                habit.department = row.get("department", "") or ""
    return sorted(merged.values(), key=lambda h: h.share, reverse=True)


def split_across_chains(storage, stores=HOUSEHOLD_STORES) -> list[Habit]:
    """Products bought at more than one chain — previously counted twice."""
    return [h for h in build(storage, stores) if h.split]


def format_habits(habits: list[Habit], limit: int = 15) -> str:
    from .mdtext import escape

    if not habits:
        return "אין עדיין מספיק היסטוריה כדי לבנות פרופיל צריכה."
    lines = ["*מה הבית באמת צורך*", ""]
    for habit in habits[:limit]:
        where = " + ".join(habit.chains)
        interval = (
            f"כל {habit.interval_days:.0f} ימים" if habit.interval_days else "תדירות לא ידועה"
        )
        mark = " 🔀" if habit.split else ""
        lines.append(f"• *{escape(habit.name)}*{mark} — {interval} _({where})_")
    if any(h.split for h in habits[:limit]):
        lines += ["", "_🔀 = נקנה ביותר מרשת אחת_"]
    return "\n".join(lines)
