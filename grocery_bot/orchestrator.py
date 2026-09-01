"""Runs one order cycle: base list + pending ad-hoc requests -> real cart(s).

No approval gate by design (per project goals): every item that resolves to
a single clear match gets added straight away. Only genuine ambiguity
(multiple plausible matches) is surfaced back to the user, and only for
that one item — never as a blanket "confirm this whole cart" step.
"""
from __future__ import annotations

import logging
from typing import Callable

from .adapters.base import StoreAdapter
from .disambiguate import resolve
from .models import CartAddResult, OrderCycleReport
from .storage import Storage

logger = logging.getLogger(__name__)

AdapterFactory = Callable[[], StoreAdapter]


def run_order_cycle(
    storage: Storage,
    adapter_factories: dict[str, AdapterFactory],
    on_progress=None,
) -> dict[str, OrderCycleReport]:
    """Run the cycle against every enabled store.

    Returns one OrderCycleReport per store. Ambiguous results are also
    persisted via storage.save_pending_ambiguity so the bot can present
    them as follow-up questions after this function returns.

    `on_progress(done, total, result)` is called after each item, so a
    caller can show progress. A full cycle is minutes of page loads, and
    without this the user watches an idle chat and cannot tell a slow run
    from a stuck one. It is called from this worker thread, so a caller
    on an event loop must marshal back to it; anything it raises is
    swallowed rather than killing a shopping run over a UI update.
    """
    def _progress(done: int, total: int, result) -> None:
        if on_progress is None:
            return
        try:
            on_progress(done, total, result)
        except Exception:
            logger.exception("Progress callback failed; continuing the cycle")

    base_items = storage.list_active_base_items()
    adhoc_items = storage.list_pending_adhoc()

    reports: dict[str, OrderCycleReport] = {}
    # An ad-hoc request is only used up once some store actually managed
    # something with it. A transient failure (network blip, changed
    # markup) must not silently delete a request nobody will think to
    # re-send.
    resolved_adhoc: set[int] = set()

    for store, make_adapter in adapter_factories.items():
        report = OrderCycleReport(store=store)
        with make_adapter() as adapter:
            # Renew an expired session before spending a whole cycle on it.
            # Without this every item fails with a redirect-to-login that
            # looks like broken selectors, and the user gets asked to log in
            # again -- exactly the manual dependency the project rules out.
            ensure = getattr(adapter, "ensure_session", None)
            if ensure is not None and not ensure():
                report.record(
                    CartAddResult(
                        item_name="(session)",
                        store=store,
                        status="error",
                        detail="Session expired and could not be renewed automatically.",
                    )
                )
                reports[store] = report
                continue

            prematched = _prefetch_matches(
                adapter, storage, store,
                [b.search_term_for(store) for b in base_items] + [a.text for a in adhoc_items],
            )
            total_items = len(base_items) + len(adhoc_items)
            done = 0

            for base_item in base_items:
                term = base_item.search_term_for(store)
                result = _add_one(
                    storage, adapter, store, term, base_item.default_quantity, prematched
                )
                # Carry the weight through so the cart view can say "0.5 ק"ג"
                # rather than a meaningless "×1" for loose produce.
                if getattr(base_item, "amount", None):
                    result.amount = base_item.amount
                    result.unit = base_item.unit
                report.record(result)
                done += 1
                _progress(done, total_items, result)

            for adhoc in adhoc_items:
                result = _add_one(
                    storage, adapter, store, adhoc.text, adhoc.quantity, prematched
                )
                result.requested_by = adhoc.requested_by
                report.record(result)
                # Only a request that actually reached the cart is done
                # with. "not_found" used to count as resolved, which meant
                # the household was told "נוסף לרשימה", the cycle quietly
                # failed to find it, and the request vanished from both the
                # list and the cart with nobody told. A request that was not
                # bought stays pending so the next cycle tries again.
                # "ambiguous" is kept because a question was actually put to
                # the user, and answering it is what consumes the request.
                if result.status in ("added", "ambiguous"):
                    resolved_adhoc.add(adhoc.id)
                done += 1
                _progress(done, total_items, result)

        reports[store] = report

    for adhoc_id in resolved_adhoc:
        storage.mark_adhoc_consumed(adhoc_id)

    return reports


def add_terms_to_cart(
    storage: Storage,
    adapter_factories: dict[str, AdapterFactory],
    terms: list[tuple[str, int]],
    on_progress=None,
) -> dict[str, OrderCycleReport]:
    """Put specific items straight into the real cart.

    Distinct from a full cycle on purpose. "תעדכן את העגלה עם קוטג
    וגבינה" names the things to add; running the whole standing list
    would drop another dozen products into the cart the user never asked
    for in that message.
    """
    reports: dict[str, OrderCycleReport] = {}
    for store, make_adapter in adapter_factories.items():
        report = OrderCycleReport(store=store)
        with make_adapter() as adapter:
            ensure = getattr(adapter, "ensure_session", None)
            if ensure is not None and not ensure():
                report.record(
                    CartAddResult(
                        item_name="(session)",
                        store=store,
                        status="error",
                        detail="Session expired and could not be renewed automatically.",
                    )
                )
                reports[store] = report
                continue

            prematched = _prefetch_matches(adapter, storage, store, [t for t, _ in terms])
            for index, (term, quantity) in enumerate(terms, start=1):
                result = _add_one(storage, adapter, store, term, quantity, prematched)
                report.record(result)
                if on_progress is not None:
                    try:
                        on_progress(index, len(terms), result)
                    except Exception:
                        logger.exception("Progress callback failed; continuing")
        reports[store] = report
    return reports


