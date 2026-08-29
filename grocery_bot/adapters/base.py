"""Common interface every store automation backend implements.

Keeping this abstraction thin on purpose: the orchestrator only ever needs
to search for an item and add it to the real cart, and to know whether the
saved login session is still usable.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import CartAddResult


class StoreAdapter(ABC):
    name: str

    @abstractmethod
    def is_session_valid(self) -> bool:
        """Return False if the saved session needs the user to log in again."""

    @abstractmethod
    def search_and_add(self, term: str, quantity: int = 1) -> CartAddResult:
        """Search for `term` and add it to the cart if there's a single clear match.

        Returns a CartAddResult with status "added", "ambiguous" (multiple
        plausible matches — candidates populated), "not_found", or "error".
        """

    @abstractmethod
    def add_specific_product(
        self, product_label: str, quantity: int = 1, product_code: str = "", search_term: str = ""
    ) -> CartAddResult:
        """Add an exact product (one of a previous ambiguous result's candidates)."""

    @abstractmethod
    def close(self) -> None:
        """Release browser/session resources."""

    def __enter__(self) -> "StoreAdapter":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
