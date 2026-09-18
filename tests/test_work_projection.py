"""work_projection.py -- pure-read Work-benchmark projection (2026-09-18).

Enforces the actual boundary the spec cares about: no plancontext /
Playwright touch, no raw SQL, no secret-shaped key or value, ever --
not just "the docstring says so."
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from grocery_bot.storage import Storage
from grocery_bot.work_projection import _SECRET_MARKERS, build_projection


def _walk(value):
    """Every string reachable from a JSON-ish structure."""
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


class ShapeTests(Base):
    def test_the_expected_keys_are_present_even_on_an_empty_db(self):
        projection = build_projection(self.storage, "shufersal")
        for key in ("store", "generated_at", "pending_needs", "preferred_products",
                    "rejections", "quantities", "promotions"):
            self.assertIn(key, projection)
        self.assertEqual(projection["store"], "shufersal")

    def test_pending_needs_reflect_real_adhoc_requests(self):
        self.storage.add_adhoc_request(text="חלב", requested_by="ישי", quantity=2)
        projection = build_projection(self.storage, "shufersal")
        self.assertEqual(projection["pending_needs"], [{"term": "חלב", "quantity": 2}])

    def test_preferred_products_come_from_the_existing_bulk_accessor(self):
        self.storage.remember_choice("shufersal", "חלב", "P_1", "חלב תנובה", source="human")
        projection = build_projection(self.storage, "shufersal")
        self.assertEqual(len(projection["preferred_products"]), 1)
        self.assertEqual(projection["preferred_products"][0]["product_code"], "P_1")

    def test_preferred_products_are_scoped_to_the_requested_store(self):
        self.storage.remember_choice("shufersal", "חלב", "P_1", "חלב תנובה", source="human")
        self.storage.remember_choice("tivtaam", "חלב", "T_1", "חלב יטבתה", source="human")
        projection = build_projection(self.storage, "shufersal")
        self.assertEqual(len(projection["preferred_products"]), 1)
        self.assertEqual(projection["preferred_products"][0]["product_code"], "P_1")

    def test_rejections_come_from_the_existing_bulk_accessor(self):
        self.storage.reject_product("shufersal", "בייקון", "CAM", source="human")
        projection = build_projection(self.storage, "shufersal")
        self.assertEqual(len(projection["rejections"]), 1)
        self.assertEqual(projection["rejections"][0]["product_code"], "CAM")

    def test_a_failure_in_one_section_degrades_to_empty_not_a_crash(self):
        with mock.patch.object(self.storage, "list_preferences", side_effect=RuntimeError("db busy")):
            projection = build_projection(self.storage, "shufersal")
        self.assertEqual(projection["preferred_products"], [])
        self.assertIn("pending_needs", projection)  # the rest of the projection still built


class NoLiveTouchTests(Base):
    """The actual enforcement of section D2: never a browser, never plancontext."""

    def test_plancontext_is_never_imported_by_this_module(self):
        import grocery_bot.work_projection as wp

        # Not just "the word doesn't appear" (the module's own docstring
        # explains *why* it avoids plancontext, which would trip a bare
        # substring check) -- no actual import or attribute reference to
        # it exists, and the module object itself never gained the name.
        self.assertNotIn("plancontext", dir(wp))
        with open(wp.__file__, encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                self.assertFalse(
                    stripped.startswith("import plancontext")
                    or stripped.startswith("from .plancontext")
                    or stripped.startswith("from grocery_bot.plancontext"),
                    f"found a real plancontext import: {line!r}",
                )

    def test_read_carts_is_never_invoked_while_building_a_projection(self):
        with mock.patch("grocery_bot.plancontext._read_carts") as read_carts:
            build_projection(self.storage, "shufersal")
        read_carts.assert_not_called()

    def test_no_adapter_or_playwright_object_is_touched(self):
        # If this module ever imports an adapter class, constructing one
        # here would be a live-session touch by definition; it currently
        # imports nothing Playwright-shaped at all.
        import grocery_bot.work_projection as wp

        with open(wp.__file__, encoding="utf-8") as fh:
            source = fh.read()
        for forbidden in ("playwright", "Adapter(", "ensure_session", "cart_summary("):
            self.assertNotIn(forbidden, source)


class NoSecretsTests(Base):
    def test_no_key_or_value_in_a_populated_projection_matches_a_secret_marker(self):
        self.storage.add_adhoc_request(text="חלב", requested_by="ישי")
        self.storage.remember_choice("shufersal", "חלב", "P_1", "חלב תנובה", source="human")
        self.storage.reject_product("shufersal", "בייקון", "CAM", source="human")
        projection = build_projection(self.storage, "shufersal")
        for text in _walk(projection):
            lowered = text.lower()
            for marker in _SECRET_MARKERS:
                self.assertNotIn(marker, lowered, f"{marker!r} found in projection value {text!r}")

    def test_the_marker_list_itself_covers_the_named_categories(self):
        for expected in ("password", "cookie", "token", "session", "secret"):
            self.assertIn(expected, _SECRET_MARKERS)


class NoRawSqlTests(Base):
    def test_every_storage_call_this_module_makes_is_a_public_method(self):
        """Pins the module to Storage's public surface -- a regression here
        means someone added a direct `._connect()`/SQL string, exactly what
        the spec forbids."""
        import inspect

        from grocery_bot import work_projection

        source = inspect.getsource(work_projection)
        self.assertNotIn("_connect(", source)
        self.assertNotIn("SELECT ", source.upper())


if __name__ == "__main__":
    unittest.main()
