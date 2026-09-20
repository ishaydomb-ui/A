"""vNext Phase 1 — the plan is a proposal: zero mutation, honest labels,
no writes to preferred_products, and the view/readiness layers stay pure.
"""
import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from grocery_bot import basket_optimizer, shopping_plan, shopping_readiness, telegram_vnext_view
from grocery_bot.storage import Storage
from grocery_bot.vnext_config import VNextConfig

TODAY = date(2026, 9, 20)
CFG = VNextConfig()


def _day(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


def _dump(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    out = {t: conn.execute(f"SELECT * FROM {t}").fetchall() for t in tables}
    conn.close()
    return out


class Seeded(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = str(Path(self._tmp.name) / "t.sqlite3")
        self.storage = Storage(self.db)
        s = self.storage
        s.set_state("target_gap_days", "9")
        from grocery_bot.stock import StockItem
        s.replace_stock_items("tivtaam", [
            StockItem(product_code="m1", product_name="חלב 3%", share=0.8, department="", default_quantity=2),
            StockItem(product_code="e1", product_name="ביצים", share=0.7, department="", default_quantity=1),
            StockItem(product_code="t1", product_name="טונה", share=0.5, department="מזווה ושימורים", default_quantity=1),
        ])
        for code, name, days in (("m1", "חלב 3%", [10, 17, 24, 31, 38, 45]), ("e1", "ביצים", [2, 9, 16, 23])):
            for i, d in enumerate(days):
                s.record_tivtaam_order_lines(f"o{code}{i}", _day(d), [{
                    "code": code, "barcode": "", "name": name, "quantity": 1, "actual_quantity": 1,
                    "weighable": False, "price": 6.0, "total": 6.0, "substituted": False}])
            s.record_last_purchase("tivtaam", [(code, _day(min(days)))])
        s.add_adhoc_request(text="גזר", requested_by="לירן")
        s.add_adhoc_request(text="שמן זית", requested_by="ישי", quantity=2)
        s.remember_choice("tivtaam", "גזר", "g1", "גזר ארוז", source="purchase")   # inference-grade
        s.log_orders([{"code": "x1", "placed_at": _day(11) + "T10:00:00", "item_count": 20}], store="tivtaam")
        s.add_vnext_planned_meal("טאקו", _day(-2), ["טורטיות", "בשר טחון"], declared_by="ישי")


class ZeroMutation(Seeded):
    def test_plan_and_readiness_change_nothing(self):
        before = _dump(self.db)
        plan = shopping_plan.build_plan(self.storage, CFG, TODAY)
        readiness = shopping_readiness.assess(self.storage, CFG, TODAY, plan)
        telegram_vnext_view.readiness_message(readiness, plan)
        telegram_vnext_view.plan_message(plan)
        telegram_vnext_view.exception_message(plan)
        plan.to_json()
        json.dumps(readiness.to_dict())
        after = _dump(self.db)
        self.assertEqual(before, after)
        self.assertFalse(plan.mutates_cart)

    def test_inferred_product_in_plan_does_not_touch_preferred_products(self):
        rows_before = self.storage.list_preferences()
        plan = shopping_plan.build_plan(self.storage, CFG, TODAY)
        carrot = next(i for i in plan.items if i.term == "גזר")
        self.assertIsNotNone(carrot.inferred_product)
        self.assertIsNone(carrot.human_product)
        self.assertEqual(carrot.inferred_product["origin"], "INFERENCE")
        self.assertEqual(self.storage.list_preferences(), rows_before)
        self.assertEqual(self.storage.list_rejections(), [])
        self.assertEqual(len(self.storage.list_pending_adhoc()), 2)

    def test_no_adapter_or_cart_code_is_imported(self):
        """The shadow layer must not even be able to reach a cart."""
        with mock.patch("grocery_bot.orchestrator.add_terms_to_cart", side_effect=AssertionError("cart touched")), \
             mock.patch("grocery_bot.execution.run_list_items", side_effect=AssertionError("cart touched")):
            shopping_plan.build_plan(self.storage, CFG, TODAY)
            shopping_readiness.assess(self.storage, CFG, TODAY)


class PlanShape(Seeded):
    def test_fields_and_labels(self):
        plan = shopping_plan.build_plan(self.storage, CFG, TODAY)
        d = plan.to_dict()
        self.assertEqual(d["schema"], shopping_plan.SCHEMA)
        self.assertFalse(d["mutates_cart"])
        item = d["items"][0]
        for key in ("term", "source_evidence", "reason", "confidence", "quantity", "quantity_confidence",
                    "mandatory", "stock_up", "meal_or_event", "candidate_retailers", "unresolved_decisions"):
            self.assertIn(key, item)
        olive = next(i for i in plan.items if i.term == "שמן זית")
        self.assertTrue(olive.mandatory)
        self.assertEqual(olive.quantity, 2)
        self.assertTrue(any("no known product" in u for u in olive.unresolved_decisions))
        milk = next(i for i in plan.items if i.term == "חלב 3%")
        self.assertTrue(milk.depletion_inference.get("is_inference"))
        self.assertFalse(milk.inventory_known)
        taco = [i for i in plan.items if i.meal_or_event == "טאקו"]
        self.assertEqual(len(taco), 2)
        self.assertTrue(all(not i.inventory_known for i in taco))
        self.assertIn("live cart contents are NOT read", " ".join(plan.caveats))

    def test_recently_bought_item_is_ignored(self):
        plan = shopping_plan.build_plan(self.storage, CFG, TODAY)
        self.assertNotIn("ביצים", [i.term for i in plan.items])
        self.assertIn("ביצים", [i["term"] for i in plan.ignored])

    def test_exceptions_are_narrower_than_suggestions(self):
        plan = shopping_plan.build_plan(self.storage, CFG, TODAY)
        self.assertLessEqual(len(plan.exceptions), len(plan.suggested_items) + len(
            [i for i in plan.auto_items if i.mandatory]))
        self.assertEqual(plan.summary["decisions_needed"], len(plan.exceptions))


class ReadinessShape(Seeded):
    def test_signals_and_action(self):
        r = shopping_readiness.assess(self.storage, CFG, TODAY)
        self.assertIn(r.suggested_action, ("prepare_now", "prepare_soon", "wait"))
        self.assertEqual(r.signals["explicit_pending"], 2)
        self.assertEqual(r.signals["upcoming_meals"], 1)
        self.assertFalse(r.signals["cart_state_known"])
        self.assertLessEqual(r.confidence, CFG.readiness_confidence_cap_cart_unknown)
        self.assertFalse(r.to_dict()["sends_messages"])
        self.assertTrue(any("nightly order sync" in x for x in r.reasons))

    def test_empty_household_waits(self):
        empty = Storage(str(Path(self._tmp.name) / "empty.sqlite3"))
        r = shopping_readiness.assess(empty, CFG, TODAY)
        self.assertEqual(r.suggested_action, "wait")
        self.assertEqual(r.signals["estimated_basket"], 0)


class ViewText(Seeded):
    def test_hebrew_counts(self):
        c = telegram_vnext_view.count
        self.assertEqual(c(0, "אחד", "רבים"), "")
        self.assertEqual(c(1, "בקשה פתוחה אחת", "בקשות פתוחות"), "בקשה פתוחה אחת")
        self.assertEqual(c(4, "בקשה פתוחה אחת", "בקשות פתוחות"), "4 בקשות פתוחות")

    def test_messages_render_without_markup(self):
        plan = shopping_plan.build_plan(self.storage, CFG, TODAY)
        r = shopping_readiness.assess(self.storage, CFG, TODAY, plan)
        ready = telegram_vnext_view.readiness_message(r, plan)
        self.assertIn("2 בקשות פתוחות", ready)
        self.assertIn("טאקו מוסיפה", ready)
        prepared = telegram_vnext_view.plan_message(plan)
        self.assertTrue(prepared.startswith("הקנייה מוכנה"))
        self.assertIn("בקשות", prepared)
        exc = telegram_vnext_view.exception_message(plan)
        self.assertTrue(exc.startswith("צריך ממך") or exc.startswith("אין החלטות"))
        for text in (ready, prepared, exc):
            self.assertNotIn("<b>", text)
            self.assertNotIn("*", text)


class OptimizerSeam(Seeded):
    def test_phase1_optimizer_declares_itself_unimplemented(self):
        plan = shopping_plan.build_plan(self.storage, CFG, TODAY)
        result = basket_optimizer.optimize(basket_optimizer.OptimizerInput(
            plan=plan, chains=(basket_optimizer.ChainEconomics("tivtaam"), basket_optimizer.ChainEconomics("shufersal")),
        ))
        self.assertFalse(result.implemented)
        self.assertEqual(result.recommended_split, {})
        self.assertEqual(result.inputs_summary["chains"], ["tivtaam", "shufersal"])


class CliCommands(Seeded):
    def test_cli_plan_and_readiness_are_read_only(self):
        import io
        from contextlib import redirect_stdout

        from grocery_bot import cli

        before = _dump(self.db)
        for command, args in (("vnext-plan", []), ("vnext-plan", ["--json"]),
                              ("vnext-readiness", []), ("vnext-readiness", ["--json"])):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cli._DB_ONLY_COMMANDS[command](self.storage, args + ["--as-of", TODAY.isoformat()])
            self.assertEqual(code, 0)
            self.assertTrue(buf.getvalue().strip())
            if "--json" in args:
                json.loads(buf.getvalue())
        self.assertEqual(_dump(self.db), before)


if __name__ == "__main__":
    unittest.main()
