"""Human confirmation of a product choice, accumulated only from real
interactions -- vNext Phase 1.5 API, wired in Phase 2a.

A term->product mapping becomes human-confirmed through exactly four
kinds of event (spec §6): the household says "this is the right one",
accepts a substitution, keeps an exception choice Gordon offered, or
corrects a choice later. Repeated purchases raise *inferred* product
confidence in the resolver but never create a row here -- that is the
line between INFERENCE and HUMAN_DECLARED, and it is why this is not
`preferred_products`.

Phase 2a wires three real Telegram acts to `note_interaction`:
a disambiguation tap (explicit_statement), "שנה" after a tap
(later_correction, negative), and a spoken replacement
(accepted_substitution for the new product, later_correction for the
old). `note_interaction` never raises: a bookkeeping failure must not
change what the household sees. `autoresolve` deliberately does not
write here -- that is inference.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

KINDS = ("explicit_statement", "accepted_substitution", "kept_exception_choice", "later_correction")
POSITIVE, NEGATIVE = "positive", "negative"
# A later correction names the product that was *wrong*.
_DEFAULT_POLARITY = {"later_correction": NEGATIVE}


def record_human_confirmation(storage, term: str, product_code: str, store: str, kind: str,
                              product_name: str = "", confirmed_by: str = "", note: str = "",
                              polarity: str | None = None) -> int:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")
    polarity = polarity or _DEFAULT_POLARITY.get(kind, POSITIVE)
    if polarity not in (POSITIVE, NEGATIVE):
        raise ValueError(f"polarity must be positive or negative, not {polarity!r}")
    if not term or not product_code or not store:
        raise ValueError("term, product_code and store are all required")
    return storage.add_vnext_product_confirmation(
        store=store, term=term, product_code=product_code, product_name=product_name,
        kind=kind, confirmed_by=confirmed_by, note=note, polarity=polarity,
    )


def note_interaction(storage, term: str, product_code: str, store: str, kind: str,
                     product_name: str = "", confirmed_by: str = "", note: str = "",
                     polarity: str | None = None) -> int | None:
    """`record_human_confirmation` that cannot break the caller.

    Returns the row id, or None when nothing was written (missing
    identity, or any storage error -- logged, never raised).
    """
    try:
        return record_human_confirmation(storage, term, product_code, store, kind,
                                         product_name=product_name, confirmed_by=confirmed_by,
                                         note=note, polarity=polarity)
    except Exception:  # noqa: BLE001
        logger.warning("vNext confirmation not recorded (%s %r@%s %s)", kind, term, store,
                       product_code, exc_info=True)
        return None


def confirmations_for(storage, term: str, store: str | None = None) -> list[dict]:
    from .storage import normalize_term

    key = normalize_term(term)
    return [r for r in storage.list_vnext_product_confirmations(store) if r.get("term") == key]
