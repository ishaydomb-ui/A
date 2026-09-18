"""work_planner_snapshot.py -- the richer, pure-read planner export
(2026-09-18). Separate from work_projection.py; asserts the same safety
boundary independently rather than assuming it carries over, plus the
shape/labelling rules specific to this export (source labels, honest
gaps for fields Gordon does not measure).
"""
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from grocery_bot.storage import Storage
from grocery_bot.stock import StockItem
from grocery_bot.work_planner_snapshot import _SECRET_MARKERS, build_snapshot


def _walk(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _walk(v)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)
    elif value is not None:
        yield str(value)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    _stock_items: dict

    def _stock(self, store, code, name, tier="A", share=0.5, barcode="", interval_days=None,
              department="מזווה ושימורים"):
        # replace_stock_items replaces the WHOLE store's list, so repeated
        # calls accumulate here rather than wiping each other out.
        if not hasattr(self, "_stock_items"):
            self._stock_items = {}
        self._stock_items.setdefault(store, {})[code] = StockItem(code, name, share, department)
        self.storage.replace_stock_items(store, list(self._stock_items[store].values()))
        # `tier` is a computed property on StockItem (from `share`), not a
        # constructor field -- set directly here so tests can pick a tier
        # independent of the share value the due-signal math also uses.
        from contextlib import closing
        with closing(self.storage._connect()) as conn:  # noqa: SLF001
            conn.execute(
                "UPDATE stock_items SET barcode=?, interval_days=?, tier=? WHERE store=? AND product_code=?",
                (barcode, interval_days, tier, store, code),
            )
            conn.commit()


class ShapeTests(Base):
    def test_top_level_keys_on_an_empty_db(self):
        snap = build_snapshot(self.storage, "tivtaam")
        for key in ("generated_at", "store", "household_needs", "staples",
                    "promotions", "retailer_context", "data_quality"):
            self.assertIn(key, snap)
        self.assertEqual(snap["store"], "tivtaam")

    def test_household_needs_reflect_real_pending_requests(self):
        self.storage.add_adhoc_request(text="חלב", requested_by="ישי", quantity=2)
        snap = build_snapshot(self.storage, "tivtaam")
        self.assertEqual(len(snap["household_needs"]), 1)
        need = snap["household_needs"][0]
        self.assertEqual(need["term"], "חלב")
        self.assertEqual(need["requested_quantity"], 2)
        self.assertIsNone(need["preferred_product"])

    def test_a_household_need_with_a_remembered_choice_carries_it(self):
        self.storage.add_adhoc_request(text="חלב", requested_by="ישי")
        self.storage.remember_choice("tivtaam", "חלב", "P_1", "חלב תנובה 3%", source="human")
        snap = build_snapshot(self.storage, "tivtaam")
        pref = snap["household_needs"][0]["preferred_product"]
        self.assertEqual(pref["product_code"], "P_1")
        self.assertEqual(pref["source"], "human")

    def test_only_tier_a_b_c_staples_are_included(self):
        self._stock("tivtaam", "1", "חלב", tier="A")
        self._stock("tivtaam", "2", "מוצר נדיר", tier="D")
        terms = {s["term"] for s in build_snapshot(self.storage, "tivtaam")["staples"]}
        self.assertIn("חלב", terms)
        self.assertNotIn("מוצר נדיר", terms)

    def test_a_staples_preferred_product_and_rejection_are_both_surfaced(self):
        self._stock("tivtaam", "1", "חלב", tier="A")
        self.storage.remember_choice("tivtaam", "חלב", "P_1", "חלב תנובה", source="purchase")
        self.storage.reject_product("tivtaam", "חלב", "P_2", "חלב אחר", source="human")
        staple = build_snapshot(self.storage, "tivtaam")["staples"][0]
        self.assertEqual(staple["preferred_product"]["source"], "purchase")
        self.assertEqual(staple["rejections"], [
            {"product_code": "P_2", "product_name": "חלב אחר", "source": "human"}
        ])

    def test_accepted_substitutions_is_always_an_empty_list(self):
        self._stock("tivtaam", "1", "חלב", tier="A")
        staple = build_snapshot(self.storage, "tivtaam")["staples"][0]
        self.assertEqual(staple["accepted_substitutions"], [])

    def test_a_failure_in_one_section_degrades_to_empty_not_a_crash(self):
        with mock.patch.object(self.storage, "list_stock_items", side_effect=RuntimeError("db busy")):
            snap = build_snapshot(self.storage, "tivtaam")
        self.assertEqual(snap["staples"], [])
        self.assertIn("household_needs", snap)


