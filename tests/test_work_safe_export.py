"""The minimum-safe Gordon -> Work export (2026-09-20).

Proves the specific guarantees this export exists to make: no Gordon
planner conclusion leaks in, no secret/session data can appear, real
per-line purchase evidence (ordered vs. actual quantity, weightable)
survives ingestion and shows up here, a known-bad mapping is correctly
flagged, promotions are absent by design, and none of it needs a
browser/adapter/session to build.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

from grocery_bot import work_safe_export
from grocery_bot.storage import Storage

STORE = "tivtaam"


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))


class NoPlannerConclusionsLeakTests(_Base):
    """Gordon's own due/buy conclusions must never appear here -- they are
    planner output, not evidence, and the audit found the department
    classification they'd rest on is broken for Tiv Taam anyway."""

    def setUp(self):
        super().setUp()
        # Data shaped so a planner-style export WOULD normally produce a
        # due/department signal, to prove this export still excludes it.
        self.storage.replace_stock_items(STORE, [_StockItem(
            product_code="P1", product_name="חלב", share=0.9,
            department="מוצרי חלב וקירור", default_quantity=2, barcode="123",
        )])
        self.storage.record_last_purchase(STORE, [("P1", "2020-01-01")])  # long overdue

    def test_banned_keys_absent_from_every_section(self):
        # The keys themselves must never appear as data -- metadata is
        # allowed to *name* them in its own "excluded_by_design" list
        # (that's documentation, not a leak), so this walks real dict
        # keys rather than substring-matching the whole blob.
        export = work_safe_export.build_export(self.storage, STORE)
        data_only = {k: v for k, v in export.items() if k != "metadata"}
        keys = _all_keys(data_only)
        for banned in ("gordon_due", "due_signal", "due_reason", "department",
                       "standing_list", "buy_or_dont_buy", "plan_status"):
            self.assertNotIn(banned, keys, banned)

    def test_top_level_has_no_promotions_or_planner_sections(self):
        export = work_safe_export.build_export(self.storage, STORE)
        for banned_key in ("promotions", "due_signal", "standing_staples", "gordon_plan"):
            self.assertNotIn(banned_key, export)

    def test_metadata_declares_what_it_excludes(self):
        export = work_safe_export.build_export(self.storage, STORE)
        excluded = export["metadata"]["excluded_by_design"]
        self.assertIn("gordon_due", excluded)
        self.assertIn("promotions", excluded)


class NoSecretsCanAppearTests(_Base):
    def test_secret_markers_absent_from_a_real_export(self):
        self.storage.add_adhoc_request("חלב", "ishay")
        export = work_safe_export.build_export(self.storage, STORE)
        blob = json.dumps(export, ensure_ascii=False).lower()
        for marker in work_safe_export._SECRET_MARKERS:
            self.assertNotIn(marker, blob, marker)

    def test_module_source_never_references_adapters_playwright_or_env_files(self):
        import grocery_bot.work_safe_export as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        for forbidden in ("adapters", "playwright", "Playwright", "storage_state", ".env", "os.environ"):
            self.assertNotIn(forbidden, source, forbidden)


class DoesNotTouchBrowserOrSessionTests(_Base):
    def test_export_builds_with_playwright_and_adapters_unimportable(self):
        blocked_names = (
            "playwright", "playwright.sync_api",
            "grocery_bot.adapters", "grocery_bot.adapters.tivtaam_api",
            "grocery_bot.adapters.shufersal",
        )
        saved = {}
        for name in blocked_names:
            saved[name] = sys.modules.get(name, "__MISSING__")
            sys.modules[name] = None  # forces ImportError on `import <name>`
        try:
            self.storage.add_adhoc_request("חלב", "ishay")
            export = work_safe_export.build_export(self.storage, STORE)
            self.assertIn("pending_needs", export)
            self.assertEqual(len(export["pending_needs"]), 1)
        finally:
            for name, prev in saved.items():
                if prev == "__MISSING__":
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = prev


