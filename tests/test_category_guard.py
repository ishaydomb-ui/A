"""An aisle name is never a product (2026-09-22: 'ירקות' became frozen soup mix)."""
import unittest
from unittest import mock

from grocery_bot import vnext_semantics as v
from grocery_bot.models import CartAddResult, PlanTerm


class CategoryOnlyTests(unittest.TestCase):
    def test_aisle_words(self):
        for t in ("ירקות", "פירות", "פירות וירקות", "הרבה ירקות", "בשר", "גבינות"):
            self.assertTrue(v.category_only(t), t)

    def test_products_are_not_aisles(self):
        for t in ("עגבניות", "חלב", "ירקות למרק", "שוקו אבקה", "מיקס ירקות קפוא"):
            self.assertFalse(v.category_only(t), t)


class CartPathRefusesAislesTests(unittest.TestCase):
    def test_add_terms_marks_an_aisle_ambiguous_without_searching(self):
        import tempfile
        from grocery_bot.orchestrator import add_terms_to_cart
        from grocery_bot.storage import Storage
        storage = Storage(tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False).name)

        class Adapter:
            name = "tivtaam"
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def ensure_session(self): return True
            def search_and_add(self, term, quantity=1):
                raise AssertionError("an aisle name must never be searched")
            def add_specific_product(self, *a, **k):
                raise AssertionError("an aisle name must never be added")

        with mock.patch.dict("os.environ", {"GORDON_BREAKER": "off"}):
            reports = add_terms_to_cart(storage, {"tivtaam": lambda: Adapter()},
                                        [PlanTerm("ירקות", 1, "adhoc", "1")])
        r = reports["tivtaam"]
        self.assertEqual(len(r.ambiguous), 1)
        self.assertIn("קטגוריה", r.ambiguous[0].detail)


if __name__ == "__main__":
    unittest.main()
