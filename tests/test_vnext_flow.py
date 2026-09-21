"""vNext Phase 2b: the assisted Telegram flow.

What these pin: the nudge stays quiet for every documented reason; the
proposal counts follow the draft; each button moves the draft the way
its label says; a spoken edit applies to the open draft; the ONE path
into the cart engine is the confirm button and it runs with
guard_cart=True; "עבור לתשלום" is a URL and no handler can reach
checkout; the optimizer's split maths; which confirmation kind each
card records; and that nothing ever writes preferred_products.
"""
import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from grocery_bot import basket_optimizer, shopping_plan, shopping_readiness, vnext_catalogue, vnext_flow
from grocery_bot.config import Config
from grocery_bot.telegram_bot import GroceryBot
from grocery_bot.vnext_config import VNextConfig
from tests.test_vnext_plan_shadow import CFG, TODAY, Seeded, _dump


def _config(stores=("tivtaam", "shufersal")) -> Config:
    return Config(
        telegram_bot_token="t", allowed_telegram_user_ids=[], db_path=":memory:",
        shufersal_storage_state_path="x.json", tivtaam_storage_state_path="y.json",
        enabled_stores=list(stores),
    )


def _context():
    context = mock.MagicMock()
    sent = mock.MagicMock()
    sent.message_id = 7
    context.bot.send_message = mock.AsyncMock(return_value=sent)
    context.bot.edit_message_text = mock.AsyncMock()
    context.bot.edit_message_reply_markup = mock.AsyncMock()
    return context


def _query(data: str, chat_id: int = 555):
    update = mock.MagicMock()
    update.effective_chat.id = chat_id
    update.effective_user.first_name = "ישי"
    q = update.callback_query
    q.data = data
    q.answer = mock.AsyncMock()
    q.edit_message_text = mock.AsyncMock()
    q.edit_message_reply_markup = mock.AsyncMock()
    q.message.message_id = 7
    return update


def _buttons(markup) -> list:
    if markup is None:
        return []
    return [b for row in markup.inline_keyboard for b in row]


class FlowBase(Seeded):
    def setUp(self):
        super().setUp()
        self.addCleanup(vnext_catalogue.clear_cache)
        self.bot = GroceryBot(_config(), self.storage)
        self.flow = self.bot.vnext
        self.cart_calls: list = []
        self.patches = [
            mock.patch("grocery_bot.telegram_bot._authorized", return_value=True),
            mock.patch("grocery_bot.orchestrator.add_terms_to_cart", side_effect=self._fake_cart),
            mock.patch("grocery_bot.execution.run_list_items", side_effect=AssertionError("wrong engine path")),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def _fake_cart(self, storage, factories, terms, on_progress=None, **kw):
        from grocery_bot.models import CartAddResult, OrderCycleReport
        self.cart_calls.append({"factories": factories, "terms": terms, "kw": kw})
        reports = {}
        for store in factories:
            report = OrderCycleReport(store=store)
            for pt in terms:
                report.record(CartAddResult(item_name=pt.term, store=store, status="added",
                                            quantity=pt.quantity, verification="verified"))
            reports[store] = report
        return reports

    def _open(self) -> vnext_flow.Draft:
        plan = shopping_plan.build_plan(self.storage, CFG, TODAY)
        return vnext_flow.new_draft(self.storage, 555, plan, CFG, enabled_stores=["tivtaam", "shufersal"])

    def _tap(self, data: str):
        update = _query(data)
        context = _context()
        asyncio.run(self.flow.on_callback(update, context))
        return update, context


class NudgeRules(Seeded):
    def _r(self, action="prepare_now"):
        r = shopping_readiness.assess(self.storage, CFG, TODAY)
        r.suggested_action = action
        return r

    def test_every_suppression_reason_is_named(self):
        noon = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)   # 12:00 Israel
        night = datetime(2026, 9, 21, 20, 30, tzinfo=timezone.utc)  # 23:30 Israel
        self.assertEqual(vnext_flow.nudge_suppression(self.storage, self._r("wait"), CFG, noon), "readiness says wait")
        self.assertIn("draft is already open", vnext_flow.nudge_suppression(self.storage, self._r(), CFG, noon, open_draft=True))
        self.assertIn("outside", vnext_flow.nudge_suppression(self.storage, self._r(), CFG, night))
        self.assertEqual(vnext_flow.nudge_suppression(self.storage, self._r(), CFG, noon), "")
        vnext_flow.note_nudge_sent(self.storage, noon)
        self.assertIn("min 3 days", vnext_flow.nudge_suppression(self.storage, self._r(), CFG, noon + timedelta(days=1)))
        self.assertEqual(vnext_flow.nudge_suppression(self.storage, self._r(), CFG, noon + timedelta(days=4)), "")
        vnext_flow.snooze_nudge(self.storage, CFG, noon + timedelta(days=4))
        self.assertIn("snoozed", vnext_flow.nudge_suppression(self.storage, self._r(), CFG, noon + timedelta(days=5)))
        quiet = VNextConfig(nudge_on_prepare_soon=False)
        self.assertIn("prepare_soon", vnext_flow.nudge_suppression(self.storage, self._r("prepare_soon"), quiet, noon))

    def test_nudge_screen_text_and_buttons(self):
        text, rows = vnext_flow.nudge_screen(self._r())
        self.assertIn("נראה שכדאי להזמין קניות", text)
        self.assertIn("רוצה שאכין הצעת קנייה?", text)
        labels = [label for row in rows for label, _ in row]
        self.assertEqual(labels, ["כן, תכין הצעה", "רק מה שחשוב", "לא עכשיו", "הצג פריטים"])


