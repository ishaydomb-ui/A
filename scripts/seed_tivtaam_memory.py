"""Seed Tiv Taam's product memory from its own order history.

Shufersal has 309 remembered choices and Tiv Taam had **zero**, despite
743 rows of real Tiv Taam order history sitting in `store_prices`. The
history import that built Shufersal's memory scrapes that chain's order
pages (`history.ORDERS_URL` is hardcoded to it), so Tiv Taam was simply
never seeded — not a design decision, a gap.

It needs no scraping: those 743 rows came from real orders
(`source='order'`), so the household's actual Tiv Taam products are
already on disk. This turns each into a remembered choice, which is what
lets `orchestrator._add_one` resolve a product directly instead of going
through Tiv Taam's autocomplete — the dropdown that returned 4, then 0,
then 5, then 1 candidate for one query in a single afternoon.

`remember_choice` is INSERT OR REPLACE, so re-running is safe and simply
refreshes. `--dry-run` reports without writing.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from grocery_bot.storage import Storage

STORE = "tivtaam"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--store", default=STORE)
    args = parser.parse_args()

    storage = Storage(os.environ.get("GROCERY_BOT_DB_PATH", "data/grocery_bot.sqlite3"))

    # Latest row per barcode: a product bought repeatedly should be
    # remembered under the name it carried most recently, not its oldest.
    latest = storage.latest_store_prices(args.store)
    if not latest:
        print(f"no store_prices rows for {args.store!r} — nothing to seed")
        return 1

    seeded, skipped = 0, 0
    for barcode, row in sorted(latest.items()):
        name = (row.get("name") or "").strip()
        if not name:
            skipped += 1
            continue
        if args.dry_run:
            seeded += 1
            continue
        storage.remember_choice(
            store=args.store,
            term=name,
            product_code=str(barcode),
            product_name=name,
        )
        seeded += 1

    verb = "would seed" if args.dry_run else "seeded"
    print(f"{verb} {seeded} remembered choices for {args.store}"
          + (f" ({skipped} skipped: no name)" if skipped else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
