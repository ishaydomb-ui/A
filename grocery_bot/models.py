"""Plain data structures shared across storage, orchestrator, adapters and the bot."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BaseListItem:
    """A recurring item from the household's standing shopping list."""

    id: int
    name: str
    search_terms: dict[str, str] = field(default_factory=dict)
    default_quantity: int = 1
    tags: list[str] = field(default_factory=list)
    active: bool = True

    def search_term_for(self, store: str) -> str:
        return self.search_terms.get(store, self.name)


@dataclass
class AdHocRequest:
    """A one-off item requested through the Telegram bot between cycles."""

    id: int
    text: str
    requested_by: str
    created_at: str
    quantity: int = 1
    consumed: bool = False


@dataclass
class CartAddResult:
    """Outcome of trying to add a single item to a single store's cart."""

    item_name: str
    store: str
    status: str  # "added" | "ambiguous" | "not_found" | "error"
    detail: str = ""
    candidates: list[str] = field(default_factory=list)
    quantity: int = 1


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
