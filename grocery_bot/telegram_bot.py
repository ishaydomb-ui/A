"""Telegram bot: manual trigger, ad-hoc capture, ambiguity resolution.

Deliberately a separate bot from any existing "second brain" bot (per
project decision) so grocery traffic doesn't mix with unrelated notes.
"""
from __future__ import annotations

import asyncio
import logging
import random

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import ask, cardreminder, hotdeals, threshold, waste
from .adapters.base import StoreAdapter
from .adapters.shufersal import ShufersalAdapter
from .adapters.tivtaam import TivTaamAdapter
from .catalog import (
    find_cheaper_equivalents,
    find_cycle_alternatives,
    format_cheaper_equivalents,
    find_deals_for_base_list,
    format_cycle_alternatives,
    format_deals_report,
    format_full_list,
    format_search_answer,
    refresh_catalog,
)
from .cartview import MIN_EDIT_INTERVAL_SECONDS, render_final, render_progress
from .checklist import render_department, render_panel, render_summary
from .config import Config
from .digest import compose as compose_digest
from .disambiguate import describe_card
from .listbuilder import as_paste_text, available_lists, build as build_list, summarise
from .connectivity import check_israeli_exit
from .listbuilder import as_paste_text, available_lists, build as build_list, summarise  # noqa: F401  (kept for tests/back-compat)
from .exitnode import ensure_israeli_exit
from .learn import digest_due, sync_from_orders
from .nlu import ParsedItem, build_meal_plan, expand_recipe, parse_message
from .orchestrator import add_terms_to_cart, format_report_summary, run_order_cycle
from .pantry import split_ingredients
from .radar import find_stockup_deals, format_stockup_deals
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

# The bot's waiting lines have a character: "גורדון" — Gordon Ramsay in the
# kitchen, clipped and impatient (household's choice, 2026-08-31; the
# family's other bots got their own — see the familyos project). This is
# surface only: it governs how a "hold on" line is phrased and nothing
# else. No parsing, no list logic, no store behaviour depends on it.
#
# First person throughout, deliberately: the household is two people and a
# second-person Hebrew line would have to pick a gender and would be wrong
# for one of them half the time.
GORDON_THINKING = [
    "🔪 רגע, בודק במזווה.",
    "🍳 שנייה, אני על זה!",
    "🥬 בודק מה חסר.",
    "🛒 עובר על העגלה.",
    "🔥 רגע אחד, כבר מטפל.",
    "🧑\u200d🍳 שנייה, מארגן את התחנה.",
    "📝 רושם את ההזמנה.",
    "⏱️ זה ייקח שנייה. לא יותר.",
    "🔪 עובד על זה. לא עומד בטל.",
    "🍽️ שנייה, מסדר את השירות.",
]

# A shuffled cycle rather than random.choice: with ten lines and plain
# random, the same one repeats back-to-back often enough to read as a bug.
# Every line appears once before any of them comes round again.
_thinking_bag: list[str] = []


def gordon_thinking() -> str:
    """A generic 'hold on' line, in Gordon's voice.

    Surface only. Nothing here is ever the vehicle for a number, an item
    name or a result — the character phrases the wait, never the answer.
    """
    global _thinking_bag
    if not _thinking_bag:
        _thinking_bag = random.sample(GORDON_THINKING, len(GORDON_THINKING))
    return _thinking_bag.pop()


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
    "tivtaam": TivTaamAdapter,
}


