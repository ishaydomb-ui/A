"""Plain data structures shared across storage, orchestrator, adapters and the bot."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BaseListItem:
    """A recurring item from the household's standing shopping list.

    `amount`/`unit` carry what `default_quantity` can't: "300 גרם
    פסטרמה" is one line on a shopping list, not 300 of something. Both
    stay optional — plenty of items really are just "2 × milk".

    `brand` is the household's usual pick (טונה סטארקיסט). It's kept
    separate from `name` rather than baked into it so the deal finder
    can still search the generic name and surface a cheaper competing
    brand — the "brand lock-in" pain point this project exists for.
    """

    id: int
    name: str
    search_terms: dict[str, str] = field(default_factory=dict)
    default_quantity: int = 1
    tags: list[str] = field(default_factory=list)
    active: bool = True
    amount: float | None = None
    unit: str = ""
    brand: str = ""

    def search_term_for(self, store: str) -> str:
        if store in self.search_terms:
            return self.search_terms[store]
        return f"{self.name} {self.brand}".strip() if self.brand else self.name

    def describe(self) -> str:
        """One-line human description, e.g. 'פסטרמה 300 גרם (תנובה)'."""
        parts = [self.name]
        if self.amount and self.unit:
            parts.append(f"{self.amount:g} {self.unit}")
        elif self.amount:
            parts.append(f"x{self.amount:g}")
        elif self.default_quantity != 1:
            parts.append(f"x{self.default_quantity}")
        line = " ".join(parts)
        return f"{line} ({self.brand})" if self.brand else line


@dataclass
class AdHocRequest:
    """A one-off item requested through the Telegram bot between cycles."""

    id: int
    text: str
    requested_by: str
    created_at: str
    quantity: int = 1
    consumed: bool = False
    amount: float | None = None
    unit: str = ""
    brand: str = ""

    def describe(self) -> str:
        parts = [self.text]
        if self.amount and self.unit:
            parts.append(f"{self.amount:g} {self.unit}")
        elif self.amount:
            parts.append(f"x{self.amount:g}")
        line = " ".join(parts)
        return f"{line} ({self.brand})" if self.brand else line


@dataclass
class CartAddResult:
    """Outcome of trying to add a single item to a single store's cart."""

    item_name: str
    store: str
    status: str  # "added" | "ambiguous" | "not_found" | "error"
    detail: str = ""
    candidates: list[str] = field(default_factory=list)
    quantity: int = 1
    # The store's own product id for what was added. Carried back so a
    # remembered choice can be keyed on the code rather than the display
    # name, which is neither unique nor stable.
    product_code: str = ""
    # Full detail for each candidate (name/code/price/size/brand), so a
    # chooser can show what actually distinguishes them. `candidates`
    # keeps only names, which are routinely duplicated across brands.
    candidate_cards: list[dict] = field(default_factory=list)
    # Set when the cycle picked a product without asking, and why
    # ("history" | "exact_name"); shown so an automatic choice is never
    # silent.
    auto_resolved: str = ""


@dataclass
class OrderCycleReport:
    """Aggregated outcome of running one order cycle against one store."""

    store: str
    added: list[CartAddResult] = field(default_factory=list)
    ambiguous: list[CartAddResult] = field(default_factory=list)
    not_found: list[CartAddResult] = field(default_factory=list)
    errors: list[CartAddResult] = field(default_factory=list)

    def record(self, result: CartAddResult) -> None:
        bucket = {
            "added": self.added,
            "ambiguous": self.ambiguous,
            "not_found": self.not_found,
            "error": self.errors,
        }[result.status]
        bucket.append(result)