class ProposalCounts(FlowBase):
    def test_counts_follow_the_draft(self):
        draft = self._open()
        c = vnext_flow.counts(draft)
        self.assertEqual(c["total"], len(draft.included))
        self.assertEqual(c["request"], len([i for i in draft.included if i["mandatory"]]))
        text, rows = vnext_flow.proposal_screen(draft)
        self.assertIn(f"({c['total']} מוצרים)", text)
        self.assertIn("בקשות שלכם", text)
        labels = [label for row in rows for label, _ in row]
        self.assertEqual(labels[:4], ["הצג את כל הרשימה", "ערוך / הסר פריטים", "השווה בין רשתות", "אשר והכן עגלה"])
        draft.find("גזר")["included"] = False
        self.assertEqual(vnext_flow.counts(draft)["total"], c["total"] - 1)

    def test_important_only_leaves_stockups_out(self):
        plan = shopping_plan.build_plan(self.storage, CFG, TODAY)
        for pi in plan.items:
            pi.stock_up = True
            pi.stockup_assessment = {"worthwhile": True, "store": "tivtaam", "product_name": "x",
                                     "recommended_units": 2, "economics": {}}
        full = vnext_flow.draft_data_from_plan(self.storage, plan, CFG, False, ["tivtaam"])
        important = vnext_flow.draft_data_from_plan(self.storage, plan, CFG, True, ["tivtaam"])
        self.assertGreater(len([i for i in full["items"] if i["included"]]),
                           len([i for i in important["items"] if i["included"]]))


