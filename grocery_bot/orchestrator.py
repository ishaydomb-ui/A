"""Runs one order cycle: base list + pending ad-hoc requests -> real cart(s).

No approval gate by design (per project goals): every item that resolves to
a single clear match gets added straight away. Only genuine ambiguity
(multiple plausible matches) is surfaced back to the user, and only for
that one item — never as a blanket "confirm this whole cart" step.
"""
from __future__ import annotations

from typing import Callable

from .adapters.base import StoreAdapter
from .models import OrderCycleReport
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

    for store, make_adapter in adapter_factories.items():
        report = OrderCycleReport(store=store)
        with make_adapter() as adapter:
            for base_item in base_items:
                term = base_item.search_term_for(store)
                result = adapter.search_and_add(term, base_item.default_quantity)
                report.record(result)
                if result.status == "ambiguous":
                    storage.save_pending_ambiguity(
                        store=store,
                        original_term=term,
                        quantity=result.quantity,
                        candidates=result.candidates,
                    )

            for adhoc in adhoc_items:
                result = adapter.search_and_add(adhoc.text, adhoc.quantity)
                report.record(result)
                if result.status == "ambiguous":
                    storage.save_pending_ambiguity(
                        store=store,
                        original_term=adhoc.text,
                        quantity=result.quantity,
                        candidates=result.candidates,
                    )

        reports[store] = report

    # An ad-hoc request is "consumed" once it's been attempted against every
    # enabled store, regardless of per-store outcome — an ambiguous or
    # not-found result is handled through the pending-ambiguity / not-found
    # summary, not by leaving the request to be retried verbatim next cycle.
    for adhoc in adhoc_items:
        storage.mark_adhoc_consumed(adhoc.id)

    return reports


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
