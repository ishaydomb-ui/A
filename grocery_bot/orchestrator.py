"""Runs one order cycle: base list + pending ad-hoc requests -> real cart(s).

No approval gate by design (per project goals): every item that resolves to
a single clear match gets added straight away. Only genuine ambiguity
(multiple plausible matches) is surfaced back to the user, and only for
that one item — never as a blanket "confirm this whole cart" step.
"""
from __future__ import annotations

from typing import Callable

from .adapters.base import StoreAdapter
from .models import CartAddResult, OrderCycleReport
from .storage import Storage

AdapterFactory = Callable[[], StoreAdapter]


def run_order_cycle(storage: Storage, adapter_factories: dict[str, AdapterFactory]) -> dict[str, OrderCycleReport]:
    """Run the cycle against every enabled store.

    Returns one OrderCycleReport per store. Ambiguous results are also
    persisted via storage.save_pending_ambiguity so the bot can present
    them as follow-up questions after this function returns.
    """
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

            for base_item in base_items:
                term = base_item.search_term_for(store)
                result = _add_one(storage, adapter, store, term, base_item.default_quantity)
                report.record(result)

            for adhoc in adhoc_items:
                result = _add_one(storage, adapter, store, adhoc.text, adhoc.quantity)
                report.record(result)
                if result.status in ("added", "ambiguous", "not_found"):
                    resolved_adhoc.add(adhoc.id)

        reports[store] = report

    for adhoc_id in resolved_adhoc:
        storage.mark_adhoc_consumed(adhoc_id)

    return reports


def _add_one(storage: Storage, adapter: StoreAdapter, store: str, term: str, quantity: int):
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
        # fresh search rather than reporting a spurious failure.
        if result.status != "not_found":
            return result
        storage.forget_choice(store, term)

    result = adapter.search_and_add(term, quantity)
    if result.status == "ambiguous":
        storage.save_pending_ambiguity(
            store=store,
            original_term=term,
            quantity=result.quantity,
            candidates=result.candidates,
        )
    return result


def format_report_summary(reports: dict[str, OrderCycleReport]) -> str:
    """Human-readable (Hebrew) summary suitable for a Telegram message."""
    lines: list[str] = []
    for store, report in reports.items():
        lines.append(f"*{store}*")
        if report.added:
            lines.append(f"✅ נוספו ({len(report.added)}): " + ", ".join(r.item_name for r in report.added))
        if report.ambiguous:
            lines.append(
                f"❓ דורש בחירה ({len(report.ambiguous)}): " + ", ".join(r.item_name for r in report.ambiguous)
            )
        if report.not_found:
            lines.append(
                f"⚠️ לא נמצא ({len(report.not_found)}): " + ", ".join(r.item_name for r in report.not_found)
            )
        if report.errors:
            lines.append(f"🛑 שגיאה ({len(report.errors)}): " + ", ".join(r.item_name for r in report.errors))
        lines.append("")
    return "\n".join(lines).strip()
