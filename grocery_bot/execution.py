"""The execution service: what runs a cart, apart from how Telegram talks about it.

Phase 10 of the reliability build (2026-09-17). Until this module the
refill loop, the removals logging, "mark the shop done", the list
watcher's run half, the cart reads after a fill, run finishing and
crash resume all lived inside `GroceryBot` as methods on the Telegram
handler class, blocking work interleaved with `send_message` calls.
Nothing here is new behaviour: each function is the blocking half of a
handler, moved verbatim, so the handler keeps only the conversation.
Anything that talks to the household stays in telegram_bot.py.

Everything here is synchronous and meant to run in a worker thread
(`asyncio.to_thread`). A function that needs to say something mid-way
takes a plain callback; the caller decides how to marshal it onto the
event loop.
"""
from __future__ import annotations

import logging
from typing import Callable

from . import standingcart
from .chains import CART_CAPABLE
from .models import PlanTerm
from .orchestrator import add_terms_to_cart, record_deals, resume_run

logger = logging.getLogger(__name__)


# -- refill -----------------------------------------------------------------

def refill(storage, factories: dict, announce: Callable[[str, int], None] | None = None) -> dict:
    """Put the standing list, plus this week's deals, into every cart.

    `announce(store, total)` is called before each store's fill, from
    this thread. Returns the per-store reports; records the manifest and
    the deals only when something ran, exactly as the handler did.
    """
    reports: dict = {}
    for store, factory in factories.items():
        plan = standingcart.plan_refill(storage, store)
        if not plan.terms:
            continue
        if announce is not None:
            announce(store, plan.total)
        store_reports = add_terms_to_cart(
            storage, {store: factory}, plan.terms, guard_cart=True, trigger="refill",
        )
        # Tag what went in because of a promotion, so /lastdeals can
        # answer afterwards instead of the household having to ask.
        if store in store_reports:
            standingcart.tag_deal_results(store_reports[store], plan)
        reports.update(store_reports)
    if reports:
        standingcart.record_manifest(storage, reports)
        record_deals(storage, reports)
    return reports


# -- the shop is done ----------------------------------------------------------

def mark_shopped(storage, store: str) -> int:
    """Record a finished shop at `store` ("all" = every cart-capable chain).

    Returns how many in-cart requests moved to `shopped`. An empty store
    records a shop with no chain — the pre-2026-09-15 behaviour, kept
    for the caller that genuinely does not know.
    """
    if store == "all":
        for one in CART_CAPABLE:
            standingcart.mark_shopped(storage, store=one)
        return storage.advance_adhoc_status("in_cart", "shopped", "")
    standingcart.mark_shopped(storage, store=store)
    return storage.advance_adhoc_status("in_cart", "shopped", store)


# -- what the household removed --------------------------------------------------

def log_removals_from_orders(storage) -> dict:
    """Compare each shopped cart against the order that emptied it.

    The reliable half, and it runs nightly rather than at `/done`
    because that is when the evidence exists: the order surfaces in the
    chain's own history hours after checkout — Tiv Taam the same day,
    Shufersal ~36 — long after the cart went empty.
    """
    from . import tivtaamhistory

    logged: dict = {}
    for store in standingcart.pending_snapshot_stores(storage):
        try:
            if store == "tivtaam":
                from .adapters.tivtaam_api import TivTaamApi, TivTaamSession

                api = TivTaamApi(TivTaamSession.from_storage_state())
                orders = tivtaamhistory.fetch_orders(api, size=5)
                if not orders:
                    continue
                newest = orders[0]
                lines = tivtaamhistory.order_lines(api.order(int(newest["code"])))
                placed = newest["placed_at"]
            else:
                # Shufersal's line items need a logged-in browser page,
                # which this pass does not hold. The snapshot simply
                # waits, and waiting is correct — not wrong, only not
                # yet answered.
                continue

            gone = standingcart.removals_from_order(storage, store, lines, placed)
            if gone:
                standingcart.log_removals(storage, store, gone)
                logged[store] = len(gone)
            # Cleared either way: the order for this shop has been seen,
            # so the snapshot has served its purpose. Keeping it would
            # compare the next order against a stale cart.
            standingcart.clear_shopped_snapshot(storage, store)
        except Exception:
            logger.exception("Could not read %s's order for removals", store)
    return logged