class ButtonsMoveTheDraft(FlowBase):
    def test_edit_remove_quantity_and_back(self):
        draft = self._open()
        item = draft.included[0]
        self._tap(vnext_flow.cb("edit", 0))
        self.assertEqual(self.flow.open_draft(555).screen, "review")
        self._tap(vnext_flow.cb("qty", item["n"], 3))
        self.assertEqual(self.flow.open_draft(555).find(item["n"])["quantity"], 3.0)
        self._tap(vnext_flow.cb("rm", item["n"]))
        self.assertFalse(self.flow.open_draft(555).find(item["n"])["included"])
        self._tap(vnext_flow.cb("inc", item["n"]))
        self.assertTrue(self.flow.open_draft(555).find(item["n"])["included"])
        update, _ = self._tap(vnext_flow.cb("proposal"))
        self.assertIn("הנה הצעת הקנייה", update.callback_query.edit_message_text.call_args[0][0])

    def test_chain_toggle_keeps_at_least_one(self):
        self._open()
        self._tap(vnext_flow.cb("chain", "shufersal"))
        d = self.flow.open_draft(555)
        self.assertEqual(d.chains, {"tivtaam": True, "shufersal": False})
        self._tap(vnext_flow.cb("chain", "tivtaam"))
        self.assertEqual(self.flow.open_draft(555).chains, {"tivtaam": True, "shufersal": False})

    def test_cancel_closes_and_a_stale_button_says_so(self):
        self._open()
        self._tap(vnext_flow.cb("cancel"))
        self.assertIsNone(self.flow.open_draft(555))
        update, _ = self._tap(vnext_flow.cb("edit", 0))
        self.assertIn("כבר לא פתוחה", update.callback_query.edit_message_text.call_args[0][0])

    def test_add_item_button_then_text(self):
        self._open()
        self._tap(vnext_flow.cb("add"))
        self.assertEqual(self.flow.open_draft(555).awaiting, "add_item")
        update = mock.MagicMock()
        update.effective_chat.id = 555
        update.message.reply_text = mock.AsyncMock()
        handled = asyncio.run(self.flow.handle_awaited_text(update, _context(), "טחינה גולמית", "ישי"))
        self.assertTrue(handled)
        d = self.flow.open_draft(555)
        self.assertEqual(d.awaiting, "")
        self.assertTrue(d.find("טחינה גולמית")["included"])
        self.assertIn("טחינה גולמית", [r.text for r in self.storage.list_pending_adhoc()])


class SpokenEditsApplyToTheDraft(FlowBase):
    def _parsed(self, intent, name="", amount=None):
        from grocery_bot.nlu import ParsedItem, ParsedMessage
        items = [ParsedItem(name=name, amount=amount)] if name else []
        return ParsedMessage(intent=intent, items=items)

    def _update(self):
        update = mock.MagicMock()
        update.effective_chat.id = 555
        update.message.reply_text = mock.AsyncMock()
        return update

    def test_add_remove_quantity(self):
        self._open()
        u = self._update()
        self.assertTrue(asyncio.run(self.flow.handle_text(u, _context(), self._parsed("add_item", "קוטג"), "ישי")))
        d = self.flow.open_draft(555)
        self.assertTrue(d.find("קוטג")["included"])
        self.assertTrue(asyncio.run(self.flow.handle_text(u, _context(), self._parsed("change_quantity", "קוטג", 2), "ישי")))
        self.assertEqual(self.flow.open_draft(555).find("קוטג")["quantity"], 2.0)
        self.assertTrue(asyncio.run(self.flow.handle_text(u, _context(), self._parsed("remove_item", "קוטג"), "ישי")))
        self.assertFalse(self.flow.open_draft(555).find("קוטג")["included"])
        self.assertEqual(self.cart_calls, [])

    def test_no_draft_means_not_handled(self):
        u = self._update()
        self.assertFalse(asyncio.run(self.flow.handle_text(u, _context(), self._parsed("add_item", "קוטג"), "ישי")))
        self.assertFalse(asyncio.run(self.flow.handle_text(u, _context(), self._parsed("price_query", "קוטג"), "ישי")))