class DueSignalTests(Base):
    """The part that exposes Gordon's own conclusion alongside the raw data."""

    def test_a_pantryable_staple_bought_long_ago_reads_as_due(self):
        # share=0.5 -> expected interval ~16 days (1/0.5 * 8); bought 30 days ago.
        self._stock("tivtaam", "1", "שמן זית", tier="A", share=0.5)
        old = (date.today() - timedelta(days=30)).isoformat()
        from contextlib import closing
        with closing(self.storage._connect()) as conn:  # noqa: SLF001
            conn.execute("INSERT INTO last_purchase (store, product_code, purchased_on) VALUES (?,?,?)",
                         ("tivtaam", "1", old))
            conn.commit()
        staple = build_snapshot(self.storage, "tivtaam")["staples"][0]
        self.assertEqual(staple["due_signal"], "due")
        self.assertTrue(staple["gordon_due"])
        self.assertEqual(staple["last_purchase_days_ago"], 30)
        self.assertIsNotNone(staple["typical_interval_days"])
        self.assertIn("since last purchase", staple["due_reason"])

    def test_a_measured_chain_interval_is_named_as_the_basis_when_present(self):
        self._stock("tivtaam", "1", "שמן זית", tier="A", share=0.5, interval_days=90.0)
        old = (date.today() - timedelta(days=10)).isoformat()
        from contextlib import closing
        with closing(self.storage._connect()) as conn:  # noqa: SLF001
            conn.execute("INSERT INTO last_purchase (store, product_code, purchased_on) VALUES (?,?,?)",
                         ("tivtaam", "1", old))
            conn.commit()
        staple = build_snapshot(self.storage, "tivtaam")["staples"][0]
        self.assertEqual(staple["typical_interval_days"], 90.0)
        self.assertIn("chain's own measured interval", staple["due_reason"])

    def test_a_non_pantryable_staple_is_not_modeled_not_fabricated_due(self):
        # "מוצרי חלב" (dairy) is not in radar.PANTRYABLE_DEPARTMENTS.
        self._stock("tivtaam", "1", "חלב", tier="A", share=0.9, department="מוצרי חלב")
        staple = build_snapshot(self.storage, "tivtaam")["staples"][0]
        self.assertEqual(staple["due_signal"], "not_modeled")
        self.assertFalse(staple["gordon_due"])
        self.assertIn("not interval-tracked", staple["due_reason"])


class QuantityLabelTests(Base):
    def test_typical_quantity_is_labelled_as_a_planner_default_not_measured(self):
        self._stock("tivtaam", "1", "חלב", tier="A")
        staple = build_snapshot(self.storage, "tivtaam")["staples"][0]
        self.assertEqual(staple["typical_quantity"]["basis"], "planner_default")


class RecentPricesTests(Base):
    def test_recent_prices_use_the_new_order_sourced_accessor(self):
        self._stock("tivtaam", "1", "חלב", tier="A", barcode="7290000000001")
        self.storage.record_store_prices("tivtaam", [
            {"barcode": "7290000000001", "name": "חלב", "price": 6.9,
             "observed_at": "2026-09-01", "source": "order"},
        ])
        staple = build_snapshot(self.storage, "tivtaam")["staples"][0]
        self.assertEqual(staple["recent_prices"], [{"date": "2026-09-01", "price": 6.9}])

    def test_no_barcode_means_no_recent_price_lookup_not_a_crash(self):
        self._stock("tivtaam", "1", "חלב", tier="A", barcode="")
        staple = build_snapshot(self.storage, "tivtaam")["staples"][0]
        self.assertEqual(staple["recent_prices"], [])


