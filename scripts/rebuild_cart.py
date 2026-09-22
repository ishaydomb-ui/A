"""Rebuild one chain's cart on explicit request: the regular refill list
plus named extras, skipping what is already in the cart.

    python scripts/rebuild_cart.py shufersal ["extra term" ...]

Runs the same engine as /start_order (orchestrator.add_terms_to_cart)
with the removal guard OFF -- this exists for the case where the
household asks for the cart to be built again, so "you deleted this
since the last fill" must not block it. Presence is still respected:
lines already in the cart are not added twice. Never touches checkout.
Prints the per-store report and the cart afterwards.
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grocery_bot import standingcart
from grocery_bot.config import Config
from grocery_bot.models import PlanTerm
from grocery_bot.orchestrator import add_terms_to_cart
from grocery_bot.storage import Storage
from grocery_bot.telegram_bot import _build_adapter_factories


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    store, extras = argv[0], argv[1:]
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    config = Config.from_env()
    storage = Storage(config.db_path)
    factories = _build_adapter_factories(config)
    if store not in factories:
        print(f"unknown/disabled store {store!r}")
        return 2
    factory = factories[store]

    with factory() as adapter:
        present = adapter.cart_summary().get("items") or []
    present_names = {(i.get("name") or "").strip() for i in present}
    print(f"cart before: {len(present)} lines")

    plan = standingcart.plan_refill(storage, store)
    terms = [t for t in plan.terms if t.term.strip() not in present_names]
    terms += [PlanTerm(t, 1, "manual", f"extra{i}") for i, t in enumerate(extras)]
    print(f"regular list: {len(plan.terms)} terms, {len(terms) - len(extras)} not yet in cart; extras: {extras}")

    reports = add_terms_to_cart(storage, {store: factory}, terms, guard_cart=False, trigger="manual")
    r = reports.get(store)
    if r is not None:
        print(f"added {len(r.added)} · ambiguous {len(r.ambiguous)} · not found {len(r.not_found)} · errors {len(r.errors)} · skipped {len(getattr(r, 'skipped', []) or [])}")
        for x in r.not_found:
            print("  not found:", x.item_name)
        for x in r.ambiguous:
            print("  ambiguous:", x.item_name)
    with factory() as adapter:
        after = adapter.cart_summary()
        print(f"cart after: {len(after.get('items') or [])} lines, total {after.get('total')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