class RealOrderLineEvidenceTests(_Base):
    """Real ordered-vs-delivered quantity and weightable state, once
    ingested, must show up verbatim in recent_purchase_history and be
    aggregated honestly in observed_purchase_summary."""

    def setUp(self):
        super().setUp()
        self.storage.log_orders(
            [{"code": "O1", "placed_at": "2026-09-12T13:48:25", "total": 20.0, "item_count": 1}],
            store=STORE,
        )
        self.storage.record_tivtaam_order_lines("O1", "2026-09-12", [
            {"code": "2", "barcode": "111", "name": "בננות", "quantity": 0.5,
             "actual_quantity": 0.332, "weighable": True, "price": 8.5, "total": 4.28,
             "substituted": False},
        ])
        self.storage.record_last_purchase(STORE, [("2", "2026-09-12")])

    def test_ordered_and_actual_quantity_both_appear_and_differ(self):
        export = work_safe_export.build_export(self.storage, STORE)
        line = export["recent_purchase_history"]["orders"][0]["lines"][0]
        self.assertEqual(line["ordered_quantity"], 0.5)
        self.assertEqual(line["actual_quantity"], 0.332)
        self.assertNotEqual(line["ordered_quantity"], line["actual_quantity"])

    def test_weighted_product_keeps_actual_quantity_and_flag(self):
        export = work_safe_export.build_export(self.storage, STORE)
        line = export["recent_purchase_history"]["orders"][0]["lines"][0]
        self.assertTrue(line["weightable"])
        self.assertEqual(line["actual_quantity"], 0.332)
        self.assertEqual(line["unit"], 'ק"ג')

    def test_observed_summary_is_derived_from_real_lines_with_sample_size(self):
        export = work_safe_export.build_export(self.storage, STORE)
        summary = next(r for r in export["observed_purchase_summary"] if r["product_code"] == "2")
        self.assertEqual(summary["observed_typical_quantity"]["median"], 0.332)
        self.assertEqual(summary["observed_typical_quantity"]["sample_size"], 1)
        self.assertIn("DERIVED_FROM_OBSERVED_HISTORY", summary["observed_typical_quantity"]["basis"])


class OnlyHumanConfirmedHintsTests(_Base):
    """Round 2 (2026-09-20): the audit found wrong mappings even at
    source='purchase', Gordon's second-highest confidence tier -- so
    provenance alone is no longer trusted to decide inclusion. Only an
    explicit human confirmation earns a hint; everything else is
    omitted, not included-and-flagged."""

    def test_purchase_sourced_hint_is_excluded_even_when_it_looks_resolvable(self):
        self.storage.remember_choice(STORE, "חלב", "P_998877", "חלב 3% עמק", source="purchase")
        export = work_safe_export.build_export(self.storage, STORE)
        self.assertFalse(any(h["household_term"] == "חלב" for h in export["product_hints"]))

    def test_inferred_sourced_hint_is_excluded(self):
        self.storage.remember_choice(STORE, "גרנולה ללא תוספת סוכר", "עוגיות גרנולה ללא תוספת סוכר",
                                      "עוגיות גרנולה ללא תוספת סוכר", source="inferred")
        export = work_safe_export.build_export(self.storage, STORE)
        self.assertFalse(any(h["household_term"] == "גרנולה ללא תוספת סוכר" for h in export["product_hints"]))

    def test_search_sourced_hint_is_excluded(self):
        self.storage.remember_choice(STORE, "משהו", "", "משהו לא ידוע", source="search")
        export = work_safe_export.build_export(self.storage, STORE)
        self.assertFalse(any(h["household_term"] == "משהו" for h in export["product_hints"]))

    def test_human_confirmed_hint_is_included(self):
        self.storage.remember_choice(STORE, "חלב", "P_998877", "חלב 3% עמק", source="human")
        export = work_safe_export.build_export(self.storage, STORE)
        hint = next(h for h in export["product_hints"] if h["household_term"] == "חלב")
        self.assertEqual(hint["provenance"], "human")

    def test_a_pending_need_with_no_human_confirmation_carries_no_hint_anywhere_in_the_export(self):
        # Mirrors the real production case: an inferred (wrong) mapping
        # for a term that is also currently a pending need.
        self.storage.add_adhoc_request("גרנולה ללא תוספת סוכר", "ליראן")
        self.storage.remember_choice(STORE, "גרנולה ללא תוספת סוכר", "עוגיות גרנולה ללא תוספת סוכר",
                                      "עוגיות גרנולה ללא תוספת סוכר", source="inferred")
        export = work_safe_export.build_export(self.storage, STORE)
        need = next(n for n in export["pending_needs"] if n["text"] == "גרנולה ללא תוספת סוכר")
        self.assertNotIn("preferred_product", need)
        self.assertNotIn("product_hint", need)
        self.assertFalse(any(h["household_term"] == "גרנולה ללא תוספת סוכר" for h in export["product_hints"]))