class OnlyTheConfirmButtonReachesTheCart(FlowBase):
    def setUp(self):
        super().setUp()
        self.exit_patch = mock.patch("grocery_bot.exitnode.ensure_israeli_exit",
                                     return_value=mock.Mock(available=True, detail="ok"))
        self.exit_patch.start()
        self.addCleanup(self.exit_patch.stop)
        self.factories = mock.patch("grocery_bot.telegram_bot._build_adapter_factories",
                                    return_value={"tivtaam": lambda: None, "shufersal": lambda: None})
        self.factories.start()
        self.addCleanup(self.factories.stop)
        self.carts = mock.patch("grocery_bot.execution.read_carts", return_value={"tivtaam": {"total": 120.5}})
        self.carts.start()
        self.addCleanup(self.carts.stop)

    def test_every_other_button_leaves_the_cart_alone(self):
        draft = self._open()
        item = draft.included[0]
        for data in (vnext_flow.cb("edit", 0), vnext_flow.cb("list", 0), vnext_flow.cb("compare"),
                     vnext_flow.cb("item", 0, "i"), vnext_flow.cb("qty", item["n"], 2), vnext_flow.cb("detail"),
                     vnext_flow.cb("proposal"), vnext_flow.cb("chain", "shufersal")):
            self._tap(data)
        self.assertEqual(self.cart_calls, [])

    def test_confirm_runs_the_engine_guarded_and_stops_before_checkout(self):
        prefs_before = self.storage.list_preferences()
        draft = self._open()
        n_before = len(draft.included)
        update, context = self._tap(vnext_flow.cb("go"))
        self.assertGreaterEqual(len(self.cart_calls), 1)
        for call in self.cart_calls:
            self.assertTrue(set(call["factories"]) <= set(draft.enabled_chains()))
            self.assertTrue(call["kw"]["guard_cart"])
            self.assertEqual(call["kw"]["trigger"], "vnext")
            self.assertIn("identities", call["kw"])
        text = context.bot.edit_message_text.call_args.kwargs["text"]
        self.assertIn("עגלה מוכנה", text)
        self.assertIn("אני לא משלם ולא מזמין", text)
        markup = context.bot.edit_message_text.call_args.kwargs["reply_markup"]
        urls = [b for b in _buttons(markup) if b.url]
        self.assertTrue(urls, "the pay button must be a URL to the chain's own cart page")
        for b in urls:
            self.assertIn(b.url, vnext_flow.CHAIN_CART_URL.values())
            self.assertIsNone(b.callback_data)
        d = self.flow.open_draft(555)
        self.assertEqual(d.status, "done")
        self.assertLess(len(d.included), n_before)
        self.assertEqual(self.storage.list_preferences(), prefs_before)

    def test_no_handler_reaches_checkout(self):
        import inspect
        from grocery_bot import vnext_flow as vf, vnext_handlers as vh
        source = inspect.getsource(vh) + inspect.getsource(vf)
        for forbidden in ("checkout(", "pay(", "place_order", "submit_order", "confirm_purchase"):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("kind=\"checkout\"", source)

    def test_exit_down_keeps_the_draft_confirmed(self):
        self.exit_patch.stop()
        with mock.patch("grocery_bot.exitnode.ensure_israeli_exit", return_value=mock.Mock(available=False, detail="down")):
            self._open()
            update, context = self._tap(vnext_flow.cb("go"))
        self.exit_patch.start()
        self.assertEqual(self.cart_calls, [])
        self.assertEqual(self.flow.open_draft(555).status, "confirmed")