def _build_adapter_factories(config: Config):
    session_paths = {
        "shufersal": config.shufersal_storage_state_path,
        "tivtaam": config.tivtaam_storage_state_path,
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


def _added_marks(message_text: str) -> set[str]:
    """Which variant lines are already ticked in the rendered question."""
    added = set()
    for line in (message_text or "").splitlines():
        line = line.strip()
        if line.startswith("✅"):
            added.add(line.lstrip("✅ ").split(" · ")[0].strip())
    return added


def _preticked(row: dict) -> bool:
    """Pre-ticked unless the user has repeatedly removed it.

    Tier A is shown ticked rather than added silently: the user asked to
    see everything going in, so a wrong one is one tap from removal
    instead of a surprise found at checkout.
    """
    from .stock import DEMOTE_AFTER_SKIPS

    skipped, picked = row.get("skipped_count", 0), row.get("picked_count", 0)
    return not (skipped >= DEMOTE_AFTER_SKIPS and skipped > picked)


def _department_buttons(proposal_id: int, dept_index: int, rows: list[dict]):
    """Number buttons per item, then bulk actions.

    Numbers rather than names on the buttons because Telegram truncates a
    long label at roughly 24 Hebrew characters. The department travels as
    an INDEX rather than its Hebrew name for a harder reason: callback
    data is capped at 64 *bytes*, and "ptoggle:12:טיפוח, תינוקות
    וניקיון:P_..." is 68 — Telegram rejects the button outright, which is
    exactly the "buttons do nothing" a real run produced.
    """
    buttons, row = [], []
    for position, item in enumerate(rows, start=1):
        row.append(
            InlineKeyboardButton(
                f"{'✅' if item['selected'] else '⬜'}{position}",
                callback_data=f"ptoggle:{proposal_id}:{dept_index}:{item['product_code']}",
            )
        )
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append(
        [
            InlineKeyboardButton("סמן הכל", callback_data=f"pall:{proposal_id}:{dept_index}"),
            InlineKeyboardButton("נקה הכל", callback_data=f"pnone:{proposal_id}:{dept_index}"),
        ]
    )
    return buttons


def _authorized(config: Config, update: Update) -> bool:
    """Only the household may drive this bot.

    Fails CLOSED when no allowlist is configured. The previous default
    was the opposite — an empty list meant "allow everyone", annotated
    "fine for a private single-user bot" — and that assumption was wrong
    in a way worth spelling out: the bot has a public @username anyone
    can find, and it does not merely answer questions. It reads the
    household's shopping list and routines, and /start_order fills a
    real cart on a real Shufersal account.

    An open default fails silently and invisibly; a closed one fails
    loudly and locally, and is trivially fixed by setting
    ALLOWED_TELEGRAM_USER_IDS. That is the correct direction for the
    mistake to point.
    """
    if not config.allowed_telegram_user_ids:
        logger.error(
            "ALLOWED_TELEGRAM_USER_IDS is empty — refusing every request. "
            "Set it to the household's Telegram user ids in .env."
        )
        return False
    user = update.effective_user
    return user is not None and user.id in config.allowed_telegram_user_ids


class GroceryBot:
    def __init__(self, config: Config, storage: Storage):
        self.config = config
        self.storage = storage

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # A Telegram deep link (t.me/<bot>?start=alldeals) arrives as
        # /start with a payload. That is what makes "more deals" a real
        # link rather than a command to remember, and it needs no hosting.
        if context.args and context.args[0] == "alldeals":
            await self.all_deals(update, context)
            return
        if context.args and context.args[0] == "chaindeals":
            await self.chain_deals(update, context)
            return
        await update.message.reply_text(
            "היי! פשוט דברו איתי רגיל, בלי פקודות. למשל:\n\n"
            "• *תוסיף 300 גרם פסטרמה* — מוסיף לרשימה עם משקל\n"
            "• *צריך טונה סטארקיסט 4 יחידות* — שומר גם את היצרן\n"
            "• *תוריד את הטונה* — מוריד מהרשימה\n"
            "• *מה יש ברשימה* — הרשימה המלאה והמעודכנת\n"
            "• *כמה עולה קוטג* — מחיר נוכחי בסניף + מבצע אם יש (כולל ₪ לק\"ג)\n"
            "• */cheaper שניצלונים* — יש חלופה זולה יותר ליחידת מידה?\n"
            "• *מה יש במבצע* — מבצעים אמיתיים על מה שאתם קונים\n"
            "• *מתכון לפאי תפוחים* — מפרק למצרכים ומוסיף לרשימה\n"
            "• *תכנן לי תפריט שבועי* — 5 ארוחות + רשימת קניות מאוחדת\n\n"
            "*רשימה מול סל:*\n"
            "• *תוסיף X* — נכנס לרשימה שממתינה למחזור הבא\n"
            "• *תוסיף X לעגלה* — נכנס עכשיו לסל האמיתי בשופרסל\n"
            "• *מלא את העגלה* — מריץ מחזור מלא על כל מה שברשימה\n"
            "• */propose* — הצעה לפי מחלקות: הכל מסומן, מורידים מה שלא צריך\n\n"
            "_תמיד עוצר על סל מוכן — הבדיקה והתשלום נשארים אצלכם._",
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
        # "האם חרדל ב-20 שקל זה טוב?" is a different question from "how much
        # is mustard": it carries a price to judge, and answering it needs
        # the other chains and any promotion, not just a catalogue lookup.
        _, quoted = ask.parse_question(query)
        if quoted is not None:
            verdict = await asyncio.to_thread(
                ask.evaluate, self.storage, query, self._selfpoint_prices
            )
            await update.message.reply_text(
                ask.format_verdict(verdict, history_days=self._price_history_days()),
                parse_mode="Markdown",
            )
            return

        results = await asyncio.to_thread(self.storage.search_with_deals, query, 6)
        await update.message.reply_text(
            format_search_answer(query, results), parse_mode="Markdown"
        )

    @staticmethod
    def _selfpoint_prices(store_key: str):
        from .adapters.selfpoint import SelfPointPrices

        return SelfPointPrices(store_key)

    def _price_history_days(self) -> int:
        """How many days of price history exist, so the answer can be honest
        about whether a trend claim is supportable at all."""
        from contextlib import closing

        with closing(self.storage._connect()) as conn:  # noqa: SLF001
            row = conn.execute("SELECT COUNT(DISTINCT day) AS d FROM price_history").fetchone()
        return int(row["d"] or 0)

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

    async def all_deals(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/alldeals — the long list behind the "more deals" link.

        The nudge carries ten deals because a long message is skimmed and
        then ignored. Everything else lives here, one tap away, so nothing
        found has to be discarded and nothing unwanted has to be read.
        """
        if not _authorized(self.config, update):
            return
        deals = await asyncio.to_thread(hotdeals.find_extended, self.storage)
        await update.message.reply_text(
            hotdeals.format_extended(deals), parse_mode="Markdown"
        )

    async def on_any_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log every callback that reaches the bot. Never answers or replies."""
        query = update.callback_query
        logger.info(
            "callback received: data=%r from=%s",
            getattr(query, "data", None),
            update.effective_user.id if update.effective_user else "?",
        )

    async def on_chain_deals_button(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """The button under /stockup — cross-chain deals, one tap."""
        query = update.callback_query
        logger.info("chaindeals button pressed by %s", update.effective_user.id
                    if update.effective_user else "?")
        if not _authorized(self.config, update):
            logger.warning("chaindeals press refused: user not on the allowlist")
            await query.answer()
            return
        await query.answer()
        relevant, exceptional = await asyncio.to_thread(hotdeals.find, self.storage)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=hotdeals.format_deals(relevant, exceptional)
            or "אין כרגע מבצעים חריגים ברשתות האחרות.",
            parse_mode="Markdown",
        )

    async def chain_deals(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/chaindeals — the same products, priced at every other chain.

        Kept apart from /stockup rather than merged into it. /stockup is
        built from the Shufersal feed and answers "what is unusually cheap
        where I already shop"; this answers "who else is cheaper", which
        is a different question with a different bar. Merging them would
        make one list that answers neither cleanly.
        """
        if not _authorized(self.config, update):
            return
        relevant, exceptional = await asyncio.to_thread(hotdeals.find, self.storage)
        text = hotdeals.format_deals(relevant, exceptional)
        await update.message.reply_text(
            text or "אין כרגע מבצעים חריגים ברשתות האחרות.", parse_mode="Markdown"
        )

    async def refresh_prices(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/refresh_prices — re-download the branch's price + promo snapshot."""
        if not _authorized(self.config, update):
            return
        await update.message.reply_text("🔥 מושך מחירים ומבצעים מהפיד. שנייה.")
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

        # Remember where the household talks to us, so proactive messages
        # (cadence digest, alerts) have somewhere to go.
        self.storage.set_state("digest_chat_id", str(update.effective_chat.id))
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
            "add_to_cart": self._do_add_to_cart,
            "report_waste": self._do_report_waste,
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

    # -- proposal checklists ------------------------------------------------

    async def digest(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/digest — the whole shop in one message, on demand."""
        if not _authorized(self.config, update):
            return
        # Acknowledge before the slow part. Composing scans the catalog for
        # deals and cheaper equivalents and takes a few seconds; without a
        # word the user sees a command vanish into nothing and assumes it
        # broke — which is exactly what happened.
        notice = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🔪 מכין את הדייג'סט — מבצעים, חלופות, מחירים. שנייה.",
        )
        try:
            await self._send_digest(update.effective_chat.id, context)
        finally:
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id, message_id=notice.message_id
                )
            except Exception:
                pass  # leaving the notice is harmless

    async def _send_digest(self, chat_id: int, context) -> None:
        import datetime as _dt

        message, paste = await asyncio.to_thread(compose_digest, self.storage)
        await _send_markdown(context, chat_id, message)
        if paste:
            await context.bot.send_message(chat_id=chat_id, text=paste)
        self.storage.set_state("last_digest_sent", _dt.datetime.now().isoformat())

    async def cadence_check(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Daily: open the conversation when the household is due to order.

        This is the fix for both failure modes the user described — the
        thrown-out food and the surprising empty fridge are timing
        problems, so the digest arrives when their own rhythm says it is
        time, not on an arbitrary weekday.
        """
        chat_id = self.storage.get_state("digest_chat_id")
        if not chat_id:
            return
        due, reason = await asyncio.to_thread(digest_due, self.storage)
        if not due:
            logger.debug("Digest not due: %s", reason)
            return
        logger.info("Digest due: %s", reason)
        await self._send_digest(int(chat_id), context)

    async def nightly_learn(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Nightly: learn from the store's own order history.

        Every order counts, including ones placed entirely by hand in the
        app — so the model stays current with zero effort from the
        household. Quietly skipped when the exit node is asleep; the next
        night catches up, and being a day behind costs nothing.
        """
        status = await asyncio.to_thread(ensure_israeli_exit, self.config.playwright_proxy)
        if not status.available:
            logger.info("Nightly learn skipped: exit node down")
            return
        factories = _build_adapter_factories(self.config)
        make_adapter = factories.get("shufersal")
        if make_adapter is None:
            return

        def _sync():
            with make_adapter() as adapter:
                if not adapter.ensure_session():
                    return None
                return sync_from_orders(self.storage, adapter)

        try:
            report = await asyncio.to_thread(_sync)
            if report:
                logger.info("Nightly learn done: %s", report)
        except Exception:
            logger.exception("Nightly learn failed; will retry tomorrow")


    async def make_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/list_full [core|full|fresh|pantry] — a paste-ready shopping list.

        Deliberately does not touch the cart. Shufersal's own "הזמנה
        מהירה" box takes a newline-separated list and matches it against
        the household's purchase history, so handing over text is both
        instant and leaves every decision with the user — where filling a
        cart item by item costs 10-40s each.
        """
        if not _authorized(self.config, update):
            return
        store = (self.config.enabled_stores or ["shufersal"])[0]
        rows = self.storage.list_stock_items(store)
        if not rows:
            await update.message.reply_text(
                "אין עדיין היסטוריה. הריצו `python -m grocery_bot.cli build-stock`.",
                parse_mode="Markdown",
            )
            return

        wanted = (context.args[0].lower() if context.args else "full")
        specs = {spec.key: spec for spec in available_lists()}
        if wanted not in specs:
            await update.message.reply_text(
                "איזו רשימה?\n"
                "• `/list_full core` — ליבה 35%+\n"
                "• `/list_full full` — מלאה 15%+\n"
                "• `/list_full fresh` — טרי בלבד\n"
                "• `/list_full pantry` — מזווה ובית",
                parse_mode="Markdown",
            )
            return

        spec = build_list(specs[wanted], rows)
        requests = self.storage.list_pending_adhoc()

        summary = summarise(spec)
        if requests:
            # Named separately from the standing list: these are things a
            # person specifically asked for this week, and knowing which
            # of the two asked is exactly what distinguishes them from an
            # item that arrived off the frequency list or a promotion.
            summary += "\n\n*בקשות אישיות שנוספו:*\n" + "\n".join(
                f"• {item.text}" + (f" — 🙋 {item.requested_by}" if item.requested_by else "")
                for item in requests
            )
        await update.message.reply_text(summary, parse_mode="Markdown")

        body = as_paste_text(spec)
        if requests:
            # The paste block stays bare names only — the quick-buy box
            # matches whatever it is given, so a "🙋 לירן" would be looked
            # up as part of a product.
            body += "\n" + "\n".join(item.text for item in requests)
        # Sent as its own bare message so it can be copied in one gesture;
        # anything else in it would be pasted into the box as a product.
        await update.message.reply_text(body)
        note = (
            f"☝️ להעתיק ולהדביק ב*הזמנה מהירה* באפליקציה"
            f"{f' (כולל {len(requests)} בקשות אישיות)' if requests else ''}.\n"
            "שופרסל תתאים מוצרים לפי ההיסטוריה שלכם, ואתם מסננים שם."
        )
        await update.message.reply_text(note, parse_mode="Markdown")


    async def cheaper(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/cheaper <product> — better value for the same kind of thing."""
        if not _authorized(self.config, update):
            return
        query = " ".join(context.args).strip()
        if not query:
            await update.message.reply_text(
                "איזה מוצר להשוות? למשל: /cheaper שניצלונים"
            )
            return
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        # Compare against the product actually bought for this term, when
        # one is remembered — "cheaper than my usual" beats "cheaper than
        # whatever the search ranked first".
        store = (self.config.enabled_stores or ["shufersal"])[0]
        remembered = self.storage.preferred_for(store, query)
        reference, cheaper = await asyncio.to_thread(
            find_cheaper_equivalents,
            self.storage,
            query,
            12,
            remembered["product_name"] if remembered else "",
        )
        await update.message.reply_text(
            format_cheaper_equivalents(reference, cheaper, query), parse_mode="Markdown"
        )


    async def stockup(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Exceptional deals worth buying ahead, even if not needed now."""
        if not _authorized(self.config, update):
            return
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        deals = await asyncio.to_thread(find_stockup_deals, self.storage)
        # The footer names /chaindeals rather than carrying a button:
        # Telegram makes a command tappable by itself, and that needs no
        # callback plumbing to go wrong.
        await update.message.reply_text(
            format_stockup_deals(deals), parse_mode="Markdown"
        )


    async def propose_cycle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Offer what to buy as per-department checklists, pre-ticked."""
        if not _authorized(self.config, update):
            return
        store = (self.config.enabled_stores or ["shufersal"])[0]
        stock = self.storage.list_stock_items(store)
        if not stock:
            await update.message.reply_text(
                "עדיין אין לי מספיק היסטוריה כדי להציע רשימה. "
                "הריצו `python -m grocery_bot.cli import-history` בשרת.",
                parse_mode="Markdown",
            )
            return

        proposed = [row for row in stock if row["tier"] in ("A", "B", "C")]
        items = [
            {
                "store": store,
                "product_code": row["product_code"],
                "product_name": row["product_name"],
                "department": row["department"],
                "quantity": row["default_quantity"],
                "amount": row["amount"],
                "unit": row["unit"],
                "selected": _preticked(row),
            }
            for row in proposed
        ]
        proposal_id = self.storage.create_proposal(update.effective_chat.id, items)
        await update.message.reply_text(
            f"הכנתי הצעה של {len(items)} פריטים ב-{len({i['department'] for i in items})} מחלקות, "
            "לפי מה שאתם קונים בפועל. עברו מחלקה־מחלקה והורידו מה שלא צריך השבוע."
        )
        await self._send_departments(update.effective_chat.id, context, proposal_id)

    async def _send_departments(self, chat_id: int, context, proposal_id: int) -> None:
        """One accordion panel instead of a message per department."""
        text, markup = self._panel(proposal_id, open_index=0)
        message = await context.bot.send_message(
            chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=markup
        )
        # Pin the panel so "where is the process" is always one tap away.
        try:
            await context.bot.pin_chat_message(
                chat_id=chat_id, message_id=message.message_id, disable_notification=True
            )
        except Exception:
            logger.info("Could not pin the proposal panel; continuing")

    def _panel(self, proposal_id: int, open_index: int):
        items = self.storage.proposal_items(proposal_id)
        names = list(dict.fromkeys(item["department"] for item in items))
        open_index = max(0, min(open_index, len(names) - 1))
        grouped = [(n, [i for i in items if i["department"] == n]) for n in names]
        text = render_panel(grouped, open_index)

        buttons = []
        # Department headers, two per row. Index in the callback, never the
        # Hebrew name: callback data is capped at 64 bytes and a long
        # department name blows straight past it (the "dead buttons" bug).
        row = []
        for index, name in enumerate(names):
            label = ("▾ " if index == open_index else "▸ ") + name[:14]
            row.append(
                InlineKeyboardButton(label, callback_data=f"pdept:{proposal_id}:{index}")
            )
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons += _department_buttons(proposal_id, open_index, grouped[open_index][1])
        buttons.append(
            [InlineKeyboardButton("🛒 אישור ומילוי הסל", callback_data=f"pconfirm:{proposal_id}")]
        )
        return text, InlineKeyboardMarkup(buttons)

    async def on_proposal_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        parts = query.data.split(":")
        action, proposal_id = parts[0], int(parts[1])

        if action == "pconfirm":
            await query.answer("ממלא את הסל…")
            await self._confirm_proposal(query, context, proposal_id)
            return

        open_index = int(parts[2]) if len(parts) > 2 else 0
        items = self.storage.proposal_items(proposal_id)
        names = list(dict.fromkeys(item["department"] for item in items))
        department = names[open_index] if open_index < len(names) else ""

        # Every tap gets a toast, so a press is never silent — the earlier
        # run left the user tapping buttons with no sign anything happened.
        if action == "pdept":
            await query.answer(department)
        elif action == "ptoggle":
            now_selected = self.storage.toggle_proposal_item(proposal_id, parts[3])
            await query.answer("סומן ✓" if now_selected else "הוסר ✗")
        elif action == "pall":
            self.storage.set_department_selection(proposal_id, department, True)
            await query.answer("סומן הכל ✓")
        elif action == "pnone":
            self.storage.set_department_selection(proposal_id, department, False)
            await query.answer("נוקה ✗")
        else:
            await query.answer()

        text, markup = self._panel(proposal_id, open_index)
        try:
            await query.edit_message_text(text=text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            # "Message is not modified" when a tap changed nothing visible —
            # harmless, the toast above already acknowledged it.
            logger.debug("Panel edit skipped", exc_info=True)

    async def _confirm_proposal(self, query, context, proposal_id: int) -> None:
        items = self.storage.proposal_items(proposal_id)
        chosen = [item for item in items if item["selected"]]
        if not chosen:
            await query.edit_message_text("לא נבחר אף פריט — לא מילאתי כלום.")
            self.storage.close_proposal(proposal_id, status="empty")
            return

        store = chosen[0]["store"]
        # The ticks are the only signal the purchase history cannot give,
        # since it cannot see what was bought at the other chain.
        self.storage.record_stock_feedback(
            store,
            picked=[i["product_code"] for i in chosen],
            skipped=[i["product_code"] for i in items if not i["selected"]],
        )
        self.storage.close_proposal(proposal_id)
        await query.edit_message_text(f"📝 {len(chosen)} פריטים לסל. מתחיל.")

        factories = _build_adapter_factories(self.config)
        if not factories:
            return
        chat_id = query.message.chat_id
        terms = [(item["product_name"], item["quantity"]) for item in chosen]
        reports = await self._run_terms_with_live_view(chat_id, context, factories, terms)
        if reports is None:
            return
        await self._send_alternatives(chat_id, context, reports)
        await self._ask_ambiguities(chat_id, context)

    async def _do_add_to_cart(self, update, context, parsed, requested_by: str) -> None:
        """Add named items to the real cart now, not just to the list."""
        if not parsed.items:
            # "update the cart" with nothing named means the whole list.
            await self.start_order(update, context)
            return
        if not _authorized(self.config, update):
            return

        factories = _build_adapter_factories(self.config)
        if not factories:
            await update.message.reply_text("אין אף רשת מוגדרת/מיושמת.")
            return

        status = await asyncio.to_thread(ensure_israeli_exit, self.config.playwright_proxy)
        if not status.available:
            # Keep them rather than lose them: they go on the list and the
            # next cycle picks them up.
            for item in parsed.items:
                self.storage.add_adhoc_request(
                    text=item.name, requested_by=requested_by,
                    amount=item.amount, unit=item.unit, brand=item.brand,
                )
            await update.message.reply_text(
                f"🕒 אין כרגע חיבור לשופרסל ({status.detail}).\n"
                "הוספתי אותם לרשימה — הם ייכנסו לסל אוטומטית כשהחיבור יחזור."
            )
            return

        terms = [(item.name, int(item.amount or 1)) for item in parsed.items]
        names = ", ".join(item.name for item in parsed.items)
        await update.message.reply_text(f"🔥 ממלא את הסל: {names}. לא זז מפה.")
        try:
            reports = await asyncio.to_thread(
                add_terms_to_cart, self.storage, factories, terms
            )
        except Exception:
            logger.exception("add_to_cart failed")
            await update.message.reply_text("ההוספה לסל נכשלה — בדקו את הלוגים.")
            return

        await update.message.reply_text(
            format_report_summary(reports) or "לא היה מה להוסיף.", parse_mode="Markdown"
        )
        cart = await asyncio.to_thread(self._read_cart, factories)
        results = [r for report in reports.values() for r in report.results]
        await self._send_cart_state(update.effective_chat.id, context, results, cart)
        await self._send_pending_ambiguities(update, context)

    async def _send_cart_state(self, chat_id, context, results, cart) -> None:
        buttons = [[InlineKeyboardButton("🛒 פתיחת הסל בשופרסל", url=SHUFERSAL_CART_URL)]]
        await context.bot.send_message(
            chat_id=chat_id,
            text=render_final(results, cart),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        await self._send_threshold_check(chat_id, context, results, cart)
        await self._send_card_prompt(chat_id, context)

    async def _send_card_prompt(self, chat_id, context) -> None:
        """Ask about the ₪700 card at the moment it can still be loaded.

        The same question also rides on the six-day nudge, but those are
        two different clocks: the card allowance is monthly and the nudge
        fires on shopping cadence, so a month where the household shops
        often can pass with the question arriving late. This is not a
        second proactive message — it appears inside a hand-off they
        started themselves, at the one moment it is most actionable:
        after the cart is built and before they go and pay.

        A button rather than a parsed reply, so nothing has to infer that
        an incoming message was meant as an answer to this.
        """
        try:
            prompt = await asyncio.to_thread(cardreminder.decide, self.storage)
            if not prompt.should_ask:
                return
            await context.bot.send_message(
                chat_id=chat_id,
                text=prompt.text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("✅ הטענתי", callback_data="cardok")]]
                ),
            )
        except Exception:
            logger.exception("Card prompt failed; cart hand-off unaffected")

    async def on_card_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """One tap records the load and ends the question for this month."""
        query = update.callback_query
        if not _authorized(self.config, update):
            await query.answer()
            return
        await asyncio.to_thread(cardreminder.confirm, self.storage)
        await query.answer("נרשם")
        await query.edit_message_text(
            "✅ *רשמתי שהטענת את הכרטיס החודש* — לא אשאל שוב עד החודש הבא.",
            parse_mode="Markdown",
        )

    async def _send_threshold_check(self, chat_id, context, results, cart) -> None:
        """The last chance to catch a missed threshold or a one-short deal.

        Sent after the cart view and before the household goes to pay,
        because that is the only moment the advice can still be acted on.
        The ₪599 gift was missed on both of the last two orders, each time
        while multi-buy offers sat one unit short in the same basket.
        """
        total = (cart or {}).get("total")
        if not total:
            return
        quantities: dict[str, float] = {}
        for r in results:
            if r.status != "added":
                continue
            code = str(getattr(r, "product_code", "") or "").removeprefix("P_")
            if code:
                quantities[code] = float(getattr(r, "quantity", 1) or 1)
        if not quantities:
            return
        try:
            result = await asyncio.to_thread(
                threshold.check,
                self.storage,
                float(total),
                list(quantities),
                None,
                "מתנה לבחירה",
                quantities,
            )
            if not (result.upsells or result.worth_chasing):
                return
            await context.bot.send_message(
                chat_id=chat_id,
                text=threshold.format_check(result),
                parse_mode="Markdown",
            )
        except Exception:
            # Never let an advisory step break the hand-off: the cart is
            # already built and the household needs the link above.
            logger.exception("Threshold check failed; cart hand-off unaffected")

    async def _do_report_waste(self, update, context, parsed, requested_by: str) -> None:
        """Record what was thrown away. Never comments on the waste itself.

        The household agreed to report this only because it costs one line
        of free text; a reply that moralises would end the supply of data
        immediately.
        """
        raw = update.message.text or ""
        items = [
            (item.name, waste.fraction_for(f"{item.name} {raw}"))
            for item in parsed.items
            if item.name
        ]
        if items:
            await asyncio.to_thread(
                waste.record, self.storage, items, requested_by
            )
        await update.message.reply_text(
            waste.acknowledge(items), parse_mode="Markdown"
        )

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
        removed, suppressed, missing = [], [], []
        for item in parsed.items:
            # Ad-hoc first: a just-added request is the likelier target of
            # "תוריד את X" than a long-standing base-list entry.
            hit = self.storage.remove_adhoc_by_name(item.name)
            if hit is None:
                hit = self.storage.deactivate_base_item_by_name(item.name)
            if hit:
                removed.append(hit)
                continue
            # Third drawer: a product the bot proposes on its own, learned
            # from order history and never typed onto either list. Before
            # this, "תוריד סימילאק" answered "not on the list" about an
            # item plainly visible in the proposal the user was reading.
            hit = self.storage.suppress_stock_item_by_name(item.name)
            (suppressed if hit else missing).append(hit or item.name)
        parts = []
        if removed:
            parts.append("הורדתי: " + ", ".join(removed))
        if suppressed:
            # Say which list it came off, so "removed" is not mistaken for
            # a change to the standing list the user maintains by hand.
            parts.append("לא אציע יותר: " + ", ".join(suppressed))
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
        await update.message.reply_text(f"🧑\u200d🍳 בונה רשימת מצרכים ל{dish}. שנייה, אני על זה.")
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        recipe = await asyncio.to_thread(expand_recipe, dish)
        if recipe is None:
            await update.message.reply_text(f"לא הצלחתי לבנות מצרכים ל'{dish}'. נסו לנסח אחרת.")
            return
        await self._preview_ingredients(
            update.effective_chat.id, context, recipe.dish, recipe.ingredients,
            requested_by, note=recipe.note,
        )

    async def _preview_ingredients(
        self, chat_id, context, dish, ingredients, requested_by, note=""
    ) -> None:
        """Show what a recipe needs and let the user decide — nothing is
        added yet.

        Two pieces of judgment the flat version lacked, both requested
        after real use: the user asked for an approval step ("לאישורי ואז
        תוסיף") — a recipe is speculative in a way the standing list is
        not — and a breakdown that blindly adds flour and sugar to a
        household that obviously owns flour and sugar creates the exact
        delete-by-hand chore this project exists to remove. So the
        preview marks what they probably have, and the default action
        adds only what is probably missing.
        """
        import json as _json
        import uuid as _uuid

        missing, have = await asyncio.to_thread(split_ingredients, self.storage, ingredients)

        token = _uuid.uuid4().hex[:8]
        self.storage.set_state(
            f"recipe_{token}",
            _json.dumps(
                {
                    "dish": dish,
                    "by": requested_by,
                    "missing": [
                        {"name": i.name, "amount": i.amount, "unit": i.unit} for i in missing
                    ],
                    "have": [
                        {"name": i.name, "amount": i.amount, "unit": i.unit} for i in have
                    ],
                },
                ensure_ascii=False,
            ),
        )

        from .mdtext import escape as _md

        lines = [f"*{_md(dish)}* — {len(ingredients)} מצרכים:", ""]
        if missing:
            lines.append("*כנראה צריך לקנות:*")
            lines += [f"🛒 {_md(_describe_parsed(i))}" for i in missing]
        if have:
            lines.append("")
            lines.append("*כנראה יש לכם (לפי הרגלי הקנייה):*")
            lines += [f"✔️ {_md(_describe_parsed(i))}" for i in have]
        if note:
            lines.append(f"\n_{_md(note)}_")
        lines.append("\n_כלום עוד לא נוסף — בחרו:_")

        buttons = [[InlineKeyboardButton(f"🛒 הוסף רק מה שחסר ({len(missing)})",
                                         callback_data=f"rcpmiss:{token}")]]
        buttons.append([
            InlineKeyboardButton("הוסף הכל", callback_data=f"rcpall:{token}"),
            InlineKeyboardButton("ביטול", callback_data=f"rcpno:{token}"),
        ])
        await context.bot.send_message(
            chat_id=chat_id, text="\n".join(lines), parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def on_recipe_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        import json as _json

        query = update.callback_query
        action, token = query.data.split(":")
        raw = self.storage.get_state(f"recipe_{token}")
        if not raw:
            await query.answer("פג תוקף — בקשו את המתכון שוב")
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            return
        payload = _json.loads(raw)

        if action == "rcpno":
            await query.answer("בוטל")
            self.storage.set_state(f"recipe_{token}", "")
            await query.edit_message_text(f"'{payload['dish']}' — בוטל, כלום לא נוסף.")
            return

        chosen = payload["missing"] + (payload["have"] if action == "rcpall" else [])
        await query.answer(f"מוסיף {len(chosen)} מצרכים…")
        for item in chosen:
            self.storage.add_adhoc_request(
                text=item["name"],
                requested_by=f"{payload['by']} (מתכון: {payload['dish']})",
                amount=item.get("amount"),
                unit=item.get("unit") or "",
            )
        self.storage.set_state(f"recipe_{token}", "")

        from .mdtext import escape as _md

        names = ", ".join(_md(item["name"]) for item in chosen)
        skipped = len(payload["missing"]) + len(payload["have"]) - len(chosen)
        suffix = f"\n_({skipped} דילגנו — כנראה יש לכם)_" if skipped else ""
        try:
            await query.edit_message_text(
                f"*{_md(payload['dish'])}* — נוספו {len(chosen)} לרשימה:\n{names}{suffix}",
                parse_mode="Markdown",
            )
        except Exception:
            logger.debug("recipe edit failed", exc_info=True)

    async def _do_meal_plan(self, update, context, parsed, requested_by: str) -> None:
        await update.message.reply_text("🍳 בונה תפריט שבועי ורשימת קניות. כמה שניות, לא זז.")
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        store = (self.config.enabled_stores or ["shufersal"])[0]
        staples = [
            row["product_name"]
            for row in self.storage.list_stock_items(store)
            if row["tier"] in ("A", "B", "C")
        ]
        # Standing household context (who the food is for) rides along on
        # every plan, so "תפריט שבועי" alone already knows about the kids
        # instead of needing the ages restated each time.
        household = self.storage.get_state("household_context")
        request = (parsed.query or "").strip()
        if household:
            request = f"{request}. הרכב המשפחה: {household}" if request else f"הרכב המשפחה: {household}"
        plan = await asyncio.to_thread(build_meal_plan, request, staples)
        if plan is None:
            await update.message.reply_text("לא הצלחתי לבנות תפריט הפעם. נסו שוב עוד רגע.")
            return

        from .mdtext import escape as _md

        lines = ["*תפריט שבועי:*"]
        lines += [
            f"• {_md(day)}: {_md(dish)}" if day else f"• {_md(dish)}"
            for day, dish in plan.meals
        ]
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        # Ingredients go through the same look-what-you-need preview as a
        # single recipe: same approval, same probably-have marking.
        await self._preview_ingredients(
            update.effective_chat.id, context,
            "תפריט שבועי", plan.ingredients, requested_by, note=plan.note,
        )
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
        # Tries the current exit node, then any other that can reach Israel:
        # the household has several devices offering themselves, and
        # Tailscale never switches between them on its own.
        status = await asyncio.to_thread(ensure_israeli_exit, self.config.playwright_proxy)
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
            chat_id=chat_id,
            text="🍳 *מאתר את כל המוצרים ברשת — כ-40 שניות, ואז מתחיל למלא.*",
            parse_mode="Markdown",
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

    async def _run_terms_with_live_view(self, chat_id, context, factories, terms):
        """Fill the cart with an explicit list, showing the same live view."""
        loop = asyncio.get_running_loop()
        view = await context.bot.send_message(
            chat_id=chat_id,
            text="🍳 *מאתר את כל המוצרים ברשת — כ-40 שניות, ואז מתחיל למלא.*",
            parse_mode="Markdown",
        )
        collected: list = []
        last_edit = 0.0

        async def _redraw(text: str) -> None:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=view.message_id, text=text, parse_mode="Markdown"
                )
            except Exception:
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
                add_terms_to_cart, self.storage, factories, terms, _on_progress
            )
        except Exception:
            logger.exception("Filling the cart from a proposal failed")
            await _redraw("🛑 המילוי נכשל — בדקו את הלוגים בשרת.")
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
        # Stock-up deals belong here, not buried in an earlier message:
        # this is the moment before the user opens the store and pays,
        # which is the only moment "worth grabbing while you are there"
        # can still be acted on.
        await self._send_stockup_hint(chat_id, context)

    async def _send_stockup_hint(self, chat_id, context) -> None:
        try:
            deals = await asyncio.to_thread(find_stockup_deals, self.storage)
        except Exception:
            logger.debug("stock-up hint failed", exc_info=True)
            return
        if not deals:
            return
        from .mdtext import escape as _md

        lines = ["📦 *לפני שמשלמים — שווה לאגור:*"]
        for deal in deals[:3]:
            mark = "🧺 " if deal.pantryable else ""
            lines.append(
                f"• {mark}{_md(deal.catalog_name)} — *{deal.deal_price:.2f}₪* "
                f"(במקום {deal.shelf_price:.2f}) −{deal.discount * 100:.0f}%"
            )
        lines.append("")
        lines.append("_לא נוסף לסל — להוסיף באפליקציה אם רלוונטי._")
        await _send_markdown(context, chat_id, "\n".join(lines))

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
        """Variant chooser — multi-select by design.

        A tap adds that variant to the cart immediately and the question
        STAYS OPEN, because a household genuinely buys both the 5% and the
        3% cottage cheese in one shop. "סיום" closes it. Immediate add
        (rather than tick-then-confirm) keeps a tap meaningful on its own
        and gives instant feedback -- the earlier flow left the user
        pressing buttons with nothing visibly happening.
        """
        query = update.callback_query
        action, ambiguity_id_str, choice_str = query.data.split(":")
        ambiguity_id = int(ambiguity_id_str)

        # Acknowledge before touching storage or the network: a callback
        # query expires ~15s after the tap, and anything slower than that
        # leaves the button looking dead.
        try:
            await query.answer("רגע…")
        except Exception:
            logger.debug("Callback already expired", exc_info=True)

        pending = self.storage.get_pending_ambiguity(ambiguity_id)
        if pending is None:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            return

        if action == "skip":
            self.storage.mark_ambiguity_resolved(ambiguity_id)
            await query.edit_message_text(f"'{pending['original_term']}' — טופל.")
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
            await query.answer()
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
        if result.status != "added":
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"לא הצלחתי להוסיף את '{chosen_label}' (status: {result.status}).",
            )
            return

        # The first pick becomes the remembered default for this term; a
        # second pick in the same question is an addition, not a
        # correction, so it must not overwrite the default.
        if self.storage.preferred_for(pending["store"], pending["original_term"]) is None:
            self.storage.remember_choice(
                store=pending["store"],
                term=pending["original_term"],
                product_code=chosen_code or getattr(result, "product_code", "") or "",
                product_name=chosen_label,
            )

        added = _added_marks(query.message.text or "") | {chosen_label}
        header = f"*{pending['original_term']}* — איזה מהם?"
        lines = [header, ""]
        from .disambiguate import describe_card

        remaining = []
        for position, card in enumerate(cards[:5]):
            if card.get("name") in added:
                # A chosen option turns into a confirmation line and loses
                # its button: the user asked for a press to visibly do
                # something, and a button that stays put looks ignored.
                lines.append(f"✅ *{describe_card(card)}* — נוסף לסל")
            else:
                lines.append(f"{len(remaining) + 1}. {describe_card(card)}")
                remaining.append((len(remaining) + 1, position))

        if remaining:
            lines += ["", "_אפשר לבחור עוד, או 'סיום'._"]
            buttons = [
                [
                    InlineKeyboardButton(
                        str(label), callback_data=f"resolve:{ambiguity_id}:{position}"
                    )
                    for label, position in remaining
                ],
                [InlineKeyboardButton("סיום ✓", callback_data=f"skip:{ambiguity_id}:0")],
            ]
            markup = InlineKeyboardMarkup(buttons)
        else:
            # Nothing left to choose: close the question rather than leave
            # a dead keyboard behind.
            lines += ["", "_הכול נבחר._"]
            markup = None
            self.storage.mark_ambiguity_resolved(ambiguity_id)

        try:
            await query.edit_message_text(
                "\n".join(lines), parse_mode="Markdown", reply_markup=markup
            )
        except Exception:
            logger.debug("Choice edit skipped", exc_info=True)

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

        # Tries the current exit node, then any other that can reach Israel:
        # the household has several devices offering themselves, and
        # Tailscale never switches between them on its own.
        status = await asyncio.to_thread(ensure_israeli_exit, self.config.playwright_proxy)
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
            BotCommand("propose", "הצעת קנייה לפי מחלקות — מסמנים מה צריך"),
            BotCommand("stockup", "שווה לאגור — מבצעים חריגים לקנייה מראש"),
            BotCommand("chaindeals", "מבצעים מכל הרשתות, לא רק שופרסל"),
            BotCommand("cheaper", "השוואת ₪ לק\"ג — יש חלופה זולה יותר?"),
            BotCommand("list_full", "רשימה להדבקה בהזמנה מהירה"),
            BotCommand("digest", "כל הקנייה בהודעה אחת — רשימה, מבצעים, חלופות"),
            BotCommand("start_order", "מילוי מהיר של כל הרשימה"),
        ]
    )
    await application.bot.set_my_description(
        "בוט קניות משפחתי — מדברים איתו רגיל בעברית. מוסיף לרשימה, בודק "
        "מחירים ומבצעים אמיתיים בסניף, מפרק מתכונים למצרכים ובונה תפריט שבועי."
    )


async def _send_markdown(context, chat_id: int, text: str, **kwargs):
    """Send Markdown, falling back to plain text if Telegram rejects it.

    A single stray character in a product name — Israeli multipacks are
    written "6*330 מ\"ל", and 349 of this branch's products contain one —
    makes Telegram reject the WHOLE message with "can't find end of the
    entity". The user then sees nothing at all, with no clue why. Escaping
    is handled at composition (see mdtext), but this is the backstop: a
    formatting problem should cost formatting, never the content.
    """
    try:
        return await context.bot.send_message(
            chat_id=chat_id, text=text, parse_mode="Markdown", **kwargs
        )
    except Exception:
        logger.warning("Markdown rejected; resending as plain text", exc_info=True)
        plain = text.replace("*", "").replace("_", "").replace("`", "")
        return await context.bot.send_message(chat_id=chat_id, text=plain, **kwargs)


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Keep expected Telegram complaints out of the traceback stream."""
    from telegram.error import BadRequest

    error = context.error
    if isinstance(error, BadRequest) and (
        "Query is too old" in str(error) or "not modified" in str(error)
    ):
        # A tap that arrived after its query expired, or an edit that
        # changed nothing. Neither is actionable.
        logger.debug("Ignoring benign Telegram error: %s", error)
        return
    logger.exception("Unhandled error while processing an update", exc_info=error)


def build_application(config: Config, storage: Storage) -> Application:
    bot = GroceryBot(config, storage)
    # concurrent_updates matters more than it looks: python-telegram-bot
    # processes updates one at a time by default, so while a cart add runs
    # for 10-40s every button tap queues behind it. Telegram invalidates a
    # callback query after ~15s, so those taps arrived expired -- no toast,
    # no edit, nothing. That is the whole "I press buttons and nothing
    # happens" report.
    application = (
        Application.builder()
        .token(config.telegram_bot_token)
        .post_init(_register_bot_metadata)
        .concurrent_updates(True)
        .build()
    )
    application.add_error_handler(_on_error)
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("start_order", bot.start_order))
    application.add_handler(CommandHandler("list", bot.list_base_items))
    application.add_handler(CommandHandler("price", bot.price))
    application.add_handler(CommandHandler("deals", bot.deals))
    application.add_handler(CommandHandler("alldeals", bot.all_deals))
    application.add_handler(CommandHandler("chaindeals", bot.chain_deals))
    application.add_handler(CommandHandler("refresh_prices", bot.refresh_prices))
    application.add_handler(CommandHandler("propose", bot.propose_cycle))
    application.add_handler(CommandHandler("stockup", bot.stockup))
    application.add_handler(CommandHandler("cheaper", bot.cheaper))
    application.add_handler(CommandHandler("list_full", bot.make_list))
    application.add_handler(CommandHandler("digest", bot.digest))
    application.add_handler(CallbackQueryHandler(bot.resolve_ambiguity, pattern=r"^(resolve|skip):"))
    application.add_handler(
        CallbackQueryHandler(bot.on_proposal_button, pattern=r"^(ptoggle|pall|pnone|pconfirm):")
    )
    application.add_handler(
        CallbackQueryHandler(bot.on_recipe_button, pattern=r"^(rcpall|rcpmiss|rcpno):")
    )
    application.add_handler(
        CallbackQueryHandler(bot.on_chain_deals_button, pattern=r"^chaindeals$")
    )
    application.add_handler(
        CallbackQueryHandler(bot.on_card_button, pattern=r"^cardok$")
    )
    # Group 1 runs in addition to group 0, so this sees every callback
    # whether or not a real handler matched. Without it, "the button does
    # nothing" is indistinguishable from "the press never arrived", and
    # that ambiguity has already cost two rounds of guessing.
    application.add_handler(
        CallbackQueryHandler(bot.on_any_callback), group=1
    )
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_message))
    if application.job_queue is not None:
        application.job_queue.run_repeating(
            bot.drain_deferred_cycle,
            interval=EXIT_POLL_SECONDS,
            first=EXIT_POLL_SECONDS,
            name="drain_deferred_cycle",
        )
        import datetime as _dt
        import zoneinfo as _zi

        israel = _zi.ZoneInfo("Asia/Jerusalem")
        # Early evening: late enough that the day's plans are known, early
        # enough to order before the delivery slots fill.
        application.job_queue.run_daily(
            bot.cadence_check, time=_dt.time(18, 40, tzinfo=israel), name="cadence_check"
        )
        # Long after midnight: the store's order history has settled and
        # nobody is shopping. Failure just waits for tomorrow.
        application.job_queue.run_daily(
            bot.nightly_learn, time=_dt.time(3, 40, tzinfo=israel), name="nightly_learn"
        )
    else:  # pragma: no cover - depends on optional PTB extra
        logger.warning(
            "JobQueue unavailable: a cycle requested while the exit node is down "
            "will stay queued until /start_order is sent again."
        )
    return application
