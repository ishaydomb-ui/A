"""Telegram bot: manual trigger, ad-hoc capture, ambiguity resolution.

Deliberately a separate bot from any existing "second brain" bot (per
project decision) so grocery traffic doesn't mix with unrelated notes.
"""
from __future__ import annotations

import asyncio
import logging

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .adapters.base import StoreAdapter
from .adapters.shufersal import ShufersalAdapter
from .catalog import (
    find_cycle_alternatives,
    find_deals_for_base_list,
    format_cycle_alternatives,
    format_deals_report,
    format_full_list,
    format_search_answer,
    refresh_catalog,
)
from .cartview import MIN_EDIT_INTERVAL_SECONDS, render_final, render_progress
from .config import Config
from .disambiguate import describe_card
from .connectivity import check_israeli_exit
from .nlu import ParsedItem, build_meal_plan, expand_recipe, parse_message
from .orchestrator import format_report_summary, run_order_cycle
from .storage import Storage

logger = logging.getLogger(__name__)

# How often to check whether the Israeli exit node came back, when a cycle
# is waiting on it. The exit is a TV box someone switches on and off by
# hand, so there is nothing to subscribe to — polling is the only option.
# Two minutes keeps the wait short without hammering the probe endpoint.
EXIT_POLL_SECONDS = 120

# Deep link to the real cart. The store's own page is the authoritative
# view and the place the purchase is completed by hand, so the bot points
# at it rather than trying to reproduce it.
SHUFERSAL_CART_URL = "https://www.shufersal.co.il/online/he/cart/cartsummary"


def _describe_parsed(item: ParsedItem) -> str:
    parts = [item.name]
    if item.amount and item.unit:
        parts.append(f"{item.amount:g} {item.unit}")
    elif item.amount:
        parts.append(f"x{item.amount:g}")
    line = " ".join(parts)
    return f"{line} ({item.brand})" if item.brand else line

ADAPTER_CLASSES: dict[str, type[StoreAdapter]] = {
    "shufersal": ShufersalAdapter,
    # "tiv_taam": TivTaamAdapter,  # Phase 2
}


def _build_adapter_factories(config: Config):
    session_paths = {
        "shufersal": config.shufersal_storage_state_path,
        "tiv_taam": config.tivtaam_storage_state_path,
    }
    factories = {}
    for store in config.enabled_stores:
        adapter_cls = ADAPTER_CLASSES.get(store)
        if adapter_cls is None:
            logger.warning("No adapter implemented yet for store %r, skipping", store)
            continue
        state_path = session_paths[store]
        # Credentials are per-store; only Shufersal has them wired up so far.
        credentials = (
            {"username": config.shufersal_username, "password": config.shufersal_password}
            if store == "shufersal"
            else {}
        )
        factories[store] = lambda cls=adapter_cls, path=state_path, creds=credentials: cls(
            path, headless=config.headless, proxy=config.playwright_proxy, **creds
        )
    return factories


def _authorized(config: Config, update: Update) -> bool:
    if not config.allowed_telegram_user_ids:
        return True  # no allowlist configured -> open (fine for a private single-user bot)
    user = update.effective_user
    return user is not None and user.id in config.allowed_telegram_user_ids


