"""vNext Phase 1 — the need engine's pinned rules, on synthetic evidence.

Each test seeds a temp Storage with the smallest set of real rows that
produces the evidence in question, then reads the assessment back. No
test touches a store adapter; nothing here can reach a cart.
"""
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from grocery_bot import household_evidence as he
from grocery_bot import need_engine
from grocery_bot.storage import Storage
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

    def _stock(self, store, code, name, share=0.8, tier="A", qty=1, department=""):
        """`tier` is derived from `share` by StockItem; the argument only
        documents the intent of the test."""
        from grocery_bot.stock import StockItem

        existing = [r for r in self.storage.list_stock_items(store)]
        items = [StockItem(product_code=r["product_code"], product_name=r["product_name"],
                           share=r["share"], department=r["department"], default_quantity=r["default_quantity"])
                 for r in existing]
        items.append(StockItem(product_code=code, product_name=name, share=share,
                               department=department, default_quantity=qty))
        self.storage.replace_stock_items(store, items)

    def _lines(self, code, name, days_ago_list, qty=1.0):
        for i, days in enumerate(days_ago_list):
            self.storage.record_tivtaam_order_lines(f"o{code}{i}", _day(days), [{
                "code": code, "barcode": "", "name": name, "quantity": qty,
                "actual_quantity": qty, "weighable": False,
                "price": 5.0, "total": 5.0 * qty, "substituted": False,
            }])
        self.storage.record_last_purchase("tivtaam", [(code, _day(min(days_ago_list)))])

    def _assess(self, term):
        assessments, _ = need_engine.assess_all(self.storage, CFG, TODAY)
        for a in assessments:
            if a.term == he.key_for(term):
                return a
        return None


class ExplicitNeedOutranksDepletion(Base):
    def test_just_bought_but_explicitly_requested_is_high(self):
        self._stock("tivtaam", "c1", "חלב 3%", share=0.8, tier="A")
        self._lines("c1", "חלב 3%", [1, 8, 15, 22])   # bought yesterday
        self.storage.add_adhoc_request(text="חלב 3%", requested_by="ישי")
        a = self._assess("חלב 3%")
        self.assertIsNotNone(a)
        self.assertTrue(a.mandatory)
        self.assertGreaterEqual(a.need_confidence, CFG.high_confidence)
        self.assertEqual(a.decision, need_engine.AUTO_INCLUDE)
        self.assertEqual(a.depletion.get("state"), "recently bought")


class RecentPurchaseLowersConfidence(Base):
    def test_recent_vs_due(self):
        self._stock("tivtaam", "r1", "ביצים", share=0.7, tier="A")
        self._lines("r1", "ביצים", [1, 8, 15, 22])          # cadence ~7, bought 1d ago
        self._stock("tivtaam", "r2", "גבינה", share=0.7, tier="A")
        recent = self._assess("ביצים")
        self.assertEqual(recent.depletion["state"], "recently bought")
        self.assertLess(recent.need_confidence, CFG.medium_confidence)
        self.assertEqual(recent.decision, need_engine.IGNORE)


class RecurringItemBecomesDue(Base):
    def test_past_cadence_is_likely_due_and_auto(self):
        self._stock("tivtaam", "d1", "קוטג", share=0.7, tier="A")
        self._lines("d1", "קוטג", [9, 16, 23, 30, 37, 44, 51])   # cadence 7, bought 9d ago
        a = self._assess("קוטג")
        self.assertIn(a.depletion["state"], ("likely due", "overdue"))
        self.assertGreaterEqual(a.need_confidence, CFG.high_confidence)
        self.assertEqual(a.decision, need_engine.AUTO_INCLUDE)
        self.assertTrue(a.depletion.get("is_inference"))

    def test_thin_cadence_is_suggested_not_auto(self):
        """Three gaps is enough to call it due, not enough to act unasked."""
        self._stock("tivtaam", "d1", "קוטג", share=0.7, tier="A")
        self._lines("d1", "קוטג", [9, 16, 23, 30])
        a = self._assess("קוטג")
        self.assertIn(a.depletion["state"], ("likely due", "overdue"))
        self.assertEqual(a.decision, need_engine.SUGGEST)
        self.assertLess(a.depletion["cadence_trust"], CFG.cadence_trust_measured)

    def test_far_past_cadence_reads_as_lapsed_not_overdue(self):
        self._stock("tivtaam", "d2", "מרק", share=0.2, tier="C")
        self._lines("d2", "מרק", [200, 207, 214, 221])
        a = self._assess("מרק")
        self.assertTrue(a.depletion["state"].startswith("lapsed"))
        self.assertLess(a.need_confidence, CFG.high_confidence)


