"""Small command line for the jobs that shouldn't need the bot running.

Run with: python -m grocery_bot.cli <command>

    refresh-prices        re-download the branch price/promo snapshot
    price <query>         Shufersal shelf price for an item (single chain)
    price-compare <query> where it is cheapest across every chain [--json]
    deals                 promotions on the standing list
    import-base-list <f>  load a YAML base list into the database
    import-history        build the base list from real past orders
                          [--year N] [--min-share F] [--memory-only] [--dry-run]
    build-stock           derive proposable products, tiers and departments
    add-item <text>       add to the shopping list [--by NAME] [--qty N]
                          (the integration point for the household's other bot)
    remove-item <text>    remove one item by fuzzy name ("תוריד חלב מהרשימה")
    add-to-cart <text>    put it straight into the open store cart [--qty N]
                          (falls back to the list if not found; never checks out)
    nudge                 the overdue-shop message, or nothing if not due
                          [--last-nudged YYYY-MM-DD] [--why]
    confirm-card ["<reply>"]  record that the ₪700 card was loaded this
                          month, so the question stops until next month
    list-items            print the pending shopping list, one per line
    recipe <dish>         ingredients for a dish [--by NAME] [--all] [--preview]
    recipe-text           same, from recipe text on stdin (OCR/screenshot/paste)
    meal-plan [request]   weekly menu + its ingredients [--by NAME] [--all] [--preview]
    benefits-catalog [query] [--json]  harvested benefit-club stores
                          (wallets, discount ceilings, cities; --json for
                          all rows, machine-readable)
    benefits-remember "<term>" "<merchant>"  resolve a term to one merchant
    benefits-branches [query] [--json]  street addresses for those stores
                          (partial — the branch crawl is incremental)

`refresh-prices` is the one meant for a scheduler — the feed publishes a
new full snapshot a few times a day, and a stale catalog quietly gives
wrong prices rather than failing loudly.

**What the household's other bot (מירי) can call.** Everything in
`_DB_ONLY_COMMANDS` needs nothing but `GROCERY_BOT_DB_PATH`: add-item,
remove-item, list-items, price, deals, recipe, recipe-text, meal-plan,
nudge, confirm-card, benefits-catalog, benefits-branches, price-compare.
No Telegram token, no store session, no exit node — that project has no use for this
one's secrets. `add-to-cart` is the exception and is listed separately in
`_STORE_COMMANDS`: it reaches the real carts, so it needs the store
sessions and the Israeli exit.
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
from . import nudge
from .config import Config
from .orchestrator import add_terms_to_cart
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

    if command in _STORE_COMMANDS:
        storage = Storage(os.environ.get("GROCERY_BOT_DB_PATH", "data/grocery_bot.sqlite3"))
        return _STORE_COMMANDS[command](storage, rest)

    config = Config.from_env()
    storage = Storage(config.db_path)

    if command == "refresh-prices":
        meta = refresh_catalog(storage, config.shufersal_price_store_id)
        print(
            f"branch {meta.get('branch')}: {meta.get('product_count')} products "
            f"from {meta.get('price_file')}"
        )
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


def _report_recipe(storage, recipe, args: list[str], header: str) -> int:
    """Shared output for the recipe/meal-plan commands.

    Adds only what the household probably lacks by default. Blindly
    queueing flour and sugar for a kitchen that owns flour and sugar
    recreates the delete-by-hand chore this project exists to remove —
    the same reasoning as the Telegram preview, kept identical here so
    both bots behave the same way.

    --preview adds nothing, so the calling bot can show the split and
    ask first; --all queues everything.
    """
    from .pantry import split_ingredients

    requested_by = _flag(args, "--by") or "unknown"
    preview = "--preview" in args
    add_all = "--all" in args

    missing, have = split_ingredients(storage, recipe.ingredients)
    chosen = recipe.ingredients if add_all else missing

    print(f"{header}: {recipe.dish}")
    for item in chosen:
        print(f"  + {item.name}" + (f" ({item.amount:g} {item.unit})" if item.amount and item.unit else ""))
    for item in (have if not add_all else []):
        print(f"  ~ {item.name} (כנראה יש)")
    if recipe.note:
        print(f"  note: {recipe.note}")

    if preview:
        print(f"preview only — nothing added ({len(chosen)} would be added)")
        return 0

    for item in chosen:
        storage.add_adhoc_request(
            text=item.name,
            requested_by=f"{requested_by} (מתכון: {recipe.dish})",
            amount=item.amount,
            unit=item.unit,
        )
    skipped = len(recipe.ingredients) - len(chosen)
    print(f"added {len(chosen)}" + (f", skipped {skipped} already at home" if skipped else ""))
    return 0


def _recipe(storage: Storage, args: list[str]) -> int:
    from .nlu import expand_recipe

    dish = " ".join(_positional(args)).strip()
    if not dish:
        print("usage: recipe <dish> [--by NAME] [--all] [--preview]")
        return 2
    recipe = expand_recipe(dish)
    if recipe is None:
        print(f"could not build ingredients for {dish!r}")
        return 1
    return _report_recipe(storage, recipe, args, "recipe")


def _recipe_text(storage: Storage, args: list[str]) -> int:
    """Ingredients from recipe text on stdin — the screenshot path.

    Image handling stays with the bot that received the image; this end
    takes the extracted text. That keeps OCR in one place and grocery
    knowledge in another, and it works just as well for a pasted recipe.
    """
    from .nlu import extract_recipe_from_text

    body = sys.stdin.read()
    if not body.strip():
        print("usage: recipe-text [--by NAME] [--all] [--preview]  < recipe.txt")
        return 2
    recipe = extract_recipe_from_text(body)
    if recipe is None:
        print("no ingredients found in that text")
        return 1
    return _report_recipe(storage, recipe, args, "recipe")


def _meal_plan(storage: Storage, args: list[str]) -> int:
    from .nlu import build_meal_plan

    request = " ".join(_positional(args)).strip()
    household = storage.get_state("household_context")
    if household:
        request = f"{request}. הרכב המשפחה: {household}" if request else f"הרכב המשפחה: {household}"
    staples = [
        row["product_name"]
        for row in storage.list_stock_items("shufersal")
        if row["tier"] in ("A", "B", "C")
    ]
    plan = build_meal_plan(request, staples)
    if plan is None:
        print("could not build a meal plan")
        return 1
    for day, dish in plan.meals:
        print(f"  {day}: {dish}" if day else f"  {dish}")

    from .nlu import Recipe

    return _report_recipe(
        storage,
        Recipe(dish="תפריט שבועי", ingredients=plan.ingredients, note=plan.note),
        args,
        "meal plan",
    )


def _positional(args: list[str]) -> list[str]:
    """Arguments that are not flags or flag values."""
    words, skip = [], False
    for argument in args:
        if skip:
            skip = False
            continue
        if argument.startswith("--"):
            skip = argument in ("--by",)
            continue
        words.append(argument)
    return words


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
def _add_to_cart(storage: Storage, args: list[str]) -> int:
    """Put an item straight into the real store cart.

    The other half of the integration surface for the household's second
    bot. `add-item` puts something on the list for the next cycle; this
    reaches the cart that is open right now, which is what "תוסיף לסל"
    means when the shop is already half-built.

    Needs the store session and the Israeli exit, so unlike `add-item` it
    is not on the token-free path — it is listed separately and fails with
    a clear message rather than a stack trace when those are missing.

    The hard rule is unchanged and unchangeable here: this adds to a cart
    and can never reach a checkout or payment step. See CLAUDE.md.
    """
    words, skip, quantity = [], False, 1
    for index, argument in enumerate(args):
        if skip:
            skip = False
            continue
        if argument == "--qty" and index + 1 < len(args):
            try:
                quantity = max(1, int(args[index + 1]))
            except ValueError:
                quantity = 1
            skip = True
            continue
        if argument.startswith("--"):
            skip = True
            continue
        words.append(argument)

    term = " ".join(words).strip()
    if not term:
        print("usage: add-to-cart <text> [--qty N]", file=sys.stderr)
        return 2

    # Imported here rather than at module load: the adapter pulls in
    # Playwright, and the token-free commands the other bot calls must not
    # pay for that.
    from .telegram_bot import _build_adapter_factories

    config = Config.from_env()
    factories = _build_adapter_factories(config)
    if not factories:
        print("no store adapters configured", file=sys.stderr)
        return 1

    from .chains import display_name

    reports = add_terms_to_cart(storage, factories, [(term, quantity)])

    # `add_terms_to_cart` fills *every* enabled chain, so with Shufersal
    # and Tiv Taam both on there are two real carts afterwards. This used
    # to print the first chain that succeeded and return, which reported
    # one cart while having filled two — the caller then told the
    # household "added to Shufersal" and nobody knew about the other. Say
    # every chain that took it; naming the chain is the whole point, since
    # a deal spotted at one chain is otherwise filled at another chain's
    # price with no sign of the substitution.
    added_anywhere = False
    for store, report in reports.items():
        for result in report.added:
            print(f"added to the {display_name(store)} cart: {result.item_name}")
            added_anywhere = True
    if added_anywhere:
        return 0

    # Nowhere took it. Ambiguity and a plain miss both end on the list —
    # the request must not vanish — but they are different answers and
    # the caller is told which.
    ambiguous_at = [
        display_name(store) for store, report in reports.items() if report.ambiguous
    ]
    storage.add_adhoc_request(text=term, requested_by="")
    if ambiguous_at:
        print(
            f"ambiguous at {', '.join(ambiguous_at)}: {term} — added to the list instead"
        )
    else:
        print(f"not found in the store: {term} — added to the list instead")
    return 0


def _nudge(storage: Storage, args: list[str]) -> int:
    """Print the overdue-shop message, or nothing when it is not due.

    Called on a schedule by the household's other bot, which delivers it
    to the group both partners already talk in. Delivery deliberately
    belongs to that bot: the grocery bot is not in their group, and making
    them answer a second assistant is the friction this removes.

    Exit 0 with output means "send this". Exit 0 with no output means
    "nothing to say", which is the common case and must stay silent.
    Replies come back through add-item, which already exists.
    """
    import datetime

    last_nudged = None
    for index, argument in enumerate(args):
        if argument == "--last-nudged" and index + 1 < len(args):
            try:
                last_nudged = datetime.date.fromisoformat(args[index + 1])
            except ValueError:
                pass

    import os

    decision = nudge.decide(
        storage,
        last_nudged=last_nudged,
        bot_username=os.environ.get("TELEGRAM_BOT_USERNAME", ""),
    )
    if decision.due:
        print(decision.text)
    elif "--why" in args:
        print(f"not due: {decision.reason}", file=sys.stderr)
    return 0


def _price(storage: Storage, args: list[str]) -> int:
    query = " ".join(_positional(args)).strip()
    if not query:
        print("usage: price <query>")
        return 2
    print(format_search_answer(query, storage.search_with_deals(query, limit=8)))
    return 0


def _deals(storage: Storage, args: list[str]) -> int:
    items = storage.list_active_base_items()
    print(format_deals_report(find_deals_for_base_list(storage, items)))
    return 0


def _price_compare(storage: Storage, args: list[str]) -> int:
    """Where is a product cheapest across every chain we hold prices for.

    The canonical "where's it cheapest" answer (see MIRI_INTEGRATION). It is
    a name-match per chain, not a proven same-product comparison — Shufersal
    has no barcode to join on — so the output says so.
    """
    import json

    as_json = "--json" in args
    query = " ".join(a for a in args if not a.startswith("--")).strip()
    if not query:
        print("usage: price-compare <query> [--json]")
        return 2
    rows = storage.cross_chain_prices(query)
    if as_json:
        print(json.dumps(rows, ensure_ascii=False))
        return 0 if rows else 1
    if not rows:
        print(f'לא נמצא מחיר ל-"{query}" באף רשת.')
        return 1
    lines = [f'*הכי זול — {query}*']
    for r in rows:
        line = f"• {r['chain']}: {r['price']:.2f}₪ — {r['name']}"
        if r.get("unit_price"):
            unit = (r.get("unit") or "").strip()
            line += f"  ({r['unit_price']:.2f}₪ ל{unit})" if unit else ""
        lines.append(line)
    lines.append("")
    lines.append("_התאמה לפי שם, לא לפי ברקוד — הגדלים/הווריאנטים עשויים להיות שונים בין הרשתות._")
    print("\n".join(lines))
    return 0


_BENEFITS_MEMORY_STORE = "benefits"


def _benefits_catalog(storage: Storage, args: list[str]) -> int:
    """The harvested benefit-club store catalog — see benefits_catalog.py.

    Reads the flat files under data/benefits/, and — this is the one use of
    `storage` — checks the disambiguation memory first: once the household
    has said "פוקס means פוקס הום" (recorded via `benefits-remember`), a
    later "פוקס" resolves straight to that merchant instead of returning
    the whole ambiguous set again. The same term→choice memory the grocery
    bot already uses for product variants (Ishay-approved, 2026-09-04).
    """
    import json

    from .benefits_catalog import (
        format_catalog_rows,
        freshness,
        load_catalog,
        search_catalog,
    )

    # --freshness: how current the data is, per club. For a caller (or
    # another bot) that needs to state the as-of date, not the data.
    if "--freshness" in args:
        fresh = freshness()
        if "--json" in args:
            print(json.dumps(fresh, ensure_ascii=False))
        else:
            for club, status in fresh.items():
                print(f"{club}: {status}")
        return 0

    as_json = "--json" in args
    query = " ".join(a for a in args if not a.startswith("--")).strip()
    rows = search_catalog(query) if query else load_catalog()

    resolved_to = None
    if query and len(rows) > 1:
        remembered = storage.preferred_for(_BENEFITS_MEMORY_STORE, query)
        if remembered is not None:
            merchant = remembered["product_name"]
            narrowed = [r for r in rows if (r.get("חנות") or "").strip() == merchant]
            # Keep the memory honest: if the remembered merchant has since
            # left the catalogue, fall back to the full set rather than
            # returning nothing.
            if narrowed:
                rows, resolved_to = narrowed, merchant

    if as_json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        if resolved_to:
            print(f"_(זכור: \"{query}\" → {resolved_to})_")
        print(format_catalog_rows(rows, query))
    return 0 if rows else 1


def _benefits_remember(storage: Storage, args: list[str]) -> int:
    """Record that a search term should resolve to a specific merchant.

    The write half of ask-when-unsure: when the household disambiguates
    ("לא, התכוונתי לפוקס הום"), Miri calls this so the next lookup skips the
    question. Every entry is a household decision — Miri writes it on their
    confirmation, not on a guess.

    Usage: benefits-remember "<term>" "<merchant>"   (two positional args)
    or with --forget to drop a remembered term.
    """
    positional = [a for a in args if not a.startswith("--")]
    if "--forget" in args:
        if len(positional) != 1:
            print('usage: benefits-remember --forget "<term>"')
            return 2
        forgotten = storage.forget_choice(_BENEFITS_MEMORY_STORE, positional[0].strip())
        print(f"forgot: {positional[0]}" if forgotten else f"nothing remembered for: {positional[0]}")
        return 0 if forgotten else 1
    if len(positional) != 2:
        print('usage: benefits-remember "<term>" "<merchant>"')
        return 2
    term, merchant = positional[0].strip(), positional[1].strip()
    if not term or not merchant:
        print("both a term and a merchant are required")
        return 2
    storage.remember_choice(
        store=_BENEFITS_MEMORY_STORE, term=term,
        product_code=merchant, product_name=merchant,
    )
    print(f'remembered: "{term}" → {merchant}')
    return 0


def _benefits_branches(storage: Storage, args: list[str]) -> int:
    """Street addresses for the benefit-club stores. See benefits_catalog.py."""
    import json

    from .benefits_catalog import format_branch_rows, load_branches, search_branches

    as_json = "--json" in args
    query = " ".join(a for a in args if not a.startswith("--")).strip()
    rows = search_branches(query) if query else load_branches()

    if as_json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        print(format_branch_rows(rows, query))
    return 0 if rows else 1


def _confirm_card(storage: Storage, args: list[str]) -> int:
    """Record that the benefit card was loaded this month.

    Called by the other bot when someone answers the monthly card
    question. Pass the person's raw reply rather than deciding yourself:
    "לא הטענתי" contains "הטענתי" and only reads as a negation if the
    negation is checked first, which this does.

    With no arguments it confirms unconditionally, for a caller that has
    already decided.
    """
    from . import cardreminder

    reply = " ".join(a for a in args if not a.startswith("--")).strip()
    if reply and not cardreminder.looks_confirmed(reply):
        print("not a confirmation; nothing recorded")
        return 0
    cardreminder.confirm(storage)
    print(f"recorded: card loaded for {cardreminder.current_month()}")
    return 0


_DB_ONLY_COMMANDS = {
    "add-item": lambda storage, args: _add_item(storage, args),
    "remove-item": _remove_item,
    "list-items": _list_items,
    # Recipes and meal plans need the model, not the store, so they stay
    # on the token-free path the other bot already calls.
    "recipe": _recipe,
    "recipe-text": _recipe_text,
    "meal-plan": _meal_plan,
    # Reads only what earlier syncs already wrote, so the other bot can
    # call it on a timer without a token or a store session.
    "nudge": _nudge,
    "confirm-card": _confirm_card,
    # Both answer from the catalog `refresh-prices` already wrote, so
    # neither needs the store, the exit node or this bot's token. They sat
    # behind Config.from_env() only because that is where every non-list
    # command happened to land, and the household's other bot could not
    # ask "how much is cottage cheese" without holding our Telegram
    # secret — a credential it has no use for.
    "price": _price,
    "price-compare": _price_compare,
    "deals": _deals,
    # Reads flat CSVs under data/benefits/ (gitignored — household
    # financial data), not the sqlite database at all; `storage` is
    # accepted and ignored to keep one dispatch shape. No token, no store
    # session, no exit node — this is the harvested catalog already on
    # disk, not a live fetch.
    "benefits-catalog": _benefits_catalog,
    "benefits-branches": _benefits_branches,
    "benefits-remember": _benefits_remember,
}

# Needs the store session and the Israeli exit, so it cannot live on the
# token-free path — but it is still part of the second bot's surface.
_STORE_COMMANDS = {
    "add-to-cart": _add_to_cart,
}


if __name__ == "__main__":
    raise SystemExit(main())
