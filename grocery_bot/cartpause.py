"""Reversible cart-mutation pause, for a Work benchmark window (Ishay,
2026-09-18). Does NOT reuse AUTO_ADD_DEALS -- that flag gates only
dealfill's autonomous multi-buy suggestions, not the general
search_and_add / add_specific_product path a normal request uses
(2026-09-18 review, section D3).

Built on the existing app_state key/value primitive (storage.get_state /
set_state) -- no schema change. Key convention: "cart_paused:<store>" for
one retailer, "cart_paused:global" for all of them. Price/history
collection, product-intelligence learning, and every other Gordon
function are untouched by this -- it gates exactly the adapter write
calls in orchestrator.py's _add_one, nothing upstream or downstream of
them.
"""
from __future__ import annotations

GLOBAL_KEY = "cart_paused:global"


def _key(store: str | None) -> str:
    return f"cart_paused:{store}" if store else GLOBAL_KEY


def is_paused(storage, store: str | None = None) -> bool:
    """True if the global pause is on, or (when `store` is given) that
    store's own pause is on. A store-specific pause never affects other
    stores; the global pause affects all of them."""
    if storage.get_state(GLOBAL_KEY, "false") == "true":
        return True
    if store and storage.get_state(_key(store), "false") == "true":
        return True
    return False


def set_paused(storage, store: str | None, value: bool, reason: str = "", by: str = "") -> None:
    """store=None means the global pause. value is a bool.

    The reason/by pair is stored alongside the flag under the same
    app_state key/value primitive (no other structured-metadata
    convention exists in this table today), purely for /pausecart's own
    confirmation and /resumecart's ability to say what is being lifted.
    It is never read by the guard itself -- is_paused only ever looks at
    the boolean.
    """
    storage.set_state(_key(store), "true" if value else "false")
    if value and (reason or by):
        storage.set_state(_key(store) + ":reason", f"{by}: {reason}" if by else reason)
    elif not value:
        storage.set_state(_key(store) + ":reason", "")


def reason_for(storage, store: str | None = None) -> str:
    """The reason/by text last recorded for this key, if any. Empty when
    unset -- purely informational, not consulted by is_paused."""
    return storage.get_state(_key(store) + ":reason", "")