def log_removals_from_carts(storage, factories: dict) -> dict:
    """Record what the household deleted from each live cart this shop.

    The pre-checkout case: `/done` fired but the cart was not actually
    emptied. After a real shop this finds nothing, by design, and
    `log_removals_from_orders` answers instead.
    """
    logged: dict = {}
    for store, factory in factories.items():
        try:
            with factory() as adapter:
                if not adapter.ensure_session():
                    items = []
                else:
                    items = adapter.cart_summary().get("items") or []
        except Exception:
            logger.exception("Could not read %s's cart for removals", store)
            items = []
        gone = standingcart.removals(storage, store, items)
        if gone:
            standingcart.log_removals(storage, store, gone)
            logged[store] = len(gone)
    return logged


# -- the list watcher's run half ---------------------------------------------------

def run_list_items(storage, factories: dict, items) -> tuple[int | None, dict, dict, list[int]]:
    """Put pending list items into the carts under one run; consume what landed.

    Returns (run_id, reports, outcomes, consumed request ids). Each
    request keeps its own id into the run, so "was it bought" is read
    from the run item and never guessed by comparing the request text to
    the added product's name. Only `verified` consumes a request: an add
    that was sent but not confirmed stays on the list and is
    presence-checked next time, never blindly re-added.

    A failure inside the fill closes the run as `aborted` and re-raises;
    the caller says so to the household.
    """
    terms = [PlanTerm(item.text, item.quantity or 1, "adhoc", str(item.id))
             for item in items if item.text]
    if not terms:
        return None, {}, {}, []
    run_id = storage.start_cart_run("watch_list")
    try:
        reports = add_terms_to_cart(storage, factories, terms, guard_cart=True, run_id=run_id)
    except Exception:
        storage.finish_cart_run(run_id, "aborted")
        raise
    outcomes = storage.run_outcomes(run_id)
    consumed = [item.id for item in items
                if outcomes.get(("adhoc", str(item.id))) == "verified"]
    for request_id in consumed:
        storage.mark_adhoc_consumed(request_id)
    finish_run(storage, run_id, outcomes)
    return run_id, reports, outcomes, consumed


# -- run lifecycle ------------------------------------------------------------------

def finish_run(storage, run_id: int, outcomes: dict | None = None) -> str:
    """Close a run with the status its items earn. Returns the status.

    Same rule as the orchestrator's own close (interrupted while items
    are pending, else completed), plus the Phase 11 outcome state.
    """
    from .orchestrator import _finish_run

    return _finish_run(storage, run_id)


def read_carts(factories: dict) -> dict:
    """Each chain's authoritative cart reading, where it can be read.

    One entry per chain, missing where the adapter has no `cart_summary`
    or the store was unreachable — the renderer falls back to the
    shelf-price estimate and says so.
    """
    carts: dict = {}
    for store, make_adapter in factories.items():
        try:
            with make_adapter() as adapter:
                reader = getattr(adapter, "cart_summary", None)
                if reader is not None:
                    carts[store] = reader()
        except Exception:
            logger.exception("Could not read the %s cart for the final view", store)
    return carts


# -- recovery -------------------------------------------------------------------------

def interrupt_open_runs(storage) -> list[int]:
    """Mark every run a previous process left `running` as interrupted."""
    ids = [run["id"] for run in storage.running_cart_runs()]
    for run_id in ids:
        storage.mark_cart_run(run_id, "interrupted")
    return ids


def resume_interrupted(storage, factories: dict, proxy: str | None) -> list[tuple[dict, dict | None]]:
    """Resume each open run under its own id. (run, reports-or-None) per run.

    None means the resume itself raised; the run is left `interrupted`
    for the next start, never silently dropped.
    """
    results: list = []
    for run in storage.running_cart_runs():
        run_id = run["id"]
        logger.warning("RESUME run=%s trigger=%s left %s by a previous process; resuming",
                       run_id, run["trigger"], run["status"])
        storage.mark_cart_run(run_id, "interrupted")
        try:
            reports = resume_run(storage, factories, run_id, proxy)
        except Exception:
            logger.exception("RESUME run=%s failed", run_id)
            storage.mark_cart_run(run_id, "interrupted")
            reports = None
        results.append((dict(run), reports))
    return results
