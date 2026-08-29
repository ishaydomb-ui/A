"""Small command line for the jobs that shouldn't need the bot running.

Run with: python -m grocery_bot.cli <command>

    refresh-prices        re-download the branch price/promo snapshot
    price <query>         look up an item (same answer the bot gives)
    deals                 promotions on the standing list
    import-base-list <f>  load a YAML base list into the database
    import-history        build the base list from real past orders
                          [--year N] [--min-share F] [--memory-only] [--dry-run]

`refresh-prices` is the one meant for a scheduler — the feed publishes a
new full snapshot a few times a day, and a stale catalog quietly gives
wrong prices rather than failing loudly.
"""
from __future__ import annotations

import logging
import sys

from .catalog import (
    find_deals_for_base_list,
    format_deals_report,
    format_search_answer,
    refresh_catalog,
)
from .config import Config
from .storage import Storage


def _usage() -> int:
    print(__doc__)
    return 2


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        return _usage()

    command, rest = args[0], args[1:]
    config = Config.from_env()
    storage = Storage(config.db_path)

    if command == "refresh-prices":
        meta = refresh_catalog(storage, config.shufersal_price_store_id)
        print(
            f"branch {meta.get('branch')}: {meta.get('product_count')} products "
            f"from {meta.get('price_file')}"
        )
        return 0

    if command == "price":
        if not rest:
            return _usage()
        query = " ".join(rest)
        print(format_search_answer(query, storage.search_with_deals(query, limit=8)))
        return 0

    if command == "deals":
        items = storage.list_active_base_items()
        print(format_deals_report(find_deals_for_base_list(storage, items)))
        return 0

    if command == "import-base-list":
        if not rest:
            return _usage()
        count = storage.import_base_list_from_yaml(rest[0])
        print(f"imported {count} items from {rest[0]}")
        return 0

    if command == "import-history":
        return _import_history(config, storage, rest)

    return _usage()


def _import_history(config: Config, storage: Storage, args: list[str]) -> int:
    """Rebuild the base list and product memory from real past orders.

    Needs a logged-in session and the Israeli exit, so it borrows the
    Shufersal adapter rather than opening its own browser.
    """
    from .adapters.shufersal import ShufersalAdapter
    from .history import fetch_order_history, import_base_list, seed_product_memory, summarise

    year = _int_flag(args, "--year")
    min_share = _float_flag(args, "--min-share") or 0.5
    dry_run = "--dry-run" in args
    memory_only = "--memory-only" in args

    adapter = ShufersalAdapter(
        config.shufersal_storage_state_path,
        headless=config.headless,
        proxy=config.playwright_proxy,
        username=config.shufersal_username,
        password=config.shufersal_password,
    )
    try:
        if not adapter.ensure_session():
            print("Could not get a valid Shufersal session.")
            return 1
        orders = fetch_order_history(adapter._page, year=year)
        history = summarise(orders)
    finally:
        adapter.close()

    print(f"analysed {history.orders_analysed} orders, {len(history.products)} distinct products")
    if history.orders_analysed == 0:
        print("No orders matched — nothing to import.")
        return 1

    selected = history.frequent(min_share)
    for item in selected:
        amount, unit = item.amount_and_unit
        extra = f"{amount} {unit}" if amount else ""
        print(f"  {item.share * 100:3.0f}%  {item.name}  x{item.default_quantity} {extra}")

    if dry_run:
        print(f"\n--dry-run: would import {len(selected)} base items; nothing written.")
        return 0

    seeded = seed_product_memory(storage, history)
    print(f"\nremembered {seeded} product choices")
    if not memory_only:
        count = import_base_list(storage, history, min_share=min_share)
        print(f"imported {count} base-list items (bought in {min_share * 100:.0f}%+ of orders)")
    return 0


def _int_flag(args: list[str], name: str) -> int | None:
    value = _flag(args, name)
    return int(value) if value is not None else None


def _float_flag(args: list[str], name: str) -> float | None:
    value = _flag(args, name)
    return float(value) if value is not None else None


def _flag(args: list[str], name: str) -> str | None:
    if name in args:
        index = args.index(name)
        if index + 1 < len(args):
            return args[index + 1]
    return None


if __name__ == "__main__":
    raise SystemExit(main())
