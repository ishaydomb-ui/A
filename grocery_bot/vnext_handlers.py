"""Telegram handlers for the vNext flow — Phase 2b.

Kept out of `telegram_bot.py` (already 3,300 lines) as a companion
object: `GroceryBot` owns one `VNextFlow` and delegates `/plan`, the
`vn:` callbacks, the nudge and the draft-aware free-text edits to it.
Everything here renders through `vnext_flow`'s pure screens and talks
to Telegram with plain text (no parse mode).

Assisted mode, by construction:
- a draft never touches a cart; the ONLY path into the cart engine is
  `_execute`, reached from the "אשר והכן עגלה/עגלות" button, and it
  calls `orchestrator.add_terms_to_cart(..., guard_cart=True)` so lines
  a person deleted are not put back and lines already there are skipped;
- `cartpause` is honoured by the engine itself;
- "עבור לתשלום" is a URL button to the chain's own cart page. There is
  no handler in this file, or anywhere, that reaches checkout or payment
  (`planner.FORBIDDEN`, the hard rule in CLAUDE.md).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from . import vnext_confirmations, vnext_flow
from .vnext_config import VNextConfig

logger = logging.getLogger(__name__)

DRAFT_INTENTS = ("add_item", "remove_item", "change_quantity", "replace_item")


def _keyboard(rows: list) -> InlineKeyboardMarkup | None:
    if not rows:
        return None
    out = []
    for row in rows:
        buttons = []
        for label, target in row:
            if isinstance(target, tuple) and target and target[0] == vnext_flow.KEY_URL:
                buttons.append(InlineKeyboardButton(label, url=target[1]))
            else:
                buttons.append(InlineKeyboardButton(label, callback_data=str(target)[:64]))
        out.append(buttons)
    return InlineKeyboardMarkup(out)


class VNextFlow:
    def __init__(self, bot):
        self.bot = bot

    @property
    def storage(self):
        return self.bot.storage

    @property
    def config(self) -> VNextConfig:
        return VNextConfig.from_env()

    # -- helpers -----------------------------------------------------------------

    def open_draft(self, chat_id: int) -> vnext_flow.Draft | None:
        return vnext_flow.load_draft(self.storage, chat_id, self.config)

    async def _show(self, context, chat_id: int, draft: vnext_flow.Draft, text: str, rows: list,
                    query=None, new_message: bool = False):
        """Edit the draft's message in place; send a fresh one when there is
        none, when editing fails, or when asked."""
        markup = _keyboard(rows)
        if query is not None and not new_message:
            try:
                await query.edit_message_text(text, reply_markup=markup)
                draft.message_id = query.message.message_id if query.message else draft.message_id
                return
            except Exception:  # noqa: BLE001
                logger.debug("vNext edit failed; sending fresh", exc_info=True)
        sent = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
        draft.message_id = sent.message_id

    def _save(self, draft: vnext_flow.Draft, **fields) -> None:
        mid = draft.message_id if isinstance(draft.message_id, int) else None
        vnext_flow.save_draft(self.storage, draft, message_id=mid, **fields)

    async def _build_plan(self):
        from .shopping_plan import build_plan
        return await asyncio.to_thread(build_plan, self.storage, self.config)

    async def _build_draft(self, chat_id: int, important_only: bool = False, plan=None) -> vnext_flow.Draft:
        plan = plan or await self._build_plan()
        stores = list(self.bot.config.enabled_stores or ["shufersal", "tivtaam"])
        return await asyncio.to_thread(
            vnext_flow.new_draft, self.storage, chat_id, plan, self.config, important_only, stores,
        )

    # -- entry points ------------------------------------------------------------

    async def start_proposal(self, update, context, important_only: bool = False, query=None) -> None:
        """/plan, the nudge's "כן", or the spoken start_order with no draft open."""
        chat_id = update.effective_chat.id
        existing = self.open_draft(chat_id)
        if existing is not None and existing.status in ("draft", "confirmed", "executing"):
            text, rows = vnext_flow.proposal_screen(existing)
            existing.screen = "proposal"
            await self._show(context, chat_id, existing, text, rows, query=query, new_message=query is None)
            self._save(existing)
            return
        notice_text = "מכין הצעת קנייה… (רק הצעה — לא נוגע בעגלה)"
        if query is not None:
            try:
                await query.edit_message_text(notice_text)
                notice = query.message
            except Exception:  # noqa: BLE001
                notice = await context.bot.send_message(chat_id=chat_id, text=notice_text)
        else:
            notice = await context.bot.send_message(chat_id=chat_id, text=notice_text)
        try:
            draft = await self._build_draft(chat_id, important_only)
        except Exception:
            logger.exception("vNext proposal failed")
            await context.bot.edit_message_text(chat_id=chat_id, message_id=notice.message_id,
                                                text="לא הצלחתי להכין הצעה עכשיו.")
            return
        text, rows = vnext_flow.proposal_screen(draft)
        try:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=notice.message_id, text=text,
                                                reply_markup=_keyboard(rows))
            draft.message_id = notice.message_id
        except Exception:  # noqa: BLE001
            await self._show(context, chat_id, draft, text, rows, new_message=True)
        self._save(draft)
        await self._send_cards(context, chat_id, draft)

    async def _send_cards(self, context, chat_id: int, draft: vnext_flow.Draft) -> None:
        for card in (vnext_flow.waste_card(draft, self.storage, self.config), vnext_flow.stockup_card(draft)):
            if card is None:
                continue
            text, rows = card
            try:
                await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=_keyboard(rows))
            except Exception:  # noqa: BLE001
                logger.debug("vNext card send failed", exc_info=True)

    async def maybe_nudge(self, context, chat_id: int) -> bool:
        """From cadence_check. True when a nudge was sent."""
        from .shopping_readiness import assess

        config = self.config
        try:
            r = await asyncio.to_thread(assess, self.storage, config)
        except Exception:
            logger.exception("vNext readiness for the nudge failed")
            return False
        current = self.open_draft(chat_id)
        open_draft = current is not None and current.status in ("draft", "confirmed", "executing")
        why_not = vnext_flow.nudge_suppression(self.storage, r, config, open_draft=open_draft)
        if why_not:
            logger.info("vNext nudge suppressed: %s", why_not)
            return False
        text, rows = vnext_flow.nudge_screen(r)
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=_keyboard(rows))
        vnext_flow.note_nudge_sent(self.storage)
        logger.info("vNext nudge sent (readiness %.2f %s)", r.score, r.suggested_action)
        return True

    # -- free text while a draft is open ------------------------------------------

    async def handle_text(self, update, context, parsed, requested_by: str) -> bool:
        """Apply a spoken edit to the open draft. False = not handled."""
        chat_id = update.effective_chat.id
        draft = self.open_draft(chat_id)
        if draft is None or draft.status not in ("draft", "confirmed"):
            return False
        if parsed.intent == "start_order":
            await self._go(update, context, draft, query=None)
            return True
        if parsed.intent not in DRAFT_INTENTS:
            return False
        names = [i.name for i in (parsed.items or []) if getattr(i, "name", "")]
        amount = next((i.amount for i in (parsed.items or []) if getattr(i, "amount", None)), None)
        replies = []
        if parsed.intent == "add_item":
            for name in names:
                # Still a real request for the household's list (as before).
                try:
                    self.storage.add_adhoc_request(text=name, requested_by=requested_by,
                                                   quantity=int(amount) if amount else 1)
                except Exception:  # noqa: BLE001
                    logger.debug("adhoc add failed", exc_info=True)
                draft.add_item(name, float(amount or 1))
                replies.append(f"✅ הוספתי להצעה: {name}")
        elif parsed.intent == "remove_item":
            for name in names:
                item = self._match_item(draft, name)
                if item is None:
                    replies.append(f"לא מצאתי בהצעה: {name}")
                    continue
                item["included"] = False
                replies.append(f"הסרתי מההצעה: {item['display_name']}")
        elif parsed.intent == "change_quantity":
            from . import convo
            subject = names[0] if names else (convo.recall(self.storage).get("subject") or "")
            item = self._match_item(draft, subject) if subject else None
            if item is None or not amount:
                replies.append("כמה, ושל מה? (למשל: 'קוטג — שניים')")
            else:
                item["quantity"] = float(amount)
                item["included"] = True
                replies.append(f"✅ {item['display_name']} — {float(amount):g}")
        elif parsed.intent == "replace_item":
            from . import convo
            old = convo.recall(self.storage).get("subject") or ""
            new = names[0] if names else ""
            if not new:
                replies.append("להחליף למה?")
            else:
                item = self._match_item(draft, old) if old else None
                if item is not None:
                    item["included"] = False
                    for store, prod in (item.get("products") or {}).items():
                        if prod.get("product_code"):
                            vnext_confirmations.note_interaction(
                                self.storage, item["term"], prod["product_code"], store, "later_correction",
                                product_name=prod.get("product_name", ""), confirmed_by=requested_by,
                                note="replaced in the vNext draft")
                draft.add_item(new, float(amount or 1))
                replies.append(f"✅ במקום {old or 'זה'}: {new}" if old else f"✅ הוספתי: {new}")
        self._save(draft)
        await update.message.reply_text("\n".join(replies) + "\n\n(ההצעה עודכנה — לחץ 'אשר והכן עגלה' כשמוכן)")
        return True

    async def handle_awaited_text(self, update, context, text: str, requested_by: str) -> bool:
        """The reply to "הוסף פריט": the raw text is the item, no NLU call."""
        chat_id = update.effective_chat.id
        draft = self.open_draft(chat_id)
        if draft is None or draft.awaiting != "add_item":
            return False
        name = text.strip()
        draft.awaiting = ""
        if not name:
            self._save(draft)
            return True
        try:
            self.storage.add_adhoc_request(text=name, requested_by=requested_by)
        except Exception:  # noqa: BLE001
            logger.debug("adhoc add failed", exc_info=True)
        draft.add_item(name)
        draft.screen = "review"
        self._save(draft)
        text_out, rows = vnext_flow.review_screen(draft, self.config)
        await update.message.reply_text(f"✅ הוספתי: {name}")
        await self._show(context, chat_id, draft, text_out, rows, new_message=True)
        self._save(draft)
        return True

    def _match_item(self, draft: vnext_flow.Draft, name: str) -> dict | None:
        from .storage import normalize_term
        key = normalize_term(name or "")
        if not key:
            return None
        for item in draft.items:
            if item.get("removed"):
                continue
            if normalize_term(item["term"]) == key or key in normalize_term(item["display_name"]) \
                    or normalize_term(item["term"]) in key:
                return item
        return None

    # -- the callback router ----------------------------------------------------------

    async def on_callback(self, update, context) -> None:
        query = update.callback_query
        parts = (query.data or "").split(":")
        if len(parts) < 2 or parts[0] != vnext_flow.CB:
            return
        action, args = parts[1], parts[2:]
        chat_id = update.effective_chat.id
        try:
            await query.answer()
        except Exception:  # noqa: BLE001
            pass

        if action == "nudge":
            return await self._on_nudge(update, context, query, args[0] if args else "yes")

        draft = self.open_draft(chat_id)
        if draft is None:
            if action == "cancel":
                return
            await query.edit_message_text("ההצעה הזו כבר לא פתוחה — /plan להצעה חדשה.")
            return
        handler = {
            "proposal": self._on_proposal, "list": self._on_list, "edit": self._on_edit, "item": self._on_item,
            "rm": self._on_rm, "qty": self._on_qty, "inc": self._on_inc, "keep": self._on_keep, "sub": self._on_sub,
            "alt": self._on_alt, "altok": self._on_altok, "add": self._on_add, "compare": self._on_compare,
            "chain": self._on_chain, "detail": self._on_detail, "go": self._on_go, "done": self._on_done,
            "links": self._on_links, "waste": self._on_waste, "su": self._on_stockup, "cancel": self._on_cancel,
        }.get(action)
        if handler is None:
            return
        await handler(update, context, query, draft, args)

    async def _on_nudge(self, update, context, query, choice: str) -> None:
        chat_id = update.effective_chat.id
        if choice in ("yes", "important"):
            await self.start_proposal(update, context, important_only=(choice == "important"), query=query)
        elif choice == "later":
            vnext_flow.snooze_nudge(self.storage, self.config)
            await query.edit_message_text("בסדר, אזכיר בעוד יומיים. /plan אם תרצה קודם.")
        elif choice == "items":
            plan = await self._build_plan()
            text = vnext_flow.nudge_items_text(plan)
            rows = [[("כן, תכין הצעה", vnext_flow.cb("nudge", "yes"))], [("לא עכשיו", vnext_flow.cb("nudge", "later"))]]
            await query.edit_message_text(text, reply_markup=_keyboard(rows))

    async def _on_proposal(self, update, context, query, draft, args) -> None:
        draft.screen = "proposal"
        text, rows = vnext_flow.proposal_screen(draft)
        await self._show(context, draft.chat_id, draft, text, rows, query=query)
        self._save(draft)

    async def _on_list(self, update, context, query, draft, args) -> None:
        draft.page = int(args[0]) if args else 0
        draft.screen = "list"
        text, rows = vnext_flow.review_screen(draft, self.config, full=True)
        await self._show(context, draft.chat_id, draft, text, rows, query=query)
        self._save(draft)

    async def _on_edit(self, update, context, query, draft, args) -> None:
        draft.page = int(args[0]) if args else 0
        draft.screen = "review"
        if draft.status == "done":
            draft.status = "draft"
        text, rows = vnext_flow.review_screen(draft, self.config)
        await self._show(context, draft.chat_id, draft, text, rows, query=query, new_message=draft.screen != "review")
        self._save(draft, status=draft.status)

    async def _on_item(self, update, context, query, draft, args) -> None:
        index = int(args[0]) if args else 0
        full = len(args) > 1 and args[1] == "f"
        screen = vnext_flow.item_screen(draft, index, full=full)
        if screen is None:
            return
        text, rows = screen
        await self._show(context, draft.chat_id, draft, text, rows, query=query)
        self._save(draft)

    async def _back_to_review(self, context, query, draft, note: str = "") -> None:
        text, rows = vnext_flow.review_screen(draft, self.config, full=(draft.screen == "list"))
        if note:
            text = note + "\n\n" + text
        await self._show(context, draft.chat_id, draft, text, rows, query=query)
        self._save(draft)

    async def _on_rm(self, update, context, query, draft, args) -> None:
        item = draft.find(args[0]) if args else None
        if item is None:
            return
        item["included"] = False
        await self._back_to_review(context, query, draft, f"הסרתי: {item['display_name']}")

    async def _on_qty(self, update, context, query, draft, args) -> None:
        item = draft.find(args[0]) if len(args) > 1 else None
        if item is None:
            return
        item["quantity"] = float(args[1])
        await self._back_to_review(context, query, draft, f"{item['display_name']}: כמות {float(args[1]):g}")

    async def _on_inc(self, update, context, query, draft, args) -> None:
        item = draft.find(args[0]) if args else None
        if item is None:
            return
        item["included"] = True
        await self._back_to_review(context, query, draft, f"הוספתי להצעה: {item['display_name']}")

    async def _on_keep(self, update, context, query, draft, args) -> None:
        """The household accepts Gordon's pick on an exception -> a real
        human confirmation (kept_exception_choice) for that product."""
        item = draft.find(args[0]) if args else None
        if item is None:
            return
        who = update.effective_user.first_name if update.effective_user else ""
        for store, prod in (item.get("products") or {}).items():
            if prod.get("product_code"):
                vnext_confirmations.note_interaction(
                    self.storage, item["term"], prod["product_code"], store, "kept_exception_choice",
                    product_name=prod.get("product_name", ""), confirmed_by=who, note="vNext exception kept")
                prod["human"] = True
        item["exception"] = {"class": "quiet", "reason": ""}
        item["included"] = True
        await self._back_to_review(context, query, draft, f"בסדר — {item['display_name']} נשאר כמו שהצעתי.")

    async def _on_sub(self, update, context, query, draft, args) -> None:
        item = draft.find(args[0]) if len(args) > 1 else None
        if item is None:
            return
        n = int(args[1])
        subs = item.get("substitutes") or []
        if n >= len(subs):
            return
        chosen = subs[n]
        who = update.effective_user.first_name if update.effective_user else ""
        # The substitute's identity per chain comes from the resolver's
        # substitution candidates when the draft was built; the name alone
        # is enough for the engine's search path.
        item["display_name"] = chosen
        item["term"] = chosen
        item["exception"] = {"class": "quiet", "reason": ""}
        item["included"] = True
        for store, prod in (item.get("products") or {}).items():
            if prod.get("product_code"):
                vnext_confirmations.note_interaction(
                    self.storage, item["key"], prod["product_code"], store, "accepted_substitution",
                    product_name=chosen, confirmed_by=who, note="vNext substitute accepted")
        await self._back_to_review(context, query, draft, f"בסדר — {chosen} במקום.")

    async def _on_alt(self, update, context, query, draft, args) -> None:
        item = draft.find(args[0]) if args else None
        if item is None:
            return
        alt = next((a for a in draft.data.get("alternatives", []) if a["key"] == item["key"]), None)
        if alt is None:
            from . import basket_optimizer
            found = await asyncio.to_thread(basket_optimizer.cheaper_alternatives, self.storage, [item], self.config, 1)
            alt = found[0] if found else None
            if alt is not None:
                draft.data.setdefault("alternatives", []).append(alt)
        if alt is None:
            await self._back_to_review(context, query, draft, f"לא מצאתי אלטרנטיבה זולה יותר ל{item['display_name']}.")
            return
        text = (f"{item['display_name']}: יש {alt['product_name']} ב{vnext_flow.display_name(alt['store'])}"
                f" — חיסכון של כ-₪{alt['saving']:.2f}. להחליף?")
        rows = [[("כן, החלף", vnext_flow.cb("altok", item["n"])), ("לא", vnext_flow.cb("edit", draft.page))]]
        await self._show(context, draft.chat_id, draft, text, rows, query=query)
        self._save(draft)

    async def _on_altok(self, update, context, query, draft, args) -> None:
        item = draft.find(args[0]) if args else None
        if item is None:
            return
        alt = next((a for a in draft.data.get("alternatives", []) if a["key"] == item["key"]), None)
        if alt is None:
            return
        who = update.effective_user.first_name if update.effective_user else ""
        alt["accepted"] = True
        item.setdefault("products", {})[alt["store"]] = {"product_code": alt["product_code"],
                                                          "product_name": alt["product_name"],
                                                          "confidence": 0.9, "status": "alternative", "human": True}
        item["kind"] = item["kind"] if item["kind"] != vnext_flow.KIND_ROUTINE else vnext_flow.KIND_ALT
        vnext_confirmations.note_interaction(
            self.storage, item["term"], alt["product_code"], alt["store"], "accepted_substitution",
            product_name=alt["product_name"], confirmed_by=who, note="cheaper alternative accepted")
        await self._back_to_review(context, query, draft, f"החלפתי ל{alt['product_name']}.")

    async def _on_add(self, update, context, query, draft, args) -> None:
        draft.awaiting = "add_item"
        self._save(draft)
        await context.bot.send_message(chat_id=draft.chat_id, text="מה להוסיף? כתוב את שם המוצר.")

    async def _on_compare(self, update, context, query, draft, args) -> None:
        draft.screen = "compare"
        text, rows = vnext_flow.compare_screen(draft, self.config)
        await self._show(context, draft.chat_id, draft, text, rows, query=query)
        self._save(draft)

    async def _on_chain(self, update, context, query, draft, args) -> None:
        store = args[0] if args else ""
        if store in draft.chains:
            if draft.chains.get(store) and len(draft.enabled_chains()) == 1:
                try:
                    await query.answer("חייבת להישאר לפחות רשת אחת", show_alert=False)
                except Exception:  # noqa: BLE001
                    pass
            else:
                draft.chains[store] = not draft.chains.get(store, True)
        await self._on_compare(update, context, query, draft, args)

    async def _on_detail(self, update, context, query, draft, args) -> None:
        text = vnext_flow.detail_screen(draft)
        await context.bot.send_message(chat_id=draft.chat_id, text=text[:4000])

    async def _on_cancel(self, update, context, query, draft, args) -> None:
        self._save(draft, status="cancelled")
        await query.edit_message_text("ההצעה בוטלה. כלום לא נכנס לעגלה. /plan להצעה חדשה.")

    async def _on_go(self, update, context, query, draft, args) -> None:
        await self._go(update, context, draft, query=query)

    async def _on_done(self, update, context, query, draft, args) -> None:
        self._save(draft, status="done")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            pass
        await context.bot.send_message(chat_id=draft.chat_id,
                                       text="בסדר — העגלות מחכות לך באתר לאישור ולתשלום. אני לא מזמין בעצמי.")

    async def _on_links(self, update, context, query, draft, args) -> None:
        results = (draft.result or {}).get("results") or {s: {} for s in draft.enabled_chains()}
        await context.bot.send_message(chat_id=draft.chat_id, text=vnext_flow.links_text(results))

    async def _on_waste(self, update, context, query, draft, args) -> None:
        item = draft.find(args[0]) if len(args) > 1 else None
        if item is None:
            return
        yes = args[1] == "yes"
        draft.data.setdefault("answers", {})[item["key"]] = "waste_yes" if yes else "waste_no"
        if yes:
            item["included"] = True
            item["waste_reduced"] = False
        else:
            item["included"] = False
        self._save(draft)
        await query.edit_message_text(f"בסדר — {'הוספתי' if yes else 'לא הוספתי'}: {item['display_name']}.")

    async def _on_stockup(self, update, context, query, draft, args) -> None:
        item = draft.find(args[0]) if len(args) > 1 else None
        if item is None:
            return
        yes = args[1] == "yes"
        draft.data.setdefault("answers", {})[item["key"]] = "stockup_yes" if yes else "stockup_no"
        item["included"] = yes
        if yes and item.get("promo", {}).get("units"):
            item["quantity"] = float(item["promo"]["units"])
        who = update.effective_user.first_name if update.effective_user else ""
        if yes:
            for store, prod in (item.get("products") or {}).items():
                if prod.get("product_code") and store == item.get("promo", {}).get("store"):
                    vnext_confirmations.note_interaction(
                        self.storage, item["term"], prod["product_code"], store, "kept_exception_choice",
                        product_name=prod.get("product_name", ""), confirmed_by=who, note="stock-up accepted")
        self._save(draft)
        await query.edit_message_text(
            f"{'מעולה — הוספתי' if yes else 'בסדר, לא הפעם'}: {item.get('promo', {}).get('product_name') or item['display_name']}."
        )

    # -- execution: the only path into the cart engine ---------------------------------

    async def _go(self, update, context, draft, query=None) -> None:
        from .telegram_bot import _build_adapter_factories, ensure_israeli_exit

        chat_id = draft.chat_id
        if not draft.included:
            await context.bot.send_message(chat_id=chat_id, text="ההצעה ריקה — אין מה להכניס.")
            return
        factories = _build_adapter_factories(self.bot.config)
        factories = {s: f for s, f in factories.items() if draft.chains.get(s, True)}
        if not factories:
            await context.bot.send_message(chat_id=chat_id, text="אין רשת פעילה להכנת עגלה.")
            return
        status = await asyncio.to_thread(ensure_israeli_exit, self.bot.config.playwright_proxy, self.bot.config)
        if not status.available:
            self._save(draft, status="confirmed")
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🕒 אין כרגע חיבור לרשתות ({status.detail}). ההצעה שמורה — לחץ שוב 'אשר והכן עגלה' כשהחיבור חוזר.")
            return
        from . import basket_optimizer
        quotes = basket_optimizer.quotes_for(draft.included, draft.chains, self.config)
        draft.data["quotes"] = quotes
        per_store = vnext_flow.execution_terms(draft, quotes)
        per_store = {s: t for s, t in per_store.items() if s in factories}
        if not per_store:
            await context.bot.send_message(chat_id=chat_id, text="אין פריטים לרשתות שנבחרו.")
            return
        self._save(draft, status="executing")
        view = await context.bot.send_message(chat_id=chat_id, text="מכין את העגלות… 🛒")
        loop = asyncio.get_running_loop()
        total = sum(len(t) for t in per_store.values())
        progress = {"done": 0, "last": 0.0}

        async def _redraw(text: str) -> None:
            try:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=view.message_id, text=text)
            except Exception:  # noqa: BLE001
                logger.debug("vNext progress edit failed", exc_info=True)

        def _on_progress(done, _total, result) -> None:
            progress["done"] += 1
            now = loop.time()
            if now - progress["last"] < 4.0 and progress["done"] < total:
                return
            progress["last"] = now
            asyncio.run_coroutine_threadsafe(
                _redraw(f"מכין את העגלות… 🛒\n{progress['done']}/{total} פריטים"), loop)

        def _run():
            from .orchestrator import add_terms_to_cart
            reports: dict = {}
            for store, pairs in per_store.items():
                terms = [pt for pt, _ident in pairs]
                identities = {(store, pt.term): ident for pt, ident in pairs if ident is not None}
                # guard_cart=True: lines the household deleted stay deleted,
                # lines already there are skipped. cartpause is honoured
                # inside the engine. Nothing beyond adding lines happens.
                reports.update(add_terms_to_cart(
                    self.storage, {store: factories[store]}, terms, _on_progress,
                    guard_cart=True, trigger="vnext", proxy=self.bot.config.playwright_proxy,
                    identities=identities,
                ))
            return reports

        try:
            reports = await asyncio.to_thread(_run)
        except Exception:
            logger.exception("vNext execution failed")
            self._save(draft, status="confirmed")
            await _redraw("🛑 המילוי נכשל — ההצעה שמורה, אפשר לנסות שוב.")
            return
        from . import execution
        try:
            carts = await asyncio.to_thread(execution.read_carts, factories)
        except Exception:  # noqa: BLE001
            logger.exception("vNext cart read after execution failed")
            carts = {}
        results = vnext_flow.summarise_reports(reports)
        # Items that landed leave the working list; failures stay for
        # "בצע שינויים נוספים".
        landed = set()
        for store, report in reports.items():
            for r in list(getattr(report, "added", []) or []) + list(getattr(report, "skipped", []) or []):
                landed.add(r.item_name)
        for item in draft.included:
            if item["term"] in landed or item["display_name"] in landed:
                item["included"] = False
        draft.result = {"results": results, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        text, rows = vnext_flow.execution_screen(draft, results, carts)
        try:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=view.message_id, text=text,
                                                reply_markup=_keyboard(rows))
        except Exception:  # noqa: BLE001
            await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=_keyboard(rows))
        import json as _json
        self._save(draft, status="done", result_json=_json.dumps(draft.result, ensure_ascii=False))