class GroceryBot:
    def __init__(self, config: Config, storage: Storage):
        self.config = config
        self.storage = storage

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "היי! פשוט דברו איתי רגיל, בלי פקודות. למשל:\n\n"
            "• *תוסיף 300 גרם פסטרמה* — מוסיף לרשימה עם משקל\n"
            "• *צריך טונה סטארקיסט 4 יחידות* — שומר גם את היצרן\n"
            "• *תוריד את הטונה* — מוריד מהרשימה\n"
            "• *מה יש ברשימה* — הרשימה המלאה והמעודכנת\n"
            "• *כמה עולה קוטג* — מחיר נוכחי בסניף + מבצע אם יש\n"
            "• *מה יש במבצע* — מבצעים אמיתיים על מה שאתם קונים\n"
            "• *מתכון לפאי תפוחים* — מפרק למצרכים ומוסיף לרשימה\n"
            "• *תכנן לי תפריט שבועי* — 5 ארוחות + רשימת קניות מאוחדת\n\n"
            "_מילוי עגלה אוטומטי בשופרסל עדיין חסום — האתר חוסם גישה "
            "מחוץ לישראל._",
            parse_mode="Markdown",
        )

    async def list_base_items(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._do_show_list(update, context, None, "")

    async def price(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/price <item> — current shelf price + any real promotion.

        Reads the public price feed only, so this works even while cart
        automation is blocked on network access to the shop itself.
        """
        if not _authorized(self.config, update):
            return
        query = " ".join(context.args).strip()
        if not query:
            await update.message.reply_text("איזה מוצר לבדוק? למשל: /price חלב")
            return
        if self.storage.catalog_meta().get("product_count") == "0":
            await update.message.reply_text(
                "הקטלוג עדיין ריק — הריצו /refresh_prices כדי למשוך את המחירים מהסניף."
            )
            return
        results = await asyncio.to_thread(self.storage.search_with_deals, query, 6)
        await update.message.reply_text(
            format_search_answer(query, results), parse_mode="Markdown"
        )

    async def deals(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/deals — genuine promotions on anything in the standing list."""
        if not _authorized(self.config, update):
            return
        items = self.storage.list_active_base_items()
        if not items:
            await update.message.reply_text("רשימת הבסיס ריקה, אז אין על מה לחפש מבצעים.")
            return
        found = await asyncio.to_thread(find_deals_for_base_list, self.storage, items)
        await update.message.reply_text(format_deals_report(found), parse_mode="Markdown")

    async def refresh_prices(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/refresh_prices — re-download the branch's price + promo snapshot."""
        if not _authorized(self.config, update):
            return
        await update.message.reply_text("מרענן מחירים ומבצעים מהפיד הציבורי, רגע...")
        try:
            meta = await asyncio.to_thread(
                refresh_catalog, self.storage, self.config.shufersal_price_store_id
            )
        except Exception:
            logger.exception("Catalog refresh failed")
            await update.message.reply_text(
                "רענון המחירים נכשל — בדקו את הלוגים בשרת."
            )
            return
        await update.message.reply_text(
            f"הקטלוג עודכן: {meta.get('product_count', '?')} מוצרים בסניף "
            f"{meta.get('branch', '?')}.\nמקור: {meta.get('price_file', '?')}"
        )

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Route a free-text message by what it actually means.

        The first version filed every message verbatim as an item, so
        "תוסיף גבינה בולגרית" was stored under that whole sentence and
        "מה" became groceries. Everything now goes through the NLU layer
        first; understanding takes several seconds, hence the typing
        indicator.
        """
        if not _authorized(self.config, update):
            return
        text = (update.message.text or "").strip()
        if not text:
            return

        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        parsed = await asyncio.to_thread(parse_message, text)
        requested_by = update.effective_user.first_name if update.effective_user else "unknown"

        handler = {
            "add_item": self._do_add,
            "remove_item": self._do_remove,
            "price_query": self._do_price,
            "deals": self._do_deals,
            "show_list": self._do_show_list,
            "recipe": self._do_recipe,
            "meal_plan": self._do_meal_plan,
            "start_order": self._do_start_order,
        }.get(parsed.intent)

        if handler is None:  # unclear / smalltalk
            await update.message.reply_text(
                parsed.reply
                or "לא הבנתי מה צריך. אפשר למשל: 'תוסיף 300 גרם פסטרמה', "
                "'כמה עולה קוטג', 'מה יש במבצע', 'מתכון לפאי תפוחים'."
            )
            return
        await handler(update, context, parsed, requested_by)

    async def _do_start_order(self, update, context, parsed, requested_by: str) -> None:
        """Let plain Hebrew start a cycle, not just the /start_order command."""
        await self.start_order(update, context)

    async def _do_add(self, update, context, parsed, requested_by: str) -> None:
        if not parsed.items:
            await update.message.reply_text("מה להוסיף?")
            return
        added = []
        for item in parsed.items:
            self.storage.add_adhoc_request(
                text=item.name,
                requested_by=requested_by,
                amount=item.amount,
                unit=item.unit,
                brand=item.brand,
            )
            added.append(_describe_parsed(item))
        message = "נוסף לרשימה: " + ", ".join(added)
        if parsed.used_fallback:
            # The rule-based fallback files anything it can't classify as an
            # item, so a whole sentence can land on the list looking like a
            # product. Say so instead of letting a silent degradation look
            # like the bot simply misunderstood.
            message += "\n\n⚠️ _הבנת השפה לא זמינה כרגע, אז ייתכן שפירשתי לא נכון. אפשר לתקן עם 'תוריד ...'._"
        await update.message.reply_text(message, parse_mode="Markdown")

    async def _do_remove(self, update, context, parsed, requested_by: str) -> None:
        if not parsed.items:
            await update.message.reply_text("מה להוריד?")
            return
        removed, missing = [], []
        for item in parsed.items:
            # Ad-hoc first: a just-added request is the likelier target of
            # "תוריד את X" than a long-standing base-list entry.
            hit = self.storage.remove_adhoc_by_name(item.name)
            if hit is None:
                hit = self.storage.deactivate_base_item_by_name(item.name)
            (removed if hit else missing).append(hit or item.name)
        parts = []
        if removed:
            parts.append("הורדתי: " + ", ".join(removed))
        if missing:
            parts.append("לא מצאתי ברשימה: " + ", ".join(missing))
        await update.message.reply_text("\n".join(parts))

    async def _do_price(self, update, context, parsed, requested_by: str) -> None:
        query = parsed.query or (parsed.items[0].name if parsed.items else "")
        if not query:
            await update.message.reply_text("איזה מוצר לבדוק?")
            return
        if self.storage.catalog_meta().get("product_count") == "0":
            await update.message.reply_text("הקטלוג ריק — רגע, תבקשו ממני 'תרענן מחירים'.")
            return
        results = await asyncio.to_thread(self.storage.search_with_deals, query, 6)
        await update.message.reply_text(
            format_search_answer(query, results), parse_mode="Markdown"
        )

    async def _do_deals(self, update, context, parsed, requested_by: str) -> None:
        items = self.storage.list_active_base_items()
        if not items:
            await update.message.reply_text("רשימת הבסיס ריקה, אז אין על מה לחפש מבצעים.")
            return
        found = await asyncio.to_thread(find_deals_for_base_list, self.storage, items)
        await update.message.reply_text(format_deals_report(found), parse_mode="Markdown")

    async def _do_show_list(self, update, context, parsed, requested_by: str) -> None:
        await update.message.reply_text(
            format_full_list(
                self.storage.list_active_base_items(), self.storage.list_pending_adhoc()
            ),
            parse_mode="Markdown",
        )

    async def _do_recipe(self, update, context, parsed, requested_by: str) -> None:
        dish = parsed.query or (parsed.items[0].name if parsed.items else "")
        if not dish:
            await update.message.reply_text("מתכון למה?")
            return
        await update.message.reply_text(f"בונה רשימת מצרכים ל{dish}, רגע...")
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        recipe = await asyncio.to_thread(expand_recipe, dish)
        if recipe is None:
            await update.message.reply_text(f"לא הצלחתי לבנות מצרכים ל'{dish}'. נסו לנסח אחרת.")
            return
        for ingredient in recipe.ingredients:
            self.storage.add_adhoc_request(
                text=ingredient.name,
                requested_by=f"{requested_by} (מתכון: {recipe.dish})",
                amount=ingredient.amount,
                unit=ingredient.unit,
            )
        lines = [f"*{recipe.dish}* — הוספתי {len(recipe.ingredients)} מצרכים לרשימה:"]
        lines += [f"• {_describe_parsed(i)}" for i in recipe.ingredients]
        if recipe.note:
            lines.append(f"\n_{recipe.note}_")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _do_meal_plan(self, update, context, parsed, requested_by: str) -> None:
        await update.message.reply_text("בונה תפריט שבועי ורשימת קניות, זה ייקח כמה שניות...")
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        plan = await asyncio.to_thread(build_meal_plan, parsed.query)
        if plan is None:
            await update.message.reply_text("לא הצלחתי לבנות תפריט כרגע, נסו שוב.")
            return
        for ingredient in plan.ingredients:
            self.storage.add_adhoc_request(
                text=ingredient.name,
                requested_by=f"{requested_by} (תפריט שבועי)",
                amount=ingredient.amount,
                unit=ingredient.unit,
            )
        lines = ["*תפריט השבוע*"]
        lines += [f"• {day}: {dish}" if day else f"• {dish}" for day, dish in plan.meals]
        lines.append(f"\n*הוספתי {len(plan.ingredients)} מצרכים לרשימה:*")
        lines += [f"• {_describe_parsed(i)}" for i in plan.ingredients]
        if plan.note:
            lines.append(f"\n_{plan.note}_")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def start_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(self.config, update):
            return
        factories = _build_adapter_factories(self.config)
        if not factories:
            await update.message.reply_text(
                "אין אף רשת מוגדרת/מיושמת (בדקו ENABLED_STORES ואת שלב ה-login החד-פעמי)."
            )
            return

        # Check the Israeli exit *before* starting. Without this the cycle
        # runs anyway and every single item comes back as an error, which
        # reads like the bot is broken rather than like the exit node at
        # home is simply switched off.
        status = await asyncio.to_thread(check_israeli_exit, self.config.playwright_proxy)
        if not status.available:
            self.storage.defer_cycle(
                chat_id=update.effective_chat.id,
                requested_by=update.effective_user.full_name if update.effective_user else "",
            )
            pending = len(self.storage.list_pending_adhoc())
            await update.message.reply_text(
                f"🕒 אין כרגע חיבור לשופרסל ({status.detail}).\n"
                f"המחזור נשמר בתור ({pending} בקשות ממתינות) ויתחיל אוטומטית "
                "ברגע שהחיבור יחזור — לא צריך לשלוח שוב."
            )
            return

        reports = await self._run_cycle_with_live_view(update.effective_chat.id, context, factories)
        if reports is None:
            return

        summary = format_report_summary(reports)
        await update.message.reply_text(summary or "לא היה מה להוסיף.", parse_mode="Markdown")
        await self._send_alternatives(update.effective_chat.id, context, reports)
        await self._send_pending_ambiguities(update, context)

    async def _run_cycle_with_live_view(self, chat_id: int, context, factories):
        """Run a cycle while keeping one message updated with its progress.

        The cycle runs in a worker thread, so progress arrives off the
        event loop and is marshalled back with run_coroutine_threadsafe.
        Edits are throttled (see cartview): Telegram rate-limits repeated
        edits to one message, and a twenty-item run would blow through it.
        """
        loop = asyncio.get_running_loop()
        view = await context.bot.send_message(
            chat_id=chat_id, text="🛒 *מתחיל למלא את העגלה…*", parse_mode="Markdown"
        )
        # Pinning keeps it reachable during a long run; not every chat
        # allows it, and failing to pin must not abort the shop.
        try:
            await context.bot.pin_chat_message(
                chat_id=chat_id, message_id=view.message_id, disable_notification=True
            )
        except Exception:
            logger.info("Could not pin the live cart view; continuing without it")

        collected: list = []
        last_edit = 0.0

        async def _redraw(text: str) -> None:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=view.message_id, text=text, parse_mode="Markdown"
                )
            except Exception:
                # A failed edit (unchanged text, rate limit) is cosmetic.
                logger.debug("Live cart edit failed", exc_info=True)

        def _on_progress(done: int, total: int, result) -> None:
            nonlocal last_edit
            collected.append(result)
            now = loop.time()
            if now - last_edit < MIN_EDIT_INTERVAL_SECONDS and done < total:
                return
            last_edit = now
            asyncio.run_coroutine_threadsafe(
                _redraw(render_progress(list(collected), done, total)), loop
            )

        try:
            reports = await asyncio.to_thread(
                run_order_cycle, self.storage, factories, _on_progress
            )
        except Exception:
            logger.exception("Order cycle failed")
            await _redraw("🛑 המחזור נכשל עם שגיאה לא צפויה — בדקו את הלוגים בשרת.")
            return None

        cart = await asyncio.to_thread(self._read_cart, factories)
        results = [r for report in reports.values() for r in report.results]
        await self._finish_live_view(chat_id, context, view.message_id, results, cart)
        return reports

    async def _finish_live_view(self, chat_id, context, message_id, results, cart) -> None:
        buttons = [[InlineKeyboardButton("🛒 פתיחת הסל בשופרסל", url=SHUFERSAL_CART_URL)]]
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=render_final(results, cart),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception:
            logger.exception("Could not render the final cart view")

    def _read_cart(self, factories) -> dict | None:
        """Read the authoritative cart total, if the store is reachable."""
        make_adapter = factories.get("shufersal")
        if make_adapter is None:
            return None
        try:
            with make_adapter() as adapter:
                reader = getattr(adapter, "cart_summary", None)
                return reader() if reader else None
        except Exception:
            logger.exception("Could not read the cart for the final view")
            return None

    async def _send_alternatives(self, chat_id: int, context, reports) -> None:
        """Point out cheaper promoted substitutes for what was just added.

        Sent after the cart is already filled, on purpose: the project
        rules out an approval gate, so this is information to act on if
        you want it, not a question blocking the order.
        """
        added = [result.item_name for report in reports.values() for result in report.added]
        if not added:
            return
        suggestions = await asyncio.to_thread(find_cycle_alternatives, self.storage, added)
        message = format_cycle_alternatives(suggestions)
        if message:
            await context.bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")

    async def _send_pending_ambiguities(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._ask_ambiguities(update.effective_chat.id, context)

    async def _ask_ambiguities(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
        for pending in self.storage.list_pending_ambiguities():
            text, buttons = _format_choice(pending)
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="Markdown",
            )

    async def resolve_ambiguity(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        action, ambiguity_id_str, choice_str = query.data.split(":")
        ambiguity_id = int(ambiguity_id_str)

        pending = self.storage.get_pending_ambiguity(ambiguity_id)
        if pending is None:
            await query.edit_message_text("הבקשה הזו כבר טופלה או פגה.")
            return

        if action == "skip":
            self.storage.mark_ambiguity_resolved(ambiguity_id)
            await query.edit_message_text(f"דילגתי על '{pending['original_term']}'.")
            return

        choice_index = int(choice_str)
        cards = pending.get("candidate_cards") or []
        chosen_card = cards[choice_index] if choice_index < len(cards) else {}
        # Identify by code, never by name: a search for קוטג' returns three
        # different products all named "קוטג' 5% שומן" (Tnuva, Strauss,
        # Tara), so adding by name would silently add whichever matched
        # first -- quite possibly not the one just chosen.
        chosen_code = chosen_card.get("code", "")
        chosen_label = chosen_card.get("name") or pending["candidates"][choice_index]
        factories = _build_adapter_factories(self.config)
        make_adapter = factories.get(pending["store"])
        if make_adapter is None:
            await query.edit_message_text("הרשת הזו לא מוגדרת יותר, לא ניתן להשלים.")
            return

        def _add():
            with make_adapter() as adapter:
                return adapter.add_specific_product(
                    chosen_label,
                    pending["quantity"],
                    product_code=chosen_code,
                    search_term=pending["original_term"],
                )

        result = await asyncio.to_thread(_add)
        self.storage.mark_ambiguity_resolved(ambiguity_id)
        if result.status == "added":
            # Remember it, so this question is asked once per product and
            # not on every single cycle.
            self.storage.remember_choice(
                store=pending["store"],
                term=pending["original_term"],
                product_code=chosen_code or getattr(result, "product_code", "") or "",
                product_name=chosen_label,
            )
            await query.edit_message_text(
                f"נוסף: {chosen_label}\nאזכור את הבחירה הזו ל'{pending['original_term']}' בפעם הבאה."
            )
        else:
            await query.edit_message_text(
                f"לא הצלחתי להוסיף את '{chosen_label}' (status: {result.status})."
            )


    async def drain_deferred_cycle(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Run a queued cycle once the Israeli exit is reachable again.

        Scheduled on the bot's job queue, so it survives the conversation
        that requested the cycle being long over. Does nothing (cheaply)
        when nothing is queued — the probe only runs if there's actually a
        cycle waiting on it.
        """
        pending = self.storage.pending_deferred_cycle()
        if pending is None:
            return

        status = await asyncio.to_thread(check_israeli_exit, self.config.playwright_proxy)
        if not status.available:
            return

        factories = _build_adapter_factories(self.config)
        if not factories:
            return

        chat_id = pending["chat_id"]
        await context.bot.send_message(
            chat_id=chat_id,
            text="🟢 החיבור לשופרסל חזר — מריץ עכשיו את מחזור הקנייה שהמתין בתור.",
        )
        try:
            reports = await self._run_cycle_with_live_view(chat_id, context, factories)
            if reports is None:
                raise RuntimeError("deferred cycle failed")
        except Exception:
            logger.exception("Deferred order cycle failed")
            # Deliberately left un-done so it retries on the next tick: the
            # failure may well be the exit dropping again mid-cycle, and
            # silently discarding a queued cycle would lose real requests.
            await context.bot.send_message(
                chat_id=chat_id,
                text="המחזור שהמתין בתור נכשל — אנסה שוב אוטומטית. בדקו את הלוגים אם זה חוזר.",
            )
            return

        self.storage.mark_deferred_cycle_done(pending["id"])
        summary = format_report_summary(reports)
        await context.bot.send_message(
            chat_id=chat_id,
            text=summary or "לא היה מה להוסיף.",
            parse_mode="Markdown",
        )
        await self._send_alternatives(chat_id, context, reports)
        await self._ask_ambiguities(chat_id, context)


# Telegram truncates a long inline-button label (the client showed roughly
# 24 Hebrew characters), so putting "name · brand size · price" on the
# button hides exactly the part that distinguishes the options. The detail
# goes in the message text instead, and the buttons stay short numbers.
_NUMBER_EMOJI = ("1\ufe0f\u20e3", "2\ufe0f\u20e3", "3\ufe0f\u20e3", "4\ufe0f\u20e3", "5\ufe0f\u20e3")


def _format_choice(pending: dict) -> tuple[str, list[list[InlineKeyboardButton]]]:
    """Render one ambiguity as a numbered list plus a compact button row."""
    cards = pending.get("candidate_cards") or []
    names = pending.get("candidates") or []
    lines = [f"*{pending['original_term']}* — איזה מהם?"]

    if cards:
        cheapest = _cheapest_index(cards)
        for i, card in enumerate(cards[: len(_NUMBER_EMOJI)]):
            marker = " 💰" if i == cheapest else ""
            lines.append(f"{_NUMBER_EMOJI[i]} {describe_card(card)}{marker}")
    else:
        # Older rows saved before candidate detail was stored.
        for i, name in enumerate(names[: len(_NUMBER_EMOJI)]):
            lines.append(f"{_NUMBER_EMOJI[i]} {name}")

    count = min(len(cards or names), len(_NUMBER_EMOJI))
    row = [
        InlineKeyboardButton(_NUMBER_EMOJI[i], callback_data=f"resolve:{pending['id']}:{i}")
        for i in range(count)
    ]
    buttons = [row, [InlineKeyboardButton("דלג", callback_data=f"skip:{pending['id']}:0")]]
    return "\n".join(lines), buttons


def _cheapest_index(cards: list[dict]) -> int | None:
    """Index of the cheapest candidate, for a 💰 hint.

    Only a hint: the cheapest is often a smaller pack rather than a better
    buy, so it is never auto-selected (see disambiguate.py).
    """
    best, best_price = None, None
    for i, card in enumerate(cards):
        try:
            price = float(card.get("price", ""))
        except (TypeError, ValueError):
            continue
        if best_price is None or price < best_price:
            best, best_price = i, price
    return best


async def _register_bot_metadata(application: Application) -> None:
    """Keep BotFather's command list/description in sync with the code.

    Runs once on every startup so the command list never drifts out of
    sync with the handlers below — no manual BotFather step needed after
    the first setup.
    """
    # Deliberately short: the bot is meant to be talked to in plain
    # Hebrew, so the command menu only carries the two things that are
    # awkward to phrase ("help", "the list") plus the manual order run.
    # /price, /deals and /refresh_prices still work if typed, but they're
    # unlisted — asking "כמה עולה קוטג" does the same thing.
    await application.bot.set_my_commands(
        [
            BotCommand("start", "מה אפשר לבקש ממני"),
            BotCommand("list", "הרשימה המלאה והמעודכנת"),
            BotCommand("start_order", "מחזור קנייה (חסום כרגע)"),
        ]
    )
    await application.bot.set_my_description(
        "בוט קניות משפחתי — מדברים איתו רגיל בעברית. מוסיף לרשימה, בודק "
        "מחירים ומבצעים אמיתיים בסניף, מפרק מתכונים למצרכים ובונה תפריט שבועי."
    )


def build_application(config: Config, storage: Storage) -> Application:
    bot = GroceryBot(config, storage)
    application = Application.builder().token(config.telegram_bot_token).post_init(_register_bot_metadata).build()
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("start_order", bot.start_order))
    application.add_handler(CommandHandler("list", bot.list_base_items))
    application.add_handler(CommandHandler("price", bot.price))
    application.add_handler(CommandHandler("deals", bot.deals))
    application.add_handler(CommandHandler("refresh_prices", bot.refresh_prices))
    application.add_handler(CallbackQueryHandler(bot.resolve_ambiguity, pattern=r"^(resolve|skip):"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_message))
    if application.job_queue is not None:
        application.job_queue.run_repeating(
            bot.drain_deferred_cycle,
            interval=EXIT_POLL_SECONDS,
            first=EXIT_POLL_SECONDS,
            name="drain_deferred_cycle",
        )
    else:  # pragma: no cover - depends on optional PTB extra
        logger.warning(
            "JobQueue unavailable: a cycle requested while the exit node is down "
            "will stay queued until /start_order is sent again."
        )
    return application
