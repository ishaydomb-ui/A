"""vNext Phase 1.5 — trust rules on the plan: reconciliation of old
requests, exception reduction, cadence strata, cart-state handling,
stock-up economics. Synthetic rows, production rules, no cart.
"""
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from grocery_bot import household_evidence as he
from grocery_bot import need_engine, vnext_reconcile
from grocery_bot.shopping_plan import build_plan
from grocery_bot.shopping_readiness import assess as assess_readiness
from grocery_bot.stock import StockItem
from grocery_bot.storage import Storage
from grocery_bot.telegram_vnext_view import evaluate_message, exception_message
from grocery_bot.vnext_config import VNextConfig

TODAY = date(2026, 9, 20)
CFG = VNextConfig()


def _day(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))
        self.storage.set_state("target_gap_days", "9")

    def _request(self, text, days_ago):
        rid = self.storage.add_adhoc_request(text=text, requested_by="לירן")
        # add_adhoc_request stamps "now"; move it back to the day of the request
        import sqlite3
        conn = sqlite3.connect(self.storage._db_path)  # noqa: SLF001 - test fixture
        conn.execute("UPDATE adhoc_requests SET created_at = ? WHERE id = ?", (_day(days_ago) + "T10:00:00+00:00", rid))
        conn.commit()
        conn.close()
        return rid

    def _order(self, code, days_ago, lines):
        self.storage.record_tivtaam_order_lines(code, _day(days_ago), [{
            "code": f"{code}-{i}", "barcode": f"b{code}{i}", "name": name, "quantity": 1.0,
            "actual_quantity": 1.0, "weighable": False, "price": 5.0, "total": 5.0, "substituted": False,
        } for i, name in enumerate(lines)])

    def _shelf(self, store, rows):
        self.storage.record_store_prices(store, [
            {"barcode": code, "name": name, "price": 9.9, "observed_at": TODAY.isoformat(), "source": "feed"}
            for code, name in rows])

    def _stock(self, store, code, name, share=0.8, qty=1):
        items = [StockItem(product_code=r["product_code"], product_name=r["product_name"], share=r["share"],
                           department=r["department"], default_quantity=r["default_quantity"])
                 for r in self.storage.list_stock_items(store)]
        items.append(StockItem(product_code=code, product_name=name, share=share, department="", default_quantity=qty))
        self.storage.replace_stock_items(store, items)

    def _lines(self, code, name, days_ago_list, qty=1.0):
        for i, days in enumerate(days_ago_list):
            self.storage.record_tivtaam_order_lines(f"o{code}{i}", _day(days), [{
                "code": code, "barcode": "", "name": name, "quantity": qty, "actual_quantity": qty,
                "weighable": False, "price": 5.0, "total": 5.0 * qty, "substituted": False}])
        self.storage.record_last_purchase("tivtaam", [(code, _day(min(days_ago_list)))])

    def _plan(self):
        return build_plan(self.storage, CFG, TODAY)

    def _item(self, plan, term):
        key = he.key_for(term)
        return next((i for i in plan.items if i.term == key), None)


class ReconciliationTests(Base):
    def test_stale_request_fulfilled_by_later_semantically_matching_order_is_not_active(self):
        rid = self._request("סלק (לא באריזת ואקום)", days_ago=4)
        self._order("o1", days_ago=3, lines=["סלק", "חלב 3%"])
        result = vnext_reconcile.reconcile_all(self.storage)
        self.assertEqual(result[0].status_estimate, "likely_fulfilled")
        self.assertEqual(result[0].fulfillment_evidence[0]["line"], "סלק")
        plan = self._plan()
        self.assertEqual(plan.summary["pending_requests"]["likely_fulfilled"], 1)
        self.assertEqual(plan.active_requests, [])
        item = self._item(plan, "סלק (לא באריזת ואקום)")
        self.assertTrue(item is None or item.decision != "auto_include")
        # shadow mode: the request row itself is untouched
        self.assertEqual([r.id for r in self.storage.list_pending_adhoc()], [rid])
        readiness = assess_readiness(self.storage, CFG, TODAY, plan)
        self.assertEqual(readiness.signals["explicit_active"], 0)
        self.assertEqual(readiness.signals["explicit_likely_fulfilled"], 1)

    def test_vague_later_purchase_does_not_satisfy_a_qualified_request(self):
        self._request("סלק (לא באריזת ואקום)", days_ago=4)
        self._order("o1", days_ago=3, lines=["סלק מבושל בואקום"])
        result = vnext_reconcile.reconcile_all(self.storage)
        self.assertEqual(result[0].status_estimate, "active")
        self._request("גרנולה ללא תוספת סוכר", days_ago=4)
        self._order("o2", days_ago=2, lines=["גרנולה עם פירות טרופיים"])
        result = {r.text: r.status_estimate for r in vnext_reconcile.reconcile_all(self.storage)}
        self.assertEqual(result["גרנולה ללא תוספת סוכר"], "active")

    def test_head_match_with_unverified_qualifier_is_uncertain_and_only_suggested(self):
        self._request("עגבניות שרי במגוון צבעים", days_ago=4)
        self._order("o1", days_ago=3, lines=["עגבניות שרי"])
        self._shelf("tivtaam", [("1", "עגבניות שרי מיקס")])
        result = vnext_reconcile.reconcile_all(self.storage)
        self.assertEqual(result[0].status_estimate, "uncertain")
        plan = self._plan()
        item = self._item(plan, "עגבניות שרי במגוון צבעים")
        self.assertEqual(item.request_status_estimate, "uncertain")
        self.assertEqual(item.decision, "suggest")

    def test_an_order_before_the_request_does_not_count(self):
        self._request("גזר", days_ago=2)
        self._order("o1", days_ago=5, lines=["גזר ארוז"])
        self.assertEqual(vnext_reconcile.reconcile_all(self.storage)[0].status_estimate, "active")


