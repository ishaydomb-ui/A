"""Deterministic product identity, resolved before any browser action.

Phase 6 of the reliability build (2026-09-17). The measurements it rests
on, all from the live database:

- 340 of 377 Tiv Taam purchased products (90%, 89% purchase-weighted)
  carry a barcode present in Tiv Taam's own price feed. **83% of the
  standing plan, 84% of a typical basket**, resolves by feed barcode with
  no name search at all.
- 293 of 378 are 13-digit EAN; **63 are 7-digit** retailer-internal
  produce codes (פלפל אדום = 9913002). Those are chain-local and never
  cross chains.
- The same EAN appears at Politzer (174), Fresh Market (121), Rami Levy
  (89), Osher Ad (80), Keshet (58) — cross-chain identity for the
  household's own products, already in the database and never joined.
- Of cross-chain name pairs sharing an EAN, 493 agree on a content word
  and 29 do not; the disagreements are mostly naming, but a sanity check
  is cheap and one or two looked genuinely wrong.

This module answers one question — *which retailer product is this
household need?* — from evidence the bot already holds, so the browser
is used to click, not to guess. The **household need** (a run item) and
the **retailer product** (a code at one chain) stay distinct: one need
may resolve to different products at different chains, and pack
variants with different barcodes stay different products.

The name path is not deleted. It remains the fallback for the 16% with
no usable barcode, for free-text requests, and whenever this returns
None. `GORDON_IDENTITY=name` disables barcode-first entirely, for
rollback only.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Below this length a barcode is a retailer-internal PLU, valid only at
# the chain that issued it.
CHAIN_LOCAL_MAX_LEN = 7
# A global trade item number. EAN-13 is what the feeds carry; EAN-8 is
# rare here and treated as global too.
GLOBAL_LENGTHS = (8, 12, 13, 14)

MIN_WORD = 3


@dataclass(frozen=True)
class Identity:
    """A retailer product this need resolves to, and why."""

    store: str
    product_code: str
    name: str
    barcode: str = ""
    basis: str = ""  # barcode_site | barcode | stock_code | cross_chain


def enabled() -> bool:
    return os.environ.get("GORDON_IDENTITY", "barcode").strip().lower() != "name"


def is_chain_local(barcode: str) -> bool:
    b = str(barcode or "").strip()
    return bool(b) and (len(b) <= CHAIN_LOCAL_MAX_LEN or b.startswith("2"))


def is_global(barcode: str) -> bool:
    b = str(barcode or "").strip()
    return b.isdigit() and len(b) in GLOBAL_LENGTHS and not b.startswith("2")


def _words(text) -> set:
    from .storage import normalize_term

    return {w for w in normalize_term(text).split() if len(w) >= MIN_WORD}


_SITE_CACHE: dict[tuple[str, str], dict | None] = {}


def site_product(store: str, barcode: str) -> dict | None:
    """{"id", "name"} as the chain's own site shows it, or None.

    Only for Self-Point chains; one API call per barcode per process,
    misses cached too. Never raises: identity must degrade to the feed
    name, not fail the add.
    """
    from .adapters.selfpoint import RETAILERS

    if store not in RETAILERS or not str(barcode).strip():
        return None
    key = (store, str(barcode).strip())
    if key not in _SITE_CACHE:
        try:
            from .adapters.selfpoint import SelfPointPrices

            found = SelfPointPrices(store, timeout=20).prices_by_barcode([key[1]])
            _SITE_CACHE[key] = found.get(key[1])
        except Exception:  # noqa: BLE001
            logger.warning("Self-Point lookup failed for %s/%s; using the feed name", store, key[1])
            return None
    return _SITE_CACHE[key]


def resolve(storage, store: str, plan_term) -> Identity | None:
    """The retailer product for this need at this store, or None.

    Only `stock` needs carry a product code today; ad-hoc and free-form
    needs go through the remembered-choice and name paths as before.
    """
    if not enabled():
        return None
    if getattr(plan_term, "source_kind", "") != "stock":
        return None
    code = str(getattr(plan_term, "source_id", "") or "").strip()
    if not code:
        return None

    row = _stock_row(storage, store, code)
    if row is None:
        return None
    barcode = str(row.get("barcode") or "").strip()
    stock_name = (row.get("product_name") or "").strip()

    # A Self-Point chain names the product on its site differently from
    # its own price feed — measured 26.09.2026: 7 of 39 Tiv Taam stock
    # barcodes carry the same name in both. The adapter accepts only an
    # exact dropdown match, so the feed name sent almost every identity
    # add back to the free-text search it exists to avoid. The site's
    # own name comes from the Self-Point API by `localBarcode` (Basics in
    # Order §3); on any failure the feed name below is used as before.
    if barcode:
        site = site_product(store, barcode)
        if site and site.get("name"):
            return Identity(store, site.get("id") or code, site["name"], barcode, "barcode_site")

    # The feed's own row is the canonical retailer product: its exact
    # name is what the store's search will match on the first hit.
    if barcode:
        feed = None
        try:
            feed = storage.latest_store_price(store, barcode)
        except Exception:  # noqa: BLE001
            logger.exception("Feed lookup failed for %s/%s", store, barcode)
        if feed and (feed.get("name") or "").strip():
            return Identity(store, code, (feed["name"] or "").strip(), barcode, "barcode")

    # No feed row: the retailer product code alone is still a stable
    # identity at this chain (Shufersal adds by `P_` code directly).
    if stock_name:
        return Identity(store, code, stock_name, barcode, "stock_code")
    return None


def resolve_cross_chain(storage, barcode: str, source_name: str, target_store: str) -> Identity | None:
    """The same product at another chain, by global barcode, with a sanity check.

    Refuses chain-local codes outright, and refuses a match whose feed
    name shares no content word with the source name — the 29-of-522
    cases where one barcode carried two unrelated-looking names.
    """
    if not enabled() or not is_global(barcode):
        return None
    try:
        feed = storage.latest_store_price(target_store, barcode)
    except Exception:  # noqa: BLE001
        logger.exception("Cross-chain lookup failed for %s/%s", target_store, barcode)
        return None
    if not feed:
        return None
    name = (feed.get("name") or "").strip()
    if not name:
        return None
    if _words(source_name) and not (_words(source_name) & _words(name)):
        logger.info("Cross-chain identity refused: %r vs %r share no content word",
                    source_name[:30], name[:30])
        return None
    return Identity(target_store, "", name, str(barcode), "cross_chain")


def _stock_row(storage, store: str, product_code: str) -> dict | None:
    try:
        for row in storage.list_stock_items(store):
            if str(row.get("product_code") or "") == product_code:
                return dict(row)
    except Exception:  # noqa: BLE001
        logger.exception("Could not read stock items for %s", store)
    return None
