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
    find_deals_for_base_list,
    format_deals_report,
    format_search_answer,
    refresh_catalog,
)
from .config import Config
from .orchestrator import format_report_summary, run_order_cycle
from .storage import Storage

logger = logging.getLogger(__name__)

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
        factories[store] = lambda cls=adapter_cls, path=state_path: cls(path, headless=config.headless)
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
            "היי! שלחו לי כל דבר בטקסט חופשי כדי להוסיף אותו לרשימת הקנייה הבאה.\n\n"
            "*מחירים ומבצעים* (עובד כבר עכשיו):\n"
            "/price חלב — מחיר נוכחי ומבצע אמיתי, אם יש\n"
            "/deals — מבצעים על פריטי רשימת הבסיס\n"
            "/refresh_prices — משיכת מחירים עדכניים מהסניף\n\n"
            "*רשימה והזמנה*:\n"
            "/list — רשימת הבסיס הפעילה\n"
            "/start_order — מחזור קנייה שממלא עגלה אמיתית "
            "(חסום כרגע — האתר של שופרסל חוסם גישה מחוץ לישראל)",
            parse_mode="Markdown",
        )

    async def list_base_items(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        items = self.storage.list_active_base_items()
        if not items:
            await update.message.reply_text("רשימת הבסיס ריקה כרגע.")
            return
        lines = [f"• {item.name} (x{item.default_quantity})" for item in items]
        await update.message.reply_text("\n".join(lines))

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

    async def capture_adhoc(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(self.config, update):
            return
        text = (update.message.text or "").strip()
        if not text:
            return
        requested_by = update.effective_user.first_name if update.effective_user else "unknown"
        self.storage.add_adhoc_request(text=text, requested_by=requested_by)
        await update.message.reply_text(f"נוסף לרשימה לפעם הבאה: {text}")

    async def start_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(self.config, update):
            return
        await update.message.reply_text("מתחיל מחזור קנייה, זה ייקח כמה רגעים...")
        factories = _build_adapter_factories(self.config)
        if not factories:
            await update.message.reply_text(
                "אין אף רשת מוגדרת/מיושמת (בדקו ENABLED_STORES ואת שלב ה-login החד-פעמי)."
            )
            return
        try:
            reports = await asyncio.to_thread(run_order_cycle, self.storage, factories)
        except Exception:
            logger.exception("Order cycle failed")
            await update.message.reply_text("המחזור נכשל עם שגיאה לא צפויה — בדקו את הלוגים בשרת.")
            return

        summary = format_report_summary(reports)
        await update.message.reply_text(summary or "לא היה מה להוסיף.", parse_mode="Markdown")
        await self._send_pending_ambiguities(update, context)

    async def _send_pending_ambiguities(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        for pending in self.storage.list_pending_ambiguities():
            buttons = [
                [InlineKeyboardButton(label, callback_data=f"resolve:{pending['id']}:{i}")]
                for i, label in enumerate(pending["candidates"])
            ]
            buttons.append([InlineKeyboardButton("דלג", callback_data=f"skip:{pending['id']}:0")])
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"[{pending['store']}] '{pending['original_term']}' — כמה תוצאות מתאימות, איזו?",
                reply_markup=InlineKeyboardMarkup(buttons),
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
        chosen_label = pending["candidates"][choice_index]
        factories = _build_adapter_factories(self.config)
        make_adapter = factories.get(pending["store"])
        if make_adapter is None:
            await query.edit_message_text("הרשת הזו לא מוגדרת יותר, לא ניתן להשלים.")
            return

        def _add() -> str:
            with make_adapter() as adapter:
                result = adapter.add_specific_product(chosen_label, pending["quantity"])
            return result.status

        status = await asyncio.to_thread(_add)
        self.storage.mark_ambiguity_resolved(ambiguity_id)
        if status == "added":
            await query.edit_message_text(f"נוסף: {chosen_label}")
        else:
            await query.edit_message_text(f"לא הצלחתי להוסיף את '{chosen_label}' (status: {status}).")


async def _register_bot_metadata(application: Application) -> None:
    """Keep BotFather's command list/description in sync with the code.

    Runs once on every startup so the command list never drifts out of
    sync with the handlers below — no manual BotFather step needed after
    the first setup.
    """
    await application.bot.set_my_commands(
        [
            BotCommand("start", "הצגת הוראות שימוש"),
            BotCommand("price", "מחיר ומבצע למוצר — למשל /price חלב"),
            BotCommand("deals", "מבצעים אמיתיים על פריטי רשימת הבסיס"),
            BotCommand("list", "הצגת רשימת הבסיס הפעילה"),
            BotCommand("refresh_prices", "רענון המחירים והמבצעים מהפיד הציבורי"),
            BotCommand("start_order", "הרצת מחזור קנייה (ממלא עגלה אמיתית)"),
        ]
    )
    await application.bot.set_my_description(
        "בוט קניות אישי — ממלא עגלה אמיתית בשופרסל לפי רשימת בסיס + בקשות "
        "אד-הוק. שולחים כל בקשה כטקסט חופשי; /start_order מריץ מחזור קנייה."
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
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.capture_adhoc))
    return application