class WasteReducesQuantityAndConfidence(Base):
    def test_waste_dampens(self):
        self._stock("tivtaam", "w1", "חסה", share=0.7, tier="A", qty=2)
        self._lines("w1", "חסה", [9, 16, 23, 30], qty=2.0)
        before = self._assess("חסה")
        self.storage.record_waste([("חסה", 0.5, _day(2), "ישי"), ("חסה", 0.5, _day(9), "ישי")])
        after = self._assess("חסה")
        self.assertLess(after.recommended_quantity, before.recommended_quantity)
        self.assertLess(after.quantity_confidence, before.quantity_confidence)
        self.assertLess(after.need_confidence, before.need_confidence)
        self.assertTrue(any("waste" in r for r in after.reasons))


class MealCreatesDemandWithoutInventory(Base):
    def test_ingredients_become_needs_flagged_unknown_inventory(self):
        self.storage.add_vnext_planned_meal("טאקו", _day(-3), ["טורטיות", "בשר טחון", "מלח"], declared_by="ישי")
        a = self._assess("טורטיות")
        self.assertIsNotNone(a)
        self.assertEqual(a.meal_or_event, "טאקו")
        self.assertFalse(a.inventory_known)
        self.assertGreaterEqual(a.need_confidence, CFG.medium_confidence)
        # a pantry staple is demoted, not assumed absent
        salt = self._assess("מלח")
        self.assertIsNotNone(salt)
        self.assertLess(salt.need_confidence, a.need_confidence)
        self.assertTrue(any("inventory NOT known" in r for r in salt.reasons))
        # the meal row itself is not a need
        self.assertIsNone(self._assess("טאקו"))

    def test_meal_outside_lookahead_is_ignored(self):
        self.storage.add_vnext_planned_meal("פסטה", _day(-30), ["פסטה ספגטי"])
        self.assertIsNone(self._assess("פסטה ספגטי"))


class PromotionRules(Base):
    def _promo_evidence(self, term, discount, saving, trust=0.8):
        return he.Evidence(type=he.EvidenceType.promotion, origin=he.Origin.FACT, term=he.key_for(term),
                           store="shufersal", product_name=term, value=discount, detail="promo",
                           source_ref="dealfill:x", trust=trust, data={"saving": saving, "quantity": 1})

    def _history(self, term, tier="B"):
        return [
            he.Evidence(type=he.EvidenceType.purchase_history, origin=he.Origin.FACT, term=he.key_for(term),
                        store="shufersal", product_code="p1", product_name=term, value=0.5,
                        source_ref="stock_items:shufersal:p1", data={"tier": tier, "department": "מזווה ושימורים"}),
            he.Evidence(type=he.EvidenceType.purchase_cadence, origin=he.Origin.INFERENCE, term=he.key_for(term),
                        store="shufersal", value=30.0, trust=0.5, source_ref="derived:p1"),
            he.Evidence(type=he.EvidenceType.purchase_recency, origin=he.Origin.FACT, term=he.key_for(term),
                        store="shufersal", value=10.0, source_ref="last_purchase:p1"),
        ]

    def test_good_promotion_makes_stock_up_candidate(self):
        ev = self._history("טונה") + [self._promo_evidence("טונה", 0.40, 6.0)]
        a = need_engine.assess_term(he.key_for("טונה"), ev, CFG)
        self.assertTrue(a.stock_up)
        self.assertGreaterEqual(a.recommended_quantity, CFG.stockup_quantity)
        self.assertIn(a.decision, (need_engine.SUGGEST, need_engine.AUTO_INCLUDE))

    def test_mediocre_promotion_creates_no_demand(self):
        # 8% off something never bought: no history, no standing list.
        ev = [self._promo_evidence("שוקולד חדש", 0.08, 1.0)]
        a = need_engine.assess_term(he.key_for("שוקולד חדש"), ev, CFG)
        self.assertFalse(a.stock_up)
        self.assertEqual(a.decision, need_engine.IGNORE)

    def test_mediocre_promotion_on_recurring_item_changes_nothing(self):
        base = need_engine.assess_term("x", self._history("טונה"), CFG)
        with_promo = need_engine.assess_term("x", self._history("טונה") + [self._promo_evidence("טונה", 0.10, 1.0)], CFG)
        self.assertFalse(with_promo.stock_up)
        self.assertEqual(with_promo.recommended_quantity, base.recommended_quantity)
        self.assertAlmostEqual(with_promo.need_confidence, base.need_confidence)

    def test_household_stockup_rule_lowers_the_bar(self):
        self.storage.add_vnext_stockup_rule("קפה", min_discount=0.10, max_quantity=3)
        rules = self.storage.list_vnext_stockup_rules()
        ev = [self._promo_evidence("קפה", 0.15, 4.0)]
        a = need_engine.assess_term(he.key_for("קפה"), ev, CFG, rules)
        self.assertTrue(a.stock_up)
        self.assertEqual(a.recommended_quantity, 3)