class NoLiveTouchTests(Base):
    def test_plancontext_is_never_imported(self):
        import grocery_bot.work_planner_snapshot as wps

        self.assertNotIn("plancontext", dir(wps))
        with open(wps.__file__, encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                self.assertFalse(
                    stripped.startswith("import plancontext")
                    or stripped.startswith("from .plancontext")
                    or stripped.startswith("from grocery_bot.plancontext"),
                    f"found a real plancontext import: {line!r}",
                )

    def test_read_carts_is_never_invoked(self):
        self._stock("tivtaam", "1", "חלב", tier="A")
        with mock.patch("grocery_bot.plancontext._read_carts") as read_carts:
            build_snapshot(self.storage, "tivtaam")
        read_carts.assert_not_called()

    def test_no_adapter_or_playwright_object_is_touched(self):
        import grocery_bot.work_planner_snapshot as wps

        with open(wps.__file__, encoding="utf-8") as fh:
            source = fh.read()
        for forbidden in ("playwright", "Adapter(", "ensure_session", "cart_summary("):
            self.assertNotIn(forbidden, source)

    def test_no_raw_sql_in_this_module(self):
        import inspect

        from grocery_bot import work_planner_snapshot

        source = inspect.getsource(work_planner_snapshot)
        self.assertNotIn("_connect(", source)
        self.assertNotIn("SELECT ", source.upper())

    def test_nothing_is_written_back_to_preferences_rejections_or_stock(self):
        self._stock("tivtaam", "1", "חלב", tier="A")
        before_prefs = self.storage.list_preferences("tivtaam")
        before_rejections = self.storage.list_rejections("tivtaam")
        before_stock = self.storage.list_stock_items("tivtaam")
        build_snapshot(self.storage, "tivtaam")
        self.assertEqual(self.storage.list_preferences("tivtaam"), before_prefs)
        self.assertEqual(self.storage.list_rejections("tivtaam"), before_rejections)
        self.assertEqual(self.storage.list_stock_items("tivtaam"), before_stock)


class NoSecretsTests(Base):
    def test_no_key_or_value_matches_a_secret_marker(self):
        self.storage.add_adhoc_request(text="חלב", requested_by="ישי")
        self._stock("tivtaam", "1", "חלב", tier="A", barcode="7290000000001")
        self.storage.remember_choice("tivtaam", "חלב", "P_1", "חלב תנובה", source="human")
        self.storage.reject_product("tivtaam", "חלב", "P_2", "חלב אחר", source="human")
        self.storage.record_store_prices("tivtaam", [
            {"barcode": "7290000000001", "name": "חלב", "price": 6.9,
             "observed_at": "2026-09-01", "source": "order"},
        ])
        snap = build_snapshot(self.storage, "tivtaam")
        for text in _walk(snap):
            lowered = text.lower()
            for marker in _SECRET_MARKERS:
                self.assertNotIn(marker, lowered, f"{marker!r} found in {text!r}")

    def test_marker_list_covers_the_named_categories(self):
        for expected in ("password", "cookie", "token", "session", "secret"):
            self.assertIn(expected, _SECRET_MARKERS)


class RetailerContextTests(Base):
    def test_context_is_static_no_live_read(self):
        ctx = build_snapshot(self.storage, "tivtaam")["retailer_context"]
        self.assertEqual(ctx["store"], "tivtaam")
        self.assertIn("display_name", ctx)
        self.assertIn("is_regular_chain", ctx)
        self.assertIn("cart_fill_supported", ctx)


if __name__ == "__main__":
    unittest.main()