class ExceptionReductionTests(Base):
    def test_high_need_confidence_can_coexist_with_unresolved_product(self):
        self._request("פלפלים בכמה צבעים", days_ago=1)
        self._shelf("tivtaam", [("1", "פלפל אדום"), ("2", "פלפל ירוק")])
        item = self._item(self._plan(), "פלפלים בכמה צבעים")
        self.assertGreaterEqual(item.need_confidence, CFG.high_confidence)
        self.assertEqual(item.product_resolution["status"], "unresolved")
        self.assertEqual(item.product_confidence, 0.0)
        self.assertEqual(item.decision, "auto_include")          # the need is not in question
        self.assertEqual(item.exception_class, "true_user_decision")   # the product is

    def test_inferred_sku_alone_does_not_create_a_user_decision(self):
        self._request("גזר", days_ago=1)
        self._shelf("tivtaam", [("1", "גזר ארוז")])
        plan = self._plan()
        item = self._item(plan, "גזר")
        self.assertIsNotNone(item.inferred_product)
        self.assertIsNone(item.human_product)
        self.assertEqual(item.exception_class, "agent_resolvable")
        self.assertEqual(plan.exceptions, [])
        self.assertEqual(exception_message(plan), "אין החלטות פתוחות — הכל ברור.")

    def test_cart_state_unknown_is_one_plan_caveat_not_n_decisions(self):
        for term in ("גזר", "מלח", "שמן זית", "סילאן"):
            self._request(term, days_ago=1)
        self._shelf("tivtaam", [("1", "גזר ארוז"), ("2", "מלח ים"), ("3", "שמן זית כתית"), ("4", "סילאן תמרים")])
        plan = self._plan()
        self.assertEqual(plan.cart_state, "unknown")
        self.assertEqual(sum(1 for c in plan.caveats if "cart_state=unknown" in c), 1)
        self.assertEqual(plan.summary["decisions_needed"], 0)
        for item in plan.items:
            self.assertFalse(any("cart" in u for u in item.unresolved_decisions), item.unresolved_decisions)

    def test_a_stated_brand_that_cannot_be_found_is_a_true_decision(self):
        self._request("לחם חיטה מלא דגנית עין בר", days_ago=1)
        self._shelf("tivtaam", [("1", "לחם חיטה מלא")])
        plan = self._plan()
        item = self._item(plan, "לחם חיטה מלא דגנית עין בר")
        self.assertEqual(item.exception_class, "true_user_decision")
        self.assertIn("המותג לא נמצא", exception_message(plan))
        self.assertIn("לחם חיטה מלא", evaluate_message(plan))


