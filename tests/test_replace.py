""""X במקום Y" done truthfully — Phase 9 (2026-09-17).

The old handler called adapter.remove_from_cart, which no adapter has:
every replace added X, left Y, and reported a removal attempt that
never happened.
"""
import tempfile
import unittest
from pathlib import Path

from grocery_bot.models import CartAddResult
from grocery_bot.replace import ReplaceOutcome, format_replace, replace_product
from grocery_bot.storage import Storage


class _Shufersal:
    """Has a real per-line remover."""
    name = "shufersal"

    def __init__(self, cart=None, add_status="added"):
        self.cart = cart or []
        self.removed = []
        self.add_status = add_status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ensure_session(self):
        return True

    def cart_summary(self):
        return {"ok": True, "items": list(self.cart), "total": 10.0, "complete": True}

    def search_and_add(self, term, quantity=1):
        if self.add_status != "added":
            return CartAddResult(item_name=term, store=self.name, status=self.add_status, detail="לא נמצא")
        return CartAddResult(item_name=f"{term} 1 ליטר", store=self.name, status="added",
                             product_code=f"P_{term}", verification="verified")

    def add_specific_product(self, name, quantity=1, **kw):
        return CartAddResult(item_name=name, store=self.name, status="added",
                             product_code=kw.get("product_code", ""), verification="verified")

    def remove_item(self, code):
        self.removed.append(code)
        return True


class _TivTaam(_Shufersal):
    """No remover at all."""
    name = "tivtaam"
    remove_item = None


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))


class ReplaceTests(Base):
    def test_shufersal_replace_rejects_old_adds_new_removes_old(self):
        self.storage.remember_choice("shufersal", "חלב תנובה", "P_OLD", "חלב תנובה 3%", source="inferred")
        ad = _Shufersal()
        out = replace_product(self.storage, {"shufersal": lambda: ad}, "shufersal", "חלב תנובה", "חלב יטבתה")
        self.assertTrue(out.added)
        self.assertEqual(out.removed, "removed")
        self.assertEqual(ad.removed, ["P_OLD"])
        self.assertTrue(self.storage.is_rejected("shufersal", "חלב תנובה", "P_OLD"))
        self.assertIsNone(self.storage.preferred_for("shufersal", "חלב תנובה"))
        pref = self.storage.preferred_for("shufersal", "חלב יטבתה")
        self.assertEqual((pref["product_code"], pref["source"]), ("P_חלב יטבתה", "human"))

    def test_tivtaam_replace_adds_and_says_it_cannot_remove(self):
        self.storage.remember_choice("tivtaam", "חלב תנובה", "OLD", "חלב תנובה", source="search")
        ad = _TivTaam()
        out = replace_product(self.storage, {"tivtaam": lambda: ad}, "tivtaam", "חלב תנובה", "חלב יטבתה")
        self.assertTrue(out.added)
        self.assertEqual(out.removed, "unsupported")
        self.assertTrue(self.storage.is_rejected("tivtaam", "חלב תנובה", "OLD"))
        self.assertIn("הסירו את חלב תנובה ידנית", format_replace(out))

    def test_old_is_found_on_the_cart_line_when_nothing_is_remembered(self):
        ad = _Shufersal(cart=[{"code": "P_1", "name": "חלב תנובה 3% 1 ליטר"}, {"code": "P_2", "name": "לחם"}])
        out = replace_product(self.storage, {"shufersal": lambda: ad}, "shufersal", "חלב תנובה", "חלב יטבתה")
        self.assertEqual(out.old_code, "P_1")
        self.assertEqual(ad.removed, ["P_1"])

    def test_when_the_add_fails_the_old_item_is_left_alone(self):
        self.storage.remember_choice("shufersal", "חלב תנובה", "P_OLD", "חלב תנובה", source="search")
        ad = _Shufersal(add_status="not_found")
        out = replace_product(self.storage, {"shufersal": lambda: ad}, "shufersal", "חלב תנובה", "חלב יטבתה")
        self.assertFalse(out.added)
        self.assertEqual(out.removed, "not_attempted")
        self.assertEqual(ad.removed, [])
        self.assertIn("נשאר בעגלה", format_replace(out))
        # The correction itself still stands: the household said "not that one".
        self.assertTrue(self.storage.is_rejected("shufersal", "חלב תנובה", "P_OLD"))

    def test_an_unidentifiable_old_item_is_reported_not_guessed(self):
        ad = _Shufersal(cart=[{"code": "P_2", "name": "לחם"}])
        out = replace_product(self.storage, {"shufersal": lambda: ad}, "shufersal", "חלב תנובה", "חלב יטבתה")
        self.assertTrue(out.added)
        self.assertEqual(out.removed, "unknown_product")
        self.assertEqual(ad.removed, [])
        self.assertIn("לא זיהיתי", format_replace(out))

    def test_a_remover_that_returns_false_is_reported_as_failed(self):
        self.storage.remember_choice("shufersal", "חלב תנובה", "P_OLD", "חלב תנובה", source="search")

        class Flaky(_Shufersal):
            def remove_item(self, code):
                return False
        out = replace_product(self.storage, {"shufersal": lambda: Flaky()}, "shufersal", "חלב תנובה", "חלב יטבתה")
        self.assertEqual(out.removed, "failed")
        self.assertIn("לא הצלחתי להסיר", format_replace(out))

    def test_an_unverified_add_is_flagged_in_the_message(self):
        out = ReplaceOutcome("shufersal", "א", "ב", added=True, verification="unverified", removed="removed")
        self.assertIn("לא אומתה", format_replace(out))

    def test_the_replace_is_a_cart_run_of_its_own(self):
        ad = _Shufersal()
        replace_product(self.storage, {"shufersal": lambda: ad}, "shufersal", "חלב תנובה", "חלב יטבתה")
        import sqlite3
        c = sqlite3.connect(str(Path(self._tmp.name) / "t.sqlite3"))
        self.assertEqual(c.execute("SELECT trigger, status FROM cart_runs").fetchall(), [("replace", "completed")])


if __name__ == "__main__":
    unittest.main()