class RejectionRemovesProductNotNeed(Base):
    def test_rejected_product_dropped_need_kept(self):
        self._stock("tivtaam", "j1", "יוגורט", share=0.7, tier="A")
        self._lines("j1", "יוגורט", [9, 16, 23, 30])
        self.storage.remember_choice("tivtaam", "יוגורט", "j1", "יוגורט", source="purchase")
        before = self._assess("יוגורט")
        self.assertTrue(any(c.product_code == "j1" for c in before.candidate_products))
        self.storage.reject_product("tivtaam", "יוגורט", "j1", "יוגורט", source="human")
        after = self._assess("יוגורט")
        self.assertIsNotNone(after)
        self.assertFalse(any(c.product_code == "j1" for c in after.candidate_products))
        self.assertIn("j1", after.rejected_products)
        self.assertGreaterEqual(after.need_confidence, CFG.medium_confidence)


class InferredProductStaysInference(Base):
    def test_resolver_preference_is_not_human(self):
        self.storage.remember_choice("tivtaam", "גזר", "g1", "גזר ארוז", source="purchase")
        self.storage.add_adhoc_request(text="גזר", requested_by="לירן")
        a = self._assess("גזר")
        self.assertEqual(len(a.candidate_products), 1)
        self.assertEqual(a.candidate_products[0].origin, he.Origin.INFERENCE)
        # Phase 1.5: an inferred-but-clean product is Gordon's to resolve,
        # not a question for the household (spec §5B).
        self.assertEqual(a.exception_class, "agent_resolvable")
        self.assertIn("inferred", a.exception_reason)
        self.assertEqual(a.display_name, "גזר")

    def test_human_preference_is_human(self):
        self.storage.remember_choice("tivtaam", "גזר", "g1", "גזר ארוז", source="human")
        self.storage.add_adhoc_request(text="גזר", requested_by="לירן")
        a = self._assess("גזר")
        self.assertEqual(a.candidate_products[0].origin, he.Origin.HUMAN_DECLARED)
        self.assertFalse(any("inferred" in u for u in a.unresolved))


class ConfigFromEnv(unittest.TestCase):
    def test_env_overrides_and_bad_values_ignored(self):
        cfg = VNextConfig.from_env({"GORDON_VNEXT_HIGH_CONFIDENCE": "0.9",
                                    "GORDON_VNEXT_STOCKUP_QUANTITY": "4",
                                    "GORDON_VNEXT_RECENT_FACTOR": "not-a-number"})
        self.assertEqual(cfg.high_confidence, 0.9)
        self.assertEqual(cfg.stockup_quantity, 4)
        self.assertEqual(cfg.recent_factor, VNextConfig().recent_factor)


if __name__ == "__main__":
    unittest.main()