class OptimizerMaths(unittest.TestCase):
    def _items(self, prices):
        return [{"key": f"k{i}", "n": i, "display_name": f"item{i}", "quantity": 1, "included": True,
                 "products": {s: {"product_name": f"p{i}{s}"} for s in p}, "prices": p, "promo": {}}
                for i, p in enumerate(prices)]

    def test_split_only_when_it_beats_two_deliveries(self):
        cfg = VNextConfig(delivery_fee_shufersal=30.0, delivery_fee_tivtaam=30.0, split_min_saving=25.0)
        # tivtaam cheaper on the first two, shufersal much cheaper on the last two
        items = self._items([{"tivtaam": 10, "shufersal": 30}, {"tivtaam": 10, "shufersal": 30},
                             {"tivtaam": 60, "shufersal": 10}, {"tivtaam": 60, "shufersal": 10}])
        q = basket_optimizer.quotes_for(items, {"tivtaam": True, "shufersal": True}, cfg)
        self.assertEqual(q["chains"]["tivtaam"]["total"], 140 + 30)
        self.assertEqual(q["chains"]["shufersal"]["total"], 80 + 30)
        # split: 20 + 20 + 2 deliveries = 100 vs single best 110 -> saving 10 < 25 -> single
        self.assertEqual(q["recommendation"]["single"], "shufersal")
        self.assertEqual(set(q["assignment"].values()), {"shufersal"})
        cfg2 = VNextConfig(delivery_fee_shufersal=30.0, delivery_fee_tivtaam=30.0, split_min_saving=5.0)
        q2 = basket_optimizer.quotes_for(items, {"tivtaam": True, "shufersal": True}, cfg2)
        self.assertEqual(q2["recommendation"]["split"], {"tivtaam": 2, "shufersal": 2})
        self.assertEqual(q2["recommendation"]["saving"], 10.0)

    def test_unquotable_items_follow_the_anchor_and_are_named(self):
        cfg = VNextConfig()
        items = self._items([{"tivtaam": 10}, {"tivtaam": 12}])
        items.append({"key": "k9", "n": 9, "display_name": "מלח", "quantity": 1, "included": True,
                      "products": {}, "prices": {}, "promo": {}})
        q = basket_optimizer.quotes_for(items, {"tivtaam": True, "shufersal": True}, cfg)
        self.assertIn("מלח", q["chains"]["tivtaam"]["missing"])
        self.assertEqual(q["assignment"]["k9"], "tivtaam")

    def test_single_chain_notes_the_gift_threshold(self):
        cfg = VNextConfig()
        items = self._items([{"shufersal": 560}])
        q = basket_optimizer.quotes_for(items, {"shufersal": True}, cfg)
        self.assertIn("599", q["recommendation"]["note"])


class CardsRecordTheRightKinds(FlowBase):
    def _confirmations(self):
        return self.storage.list_vnext_product_confirmations()

    def test_stockup_yes_is_a_kept_exception_choice(self):
        draft = self._open()
        item = draft.included[0]
        item["kind"] = vnext_flow.KIND_STOCKUP
        item["promo"] = {"store": "tivtaam", "product_name": "מבצע", "units": 2, "saving": 6.0}
        item["products"] = {"tivtaam": {"product_code": "777", "product_name": "מבצע"}}
        draft.data["cards"]["stockup"] = [item["key"]]
        vnext_flow.save_draft(self.storage, draft)
        self._tap(vnext_flow.cb("su", item["n"], "yes"))
        rows = self._confirmations()
        self.assertEqual([r["kind"] for r in rows], ["kept_exception_choice"])
        self.assertEqual(rows[0]["polarity"], "positive")
        self.assertEqual(self.flow.open_draft(555).find(item["n"])["quantity"], 2.0)
        self._tap(vnext_flow.cb("su", item["n"], "no"))
        self.assertEqual(len(self._confirmations()), 1)   # "not now" is not a rejection

    def test_waste_card_changes_inclusion_not_products(self):
        draft = self._open()
        item = draft.included[0]
        item["waste_reduced"] = True
        draft.data["cards"]["waste"] = [item["key"]]
        vnext_flow.save_draft(self.storage, draft)
        card = vnext_flow.waste_card(draft, self.storage, CFG)
        self.assertIsNotNone(card)
        self.assertIn("שים לב", card[0])
        self._tap(vnext_flow.cb("waste", item["n"], "no"))
        self.assertFalse(self.flow.open_draft(555).find(item["n"])["included"])
        self.assertEqual(self._confirmations(), [])   # a quantity decision, not a product one
        self._tap(vnext_flow.cb("waste", item["n"], "yes"))
        self.assertTrue(self.flow.open_draft(555).find(item["n"])["included"])

    def test_keep_and_substitute_and_alternative(self):
        prefs_before = self.storage.list_preferences()
        draft = self._open()
        item = draft.included[0]
        item["exception"] = {"class": "true_user_decision", "reason": "requested brand not found"}
        item["products"] = {"tivtaam": {"product_code": "555", "product_name": "לחם"}}
        item["substitutes"] = ["לחם אחר"]
        vnext_flow.save_draft(self.storage, draft)
        self._tap(vnext_flow.cb("keep", item["n"]))
        kinds = [r["kind"] for r in self._confirmations()]
        self.assertEqual(kinds, ["kept_exception_choice"])
        self._tap(vnext_flow.cb("sub", item["n"], 0))
        kinds = [r["kind"] for r in self._confirmations()]
        self.assertEqual(kinds, ["kept_exception_choice", "accepted_substitution"])
        d = self.flow.open_draft(555)
        d.data["alternatives"] = [{"key": item["key"], "store": "shufersal", "product_code": "P_1",
                                   "product_name": "לחם זול", "saving": 3.0, "fraction": 0.2, "accepted": False}]
        vnext_flow.save_draft(self.storage, d)
        self._tap(vnext_flow.cb("altok", item["n"]))
        kinds = [r["kind"] for r in self._confirmations()]
        self.assertEqual(kinds[-1], "accepted_substitution")
        self.assertTrue(self.flow.open_draft(555).find(item["n"])["products"]["shufersal"]["human"])
        self.assertEqual(self.storage.list_preferences(), prefs_before)