class MachineResolvableFlagTests(_Base):
    """machine_resolvable stays a defensive, transparent flag even on the
    human-confirmed set -- it is not what decides inclusion."""

    def test_code_equals_name_is_flagged_unresolvable_even_when_human_confirmed(self):
        self.storage.remember_choice(STORE, "בייקון", "בייקון", "בייקון", source="human")
        export = work_safe_export.build_export(self.storage, STORE)
        hint = next(h for h in export["product_hints"] if h["household_term"] == "בייקון")
        self.assertFalse(hint["machine_resolvable"])
        self.assertEqual(hint["provenance"], "human")

    def test_a_real_distinct_code_is_flagged_resolvable(self):
        self.storage.remember_choice(STORE, "חלב", "P_998877", "חלב 3% עמק", source="human")
        export = work_safe_export.build_export(self.storage, STORE)
        hint = next(h for h in export["product_hints"] if h["household_term"] == "חלב")
        self.assertTrue(hint["machine_resolvable"])

    def test_empty_code_is_flagged_unresolvable(self):
        self.storage.remember_choice(STORE, "משהו", "", "משהו לא ידוע", source="human")
        export = work_safe_export.build_export(self.storage, STORE)
        hint = next(h for h in export["product_hints"] if h["household_term"] == "משהו")
        self.assertFalse(hint["machine_resolvable"])


class RejectionsAndFailuresTests(_Base):
    def test_explicit_rejections_carry_a_do_not_choose_constraint(self):
        self.storage.reject_product(STORE, "קפה", "P1", "קפה זול", source="human")
        export = work_safe_export.build_export(self.storage, STORE)
        self.assertEqual(len(export["explicit_rejections"]), 1)
        self.assertEqual(export["explicit_rejections"][0]["constraint"], "DO_NOT_CHOOSE")

    def test_repeat_failures_are_labeled_operational_not_preference(self):
        from grocery_bot.models import CartAddResult

        results = [
            CartAddResult(item_name="מלפפונים", store=STORE, status="error", detail="x")
            for _ in range(2)
        ]
        self.storage.record_cart_failures(results, when="2026-09-17T00:00:00")
        results2 = [
            CartAddResult(item_name="מלפפונים", store=STORE, status="error", detail="x")
        ]
        self.storage.record_cart_failures(results2, when="2026-09-18T00:00:00")
        export = work_safe_export.build_export(self.storage, STORE)
        self.assertEqual(len(export["repeat_cart_failures"]), 1)
        self.assertEqual(export["repeat_cart_failures"][0]["kind"], "OPERATIONAL_EVIDENCE_NOT_A_PREFERENCE")


class PromotionsAbsentTests(_Base):
    def test_no_promotions_key_in_this_first_safe_export(self):
        export = work_safe_export.build_export(self.storage, STORE)
        self.assertNotIn("promotions", export)


def _all_keys(obj, found=None):
    found = found if found is not None else set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.add(k)
            _all_keys(v, found)
    elif isinstance(obj, list):
        for item in obj:
            _all_keys(item, found)
    return found


def _StockItem(**kw):
    from grocery_bot.stock import StockItem

    return StockItem(**kw)


if __name__ == "__main__":
    unittest.main()