def _prefetch_matches(adapter, storage, store: str, terms: list[str]) -> dict:
    """Resolve every term up front with the store's bulk matcher.

    Turns a cycle from one page load per item into a single call: 89
    items took ~15 minutes of searching and now resolve in ~40 seconds.
    Terms already in product memory are skipped — a remembered code is a
    decision the household made, and is better than any matcher's guess.

    Returns {} on any failure, which simply restores the old per-item
    path; the caller must treat this as a speed-up, never a requirement.
    """
    matcher = getattr(adapter, "bulk_match", None)
    if matcher is None:
        return {}
    unknown = [t for t in terms if storage.preferred_for(store, t) is None]
    if not unknown:
        return {}
    logger.info("Bulk-matching %d unresolved terms", len(unknown))
    return matcher(unknown)


def _add_one(
    storage: Storage,
    adapter: StoreAdapter,
    store: str,
    term: str,
    quantity: int,
    prematched: dict | None = None,
):
    """Add one term, honouring a previously remembered product choice.

    Without the memory lookup this bot is unusable in practice: a real
    Shufersal search for an everyday term returns ~20 tiles, so every
    single item comes back "ambiguous" and the user is asked a dozen
    questions per cycle. Remembering the choice turns that into a
    one-time question per product, which is what the project's "focused
    decision point, only on genuine ambiguity" rule actually asks for.
    """
    preferred = storage.preferred_for(store, term)
    if preferred is not None:
        result = adapter.add_specific_product(
            preferred["product_name"],
            quantity,
            product_code=preferred["product_code"],
            search_term=term,
        )
        # A remembered product can be delisted or renamed; fall back to a
        # fresh search rather than reporting a spurious failure. The
        # memory is deliberately *kept*: one failed lookup usually means
        # the store's search didn't surface it this time, not that the
        # household changed its mind, and deleting on the first miss
        # silently threw away choices that took a real conversation to
        # establish.
        if result.status != "not_found":
            return result
        logger.info(
            "Shufersal: remembered product for %r not found this run; keeping the memory", term
        )

    # A bulk-matched hit skips the search entirely: we already have the
    # product code, so this is a direct add rather than a page load.
    hit = (prematched or {}).get(term)
    if hit and hit.get("code"):
        if not hit.get("in_stock", True):
            logger.info("Skipping %r — matched product is out of stock", term)
            return CartAddResult(
                item_name=term, store=store, status="not_found",
                detail="אזל מהמלאי", quantity=quantity,
            )
        result = adapter.add_specific_product(
            hit.get("name") or term, quantity,
            product_code=hit["code"], search_term=term,
        )
        if result.status == "added":
            storage.remember_choice(
                store=store, term=term,
                product_code=hit["code"], product_name=hit.get("name") or term,
            )
            result.auto_resolved = "bulk_match"
            return result

    result = adapter.search_and_add(term, quantity)
    if result.status != "ambiguous":
        return result

    # Before asking, check whether the answer is already known. Most
    # "ambiguity" is a search returning twenty tiles, of which exactly one
    # is a product this household has bought for years.
    cards = getattr(result, "candidate_cards", None)
    if cards:
        known = _known_products(storage, store)
        decision = resolve(term, cards, known["names"], known["codes"])
        if decision.resolved:
            chosen = decision.card
            picked = adapter.add_specific_product(
                chosen.get("name", term),
                quantity,
                product_code=chosen.get("code", ""),
                search_term=term,
            )
            if picked.status == "added":
                storage.remember_choice(
                    store=store,
                    term=term,
                    product_code=chosen.get("code", ""),
                    product_name=chosen.get("name", term),
                )
                picked.auto_resolved = decision.reason
                return picked

    storage.save_pending_ambiguity(
        store=store,
        original_term=term,
        quantity=result.quantity,
        candidates=result.candidates,
        candidate_cards=cards or [],
    )
    return result


def _known_products(storage: Storage, store: str) -> dict:
    """Every product this household has actually bought, by name and code."""
    preferences = storage.list_preferences(store)
    return {
        "names": {p["product_name"] for p in preferences if p.get("product_name")},
        "codes": {p["product_code"] for p in preferences if p.get("product_code")},
    }


def format_report_summary(reports: dict[str, OrderCycleReport]) -> str:
    """Human-readable (Hebrew) summary suitable for a Telegram message."""
    lines: list[str] = []
    for store, report in reports.items():
        lines.append(f"*{store}*")
        if report.added:
            lines.append(f"✅ נוספו ({len(report.added)}): " + ", ".join(r.item_name for r in report.added))
            # An automatic pick must be visible: it replaced a question the
            # user would otherwise have answered, so they need to be able to
            # spot a wrong one.
            auto = [r for r in report.added if getattr(r, "auto_resolved", "")]
            if auto:
                lines.append(
                    f"   _נבחרו לפי הרגלי הקנייה שלכם ({len(auto)}): _"
                    + ", ".join(r.item_name for r in auto)
                )
        if report.ambiguous:
            lines.append(
                f"❓ דורש בחירה ({len(report.ambiguous)}): " + ", ".join(r.item_name for r in report.ambiguous)
            )
        if report.not_found:
            # Say it stays on the list. Otherwise a long report reads as
            # "these are gone", and the household has no way to know the
            # request is still queued for the next cycle.
            lines.append(
                f"⚠️ לא נמצא ({len(report.not_found)}): "
                + ", ".join(r.item_name for r in report.not_found)
                + "\n   _נשאר ברשימה — אנסה שוב בפעם הבאה._"
            )
        if report.errors:
            lines.append(f"🛑 שגיאה ({len(report.errors)}): " + ", ".join(r.item_name for r in report.errors))
        lines.append("")
    return "\n".join(lines).strip()
