"""Remove specific lines from a real store cart, by product code.

    python scripts/remove_cart_lines.py shufersal P_123 P_456 ...

Removal only -- reads the cart before and after, names every line it
touched, never adds, never goes near checkout. Exists because a session
that may edit code may still not be allowed to mutate the household's
cart itself; the household runs this.
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grocery_bot.config import Config
from grocery_bot.telegram_bot import _build_adapter_factories


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    store, codes = argv[0], argv[1:]
    logging.basicConfig(level=logging.WARNING)
    factories = _build_adapter_factories(Config.from_env())
    if store not in factories:
        print(f"unknown/disabled store {store!r}; enabled: {sorted(factories)}")
        return 2
    with factories[store]() as adapter:
        remover = getattr(adapter, "remove_item", None)
        if remover is None:
            print(f"{store} adapter has no remove_item")
            return 2
        before = {i["code"]: i["name"] for i in (adapter.cart_summary().get("items") or []) if i.get("code")}
        for code in codes:
            name = before.get(code, "?")
            if code not in before:
                print(f"skip   {code} -- not in cart")
                continue
            ok = remover(code)
            print(f"{'removed' if ok else 'FAILED '} {code}  {name}")
        after = adapter.cart_summary()
        items = after.get("items") or []
        print(f"\nafter: ok={after.get('ok')} lines={len(items)} total={after.get('total')}")
        for it in items:
            print("  -", it.get("name"), it.get("code"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