class CadenceStrataTests(Base):
    def test_low_trust_cadence_cannot_cause_high_auto_include_by_itself(self):
        # Tier A by share, last bought long ago, but only a share-estimated cadence.
        self._stock("tivtaam", "c1", "קוטג 5%", share=0.9)
        self.storage.record_last_purchase("tivtaam", [("c1", _day(40))])
        a = need_engine.assess_all(self.storage, CFG, TODAY)[0]
        a = next(x for x in a if x.term == he.key_for("קוטג 5%"))
        self.assertEqual(a.cadence_stratum, "household_fallback")
        self.assertTrue(a.cadence_capped)
        self.assertLessEqual(a.need_confidence, CFG.cadence_ceiling_fallback)
        self.assertNotEqual(a.decision, "auto_include")

    def test_measured_cadence_may_reach_high(self):
        self._stock("tivtaam", "c2", "חלב 3%", share=0.9)
        self._lines("c2", "חלב 3%", [12, 19, 26, 33, 40, 47, 54, 61, 68])
        self.storage.record_last_purchase("tivtaam", [("c2", _day(12))])
        assessments, _ = need_engine.assess_all(self.storage, CFG, TODAY)
        a = next(x for x in assessments if x.term == he.key_for("חלב 3%"))
        self.assertEqual(a.cadence_stratum, "measured")
        self.assertFalse(a.cadence_capped)
        self.assertEqual(a.decision, "auto_include")

    def test_explicit_need_lifts_an_item_above_its_cadence_cap(self):
        self._stock("tivtaam", "c1", "קוטג 5%", share=0.9)
        self.storage.record_last_purchase("tivtaam", [("c1", _day(40))])
        self.storage.add_adhoc_request(text="קוטג 5%", requested_by="ישי")
        assessments, _ = need_engine.assess_all(self.storage, CFG, TODAY)
        a = next(x for x in assessments if x.term == he.key_for("קוטג 5%"))
        self.assertEqual(a.decision, "auto_include")


class StockUpEconomicsTests(Base):
    def _promo(self, term, name, shelf, deal):
        from unittest import mock
        from grocery_bot import household_evidence as he_mod

        ev = he_mod.Evidence(type=he_mod.EvidenceType.promotion, origin=he_mod.Origin.FACT, term=he.key_for(term),
                             store="tivtaam", product_name=name, value=(shelf - deal) / shelf, detail="promo",
                             source_ref=f"dealfill:tivtaam:{term}", trust=0.8,
                             data={"shelf_price": shelf, "deal_price": deal, "quantity": 1, "saving": shelf - deal})
        return ev

    def test_perishable_promotion_is_not_a_stock_up(self):
        from grocery_bot import vnext_economics
        self._stock("tivtaam", "p1", "פלפל אדום", share=0.9, qty=0.6)
        self._lines("p1", "פלפל אדום", [3, 11, 19, 27], qty=0.6)
        evidence = he.gather(self.storage).by_term().get(he.key_for("פלפל אדום"), [])
        result = vnext_economics.assess(self.storage, he.key_for("פלפל אדום"), self._promo("פלפל אדום", "פלפל אדום", 9.9, 4.9), evidence, CFG)
        self.assertFalse(result.worthwhile)
        self.assertTrue(result.economics["perishable"])

    def test_routine_promo_price_is_not_a_stock_up(self):
        from grocery_bot import vnext_economics
        self._stock("tivtaam", "s1", "רוטב סויה", share=0.5)
        self._lines("s1", "רוטב סויה", [10, 40, 70, 100])
        # the "deal" price is what it has cost most recorded days
        rows = [{"barcode": "b1", "name": "רוטב סויה", "price": 10.0 if i % 4 else 20.9, "observed_at": _day(i), "source": "feed"}
                for i in range(1, 30)]
        self.storage.record_store_prices("tivtaam", rows)
        promo = self._promo("רוטב סויה", "רוטב סויה", 20.9, 10.0)
        promo = he.Evidence(**{**promo.__dict__, "data": {**promo.data, "barcode": "b1"}})
        evidence = he.gather(self.storage).by_term().get(he.key_for("רוטב סויה"), [])
        result = vnext_economics.assess(self.storage, he.key_for("רוטב סויה"), promo, evidence, CFG)
        self.assertFalse(result.worthwhile)
        self.assertGreaterEqual(result.economics["routine_promo_share"], CFG.economics_routine_promo_share)

    def test_genuine_shelf_stable_saving_is_a_stock_up_within_consumption(self):
        from grocery_bot import vnext_economics
        self._stock("tivtaam", "s2", "רוטב סויה", share=0.5)
        self._lines("s2", "רוטב סויה", [10, 30, 50, 70])
        rows = [{"barcode": "b2", "name": "רוטב סויה", "price": 20.9, "observed_at": _day(i), "source": "feed"} for i in range(1, 30)]
        self.storage.record_store_prices("tivtaam", rows)
        promo = self._promo("רוטב סויה", "רוטב סויה", 20.9, 10.0)
        promo = he.Evidence(**{**promo.__dict__, "data": {**promo.data, "barcode": "b2"}})
        evidence = he.gather(self.storage).by_term().get(he.key_for("רוטב סויה"), [])
        result = vnext_economics.assess(self.storage, he.key_for("רוטב סויה"), promo, evidence, CFG)
        self.assertTrue(result.worthwhile)
        self.assertGreaterEqual(result.recommended_units, 2)
        self.assertLessEqual(result.recommended_units, CFG.economics_max_units)


if __name__ == "__main__":
    unittest.main()
