"""Human confirmation of a product choice, accumulated only from real
interactions — vNext Phase 1.5, API surface only.

A term→product mapping becomes human-confirmed through exactly four
kinds of event (spec §6): the household says "this is the right one",
accepts a substitution, keeps an exception choice Gordon offered, or
corrects a choice later. Repeated purchases raise *inferred* product
confidence in the resolver but never create a row here — that is the
line between INFERENCE and HUMAN_DECLARED, and it is why this is not
`preferred_products`.

Nothing in production calls `record_human_confirmation` yet; Phase 2
would wire the Telegram interactions above to it. The vNext resolver
already reads the table as HUMAN_DECLARED evidence.
"""
from __future__ import annotations

KINDS = ("explicit_statement", "accepted_substitution", "kept_exception_choice", "later_correction")


def record_human_confirmation(storage, term: str, product_code: str, store: str, kind: str,
                              product_name: str = "", confirmed_by: str = "", note: str = "") -> int:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")
    return storage.add_vnext_product_confirmation(
        store=store, term=term, product_code=product_code, product_name=product_name,
        kind=kind, confirmed_by=confirmed_by, note=note,
    )


def confirmations_for(storage, term: str, store: str | None = None) -> list[dict]:
    from .storage import normalize_term

    key = normalize_term(term)
    return [r for r in storage.list_vnext_product_confirmations(store) if r.get("term") == key]