class ExecutionScreen(unittest.TestCase):
    def test_pay_is_a_link_and_gaps_are_named(self):
        draft = vnext_flow.Draft(id=1, chat_id=1, status="done", data={"items": [], "chains": {"tivtaam": True}})
        text, rows = vnext_flow.execution_screen(
            draft, {"tivtaam": {"added": 3, "already": 1, "failed": ["טחינה"], "unverified": []}},
            {"tivtaam": {"total": 99.9}})
        self.assertIn("✅ טיב טעם — עגלה מוכנה (4 פריטים, ₪99.90)", text)
        self.assertIn("לא נמצא: טחינה", text)
        links = [t for row in rows for _, t in row if isinstance(t, tuple)]
        self.assertEqual(links, [(vnext_flow.KEY_URL, vnext_flow.CHAIN_CART_URL["tivtaam"])])
        self.assertIn("אני לא משלם ולא מזמין", text)

    def test_execution_terms_follow_the_assignment_with_identities(self):
        draft = vnext_flow.Draft(id=1, chat_id=1, status="draft", data={
            "chains": {"tivtaam": True, "shufersal": True},
            "items": [
                {"n": 0, "key": "חלב", "term": "חלב", "display_name": "חלב", "quantity": 2, "kind": "request",
                 "included": True, "products": {"tivtaam": {"product_code": "1", "product_name": "חלב 3%"}}},
                {"n": 1, "key": "מלח", "term": "מלח", "display_name": "מלח", "quantity": 1, "kind": "routine",
                 "included": True, "products": {}},
            ],
            "quotes": {"assignment": {"חלב": "tivtaam", "מלח": "shufersal"}},
        })
        per_store = vnext_flow.execution_terms(draft)
        self.assertEqual(sorted(per_store), ["shufersal", "tivtaam"])
        pt, ident = per_store["tivtaam"][0]
        self.assertEqual((pt.term, pt.quantity, pt.source_kind), ("חלב", 2, "adhoc"))
        self.assertEqual((ident.product_code, ident.name, ident.basis), ("1", "חלב 3%", "vnext_resolver"))
        self.assertIsNone(per_store["shufersal"][0][1])


class NothingWritesPreferences(FlowBase):
    def test_the_whole_flow_leaves_preferred_products_alone(self):
        before = self.storage.list_preferences()
        draft = self._open()
        item = draft.included[0]
        for data in (vnext_flow.cb("edit", 0), vnext_flow.cb("qty", item["n"], 2), vnext_flow.cb("compare"),
                     vnext_flow.cb("keep", item["n"]), vnext_flow.cb("proposal")):
            self._tap(data)
        self.assertEqual(self.storage.list_preferences(), before)
        self.assertEqual(self.storage.list_rejections(), [])


if __name__ == "__main__":
    unittest.main()
