"""Small command line for the jobs that shouldn't need the bot running.

Run with: python -m grocery_bot.cli <command>

    refresh-prices        re-download the branch price/promo snapshot
    price <query>         look up an item (same answer the bot gives)
    deals                 promotions on the standing list
    import-base-list <f>  load a YAML base list into the database
    import-history        build the base list from real past orders
                          [--year N] [--min-share F] [--memory-only] [--dry-run]
    build-stock           derive proposable products, tiers and departments
    add-item <text>       add to the shopping list [--by NAME] [--qty N]
                          (the integration point for the household's other bot)
    remove-item <text>    remove one item by fuzzy name ("תוריד חלב מהרשימה")
    list-items            print the pending shopping list, one per line

`refresh-prices` is the one meant for a scheduler — the feed publishes a
new full snapshot a few times a day, and a stale catalog quietly gives
wrong prices rather than failing loudly.
"""
from __future__ import annotations

import logging
import os
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

    # Commands that only touch the local database are dispatched before
    # Config.from_env(), which demands TELEGRAM_BOT_TOKEN. The household's
    # other bot calls `add-item` to put groceries on the list, and making
    # that require this bot's Telegram secret would force an unrelated
    # project to hold a credential it has no use for — a boundary worth
    # keeping clean. These need nothing but GROCERY_BOT_DB_PATH.
    if command in _DB_ONLY_COMMANDS:
        storage = Storage(os.environ.get("GROCERY_BOT_DB_PATH", "data/grocery_bot.sqlite3"))
        return _DB_ONLY_COMMANDS[command](storage, rest)

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

    if command == "build-stock":
        return _build_stock(config, storage, rest)

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


def _build_stock(config: Config, storage: Storage, args: list[str]) -> int:
    """Derive the proposable product set from real order history."""
    from .adapters.shufersal import ShufersalAdapter
    from .history import fetch_order_history
    from .stock import build_from_orders, group_by_department

    store = (config.enabled_stores or ["shufersal"])[0]
    adapter = ShufersalAdapter(
        config.shufersal_storage_state_path,
        headless=config.headless,
        proxy=config.playwright_proxy,
        username=config.shufersal_username,
        password=config.shufersal_password,
    )
    try:
        if not adapter.ensure_session():
            print("Could not get a valid session.")
            return 1
        orders = fetch_order_history(adapter._page, year=_int_flag(args, "--year"))
    finally:
        adapter.close()

    items = build_from_orders(orders)
    storage.replace_stock_items(store, items)
    departments = group_by_department(items)
    proposed = sum(len(d.items) for d in departments)
    print(f"{len(orders)} orders -> {len(items)} products, {proposed} worth proposing")
    for department in departments:
        print(f"  {department.name}: {len(department.items)}")
    return 0


def _add_item(storage: Storage, args: list[str]) -> int:
    """Add one item to the shopping list.

    This is the seam for the household's *other* Telegram bot (Family OS,
    which the two partners already share): it lets the second person add
    groceries from the assistant they are already talking to, without
    that project needing to know this schema, this venv, or the dedupe
    rules. Keep the contract stable -- text in, one line out, exit 0.

    Deliberately a CLI rather than an importable module: each project has
    its own virtualenv, so a shared process boundary would couple their
    dependency trees. A subprocess call has neither problem.
    """
    # Skip flag values as well as the flags: "--by לירן" must not leave
    # "לירן" in the product name.
    words, skip = [], False
    for argument in args:
        if skip:
            skip = False
            continue
        if argument.startswith("--"):
            skip = "=" not in argument
            continue
        words.append(argument)
    if not words:
        print("usage: add-item <text> [--by NAME] [--qty N]")
        return 2
    text = " ".join(words).strip()
    requested_by = _flag(args, "--by") or "unknown"
    quantity = _int_flag(args, "--qty") or 1

    before = {r.id for r in storage.list_pending_adhoc()}
    request_id = storage.add_adhoc_request(
        text=text, requested_by=requested_by, quantity=quantity
    )
    # add_adhoc_request folds an exact repeat onto the pending one, so say
    # which happened rather than implying a second copy was created.
    if request_id in before:
        print(f"already on the list: {text}")
    else:
        print(f"added: {text}")
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


def _remove_item(storage: Storage, args: list[str]) -> int:
    """Remove one pending item by fuzzy name.

    An obvious follow-up to add-item once a second bot is putting items
    on this list on someone's behalf: "תוריד חלב מהרשימה" is exactly the
    kind of thing either partner will say, and there was no way to answer
    it. Reuses the same fuzzy match the Telegram bot already uses, so the
    behaviour is one implementation, not two.
    """
    text = " ".join(a for a in args if not a.startswith("--")).strip()
    if not text:
        print("usage: remove-item <text>")
        return 2
    removed = storage.remove_adhoc_by_name(text)
    if removed is None:
        print(f"not found: {text}")
        return 1
    print(f"removed: {removed}")
    return 0


def _list_items(storage: Storage, args: list[str]) -> int:
    """Print the pending shopping list, one item per line.

    Includes who asked, because the household's two people both add here
    and "someone specifically wants this" is the distinction that matters
    when reading the list back.
    """
    for request in storage.list_pending_adhoc():
        who = f"  🙋 {request.requested_by}" if request.requested_by else ""
        print(f"{request.text}{who}")
    return 0


# Commands needing only the database — no Telegram token, no network. The
# integration surface for the household's other bot.
_DB_ONLY_COMMANDS = {
    "add-item": lambda storage, args: _add_item(storage, args),
    "remove-item": _remove_item,
    "list-items": _list_items,
}


if __name__ == "__main__":
    raise SystemExit(main())
