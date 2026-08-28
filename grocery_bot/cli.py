"""Small command line for the jobs that shouldn't need the bot running.

Run with: python -m grocery_bot.cli <command>

    refresh-prices        re-download the branch price/promo snapshot
    price <query>         look up an item (same answer the bot gives)
    deals                 promotions on the standing list
    import-base-list <f>  load a YAML base list into the database

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

    return _usage()


if __name__ == "__main__":
    raise SystemExit(main())
