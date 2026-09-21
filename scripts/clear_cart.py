"""Empty a real store cart, line by line, and verify by re-reading it.

    python scripts/clear_cart.py shufersal
    python scripts/clear_cart.py tivtaam

Removal only. Never touches checkout. Shufersal lines are removed by
product code, Tiv Taam lines by exact name (its panel carries no code).
Prints every line it removed and the cart as read afterwards.
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grocery_bot.config import Config
from grocery_bot.telegram_bot import _build_adapter_factories


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    store = argv[0]
    logging.basicConfig(level=logging.WARNING)
    factories = _build_adapter_factories(Config.from_env())
    if store not in factories:
        print(f"unknown/disabled store {store!r}; enabled: {sorted(factories)}")
        return 2
    with factories[store]() as adapter:
        summary = adapter.cart_summary()
        items = summary.get("items") or []
        print(f"before: ok={summary.get('ok')} lines={len(items)} total={summary.get('total')}")
        if not summary.get("ok"):
            print("cart not readable; not touching it")
            return 1
        removed = failed = 0
        for item in items:
            key = item.get("code") if store == "shufersal" else item.get("name")
            if not key:
                continue
            ok = adapter.remove_item(key)
            removed += ok
            failed += not ok
            print(f"{'removed' if ok else 'FAILED '} {item.get('name')}")
        after = adapter.cart_summary()
        left = after.get("items") or []
        print(f"\nafter: ok={after.get('ok')} lines={len(left)} total={after.get('total')} "
              f"(removed {removed}, failed {failed})")
        for it in left:
            print("  still there:", it.get("name"))
    return 0 if not left else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
