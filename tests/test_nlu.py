"""Tests for message understanding and the enriched list model.

The model-backed path (`claude -p`) is deliberately not exercised here —
it's a slow external call. What's tested is everything around it: JSON
extraction from replies, the rule-based fallback that runs when the
model is unavailable, and the list fields the parse feeds into.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from grocery_bot.catalog import format_full_list
from grocery_bot.models import AdHocRequest, BaseListItem
from grocery_bot.nlu import _extract_json, _fallback_parse
from grocery_bot.storage import Storage


class ExtractJsonTests(unittest.TestCase):
    def test_reads_a_bare_object(self):
        self.assertEqual(_extract_json('{"intent": "deals"}')["intent"], "deals")

    def test_reads_a_fenced_object(self):
        raw = '```json\n{"intent": "add_item"}\n```'
        self.assertEqual(_extract_json(raw)["intent"], "add_item")

    def test_reads_an_object_with_chatter_around_it(self):
        raw = 'בטח, הנה:\n{"intent": "show_list"}\nמקווה שעזרתי'
        self.assertEqual(_extract_json(raw)["intent"], "show_list")

    def test_raises_when_there_is_no_object(self):
        with self.assertRaises(ValueError):
            _extract_json("no json here")


class FallbackParseTests(unittest.TestCase):
    """The fallback must never repeat the original bug of filing verbs
    and filler words as groceries."""

    def test_strips_the_leading_verb_from_an_add(self):
        parsed = _fallback_parse("תוסיף גבינה בולגרית")
        self.assertEqual(parsed.intent, "add_item")
        self.assertEqual(parsed.items[0].name, "גבינה בולגרית")

    def test_a_bare_ambiguous_word_is_not_filed_as_an_item(self):
        self.assertEqual(_fallback_parse("מה").intent, "unclear")

    def test_recipe_request_does_not_become_an_item(self):
        parsed = _fallback_parse("מתכון לפאי תפוחים")
        self.assertEqual(parsed.intent, "recipe")
        self.assertEqual(parsed.query, "פאי תפוחים")
        self.assertEqual(parsed.items, [])

    def test_price_question_is_routed_to_price(self):
        parsed = _fallback_parse("כמה עולה קוטג")
        self.assertEqual(parsed.intent, "price_query")
        self.assertEqual(parsed.query, "קוטג")

    def test_deals_question(self):
        self.assertEqual(_fallback_parse("מה יש במבצע").intent, "deals")

    def test_removal_is_recognised(self):
        parsed = _fallback_parse("תוריד את הטונה")
        self.assertEqual(parsed.intent, "remove_item")

    def test_plain_multiword_item_is_added(self):
        parsed = _fallback_parse("גבינה צהובה")
        self.assertEqual(parsed.intent, "add_item")
        self.assertEqual(parsed.items[0].name, "גבינה צהובה")


class DescribeTests(unittest.TestCase):
    def test_amount_and_unit_are_shown(self):
        item = BaseListItem(id=1, name="פסטרמה", amount=300, unit="גרם")
        self.assertEqual(item.describe(), "פסטרמה 300 גרם")

    def test_brand_is_shown_in_parentheses(self):
        item = BaseListItem(id=1, name="טונה", amount=4, unit="יחידות", brand="סטארקיסט")
        self.assertEqual(item.describe(), "טונה 4 יחידות (סטארקיסט)")

    def test_plain_quantity_still_renders(self):
        self.assertEqual(BaseListItem(id=1, name="חלב", default_quantity=2).describe(), "חלב x2")

    def test_search_term_includes_brand_so_the_usual_pick_is_found(self):
        item = BaseListItem(id=1, name="טונה", brand="סטארקיסט")
        self.assertEqual(item.search_term_for("shufersal"), "טונה סטארקיסט")

    def test_explicit_store_term_still_wins_over_brand(self):
        item = BaseListItem(
            id=1, name="טונה", brand="סטארקיסט", search_terms={"shufersal": "טונה בשמן"}
        )
        self.assertEqual(item.search_term_for("shufersal"), "טונה בשמן")


class RemovalTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.storage = Storage(str(Path(self._dir.name) / "t.sqlite3"))

    def tearDown(self):
        self._dir.cleanup()

    def test_removes_adhoc_by_fuzzy_name(self):
        self.storage.add_adhoc_request(text="טונה", requested_by="ישי")
        self.assertEqual(self.storage.remove_adhoc_by_name("הטונה"), "טונה")
        self.assertEqual(self.storage.list_pending_adhoc(), [])

    def test_removing_something_absent_reports_none(self):
        self.assertIsNone(self.storage.remove_adhoc_by_name("שוקולד"))

    def test_deactivates_base_item(self):
        self.storage.add_base_list_item(name="טונה", default_quantity=3)
        self.assertEqual(self.storage.deactivate_base_item_by_name("טונה"), "טונה")
        self.assertEqual(self.storage.list_active_base_items(), [])

    def test_amount_unit_and_brand_survive_a_round_trip(self):
        self.storage.add_base_list_item(name="פסטרמה", amount=300, unit="גרם", brand="תנובה")
        item = self.storage.list_active_base_items()[0]
        self.assertEqual((item.amount, item.unit, item.brand), (300, "גרם", "תנובה"))


class FullListTests(unittest.TestCase):
    def test_shows_both_sections(self):
        text = format_full_list(
            [BaseListItem(id=1, name="חלב", default_quantity=2)],
            [AdHocRequest(id=1, text="פסטרמה", requested_by="לירן", created_at="", amount=300, unit="גרם")],
        )
        self.assertIn("חלב x2", text)
        self.assertIn("פסטרמה 300 גרם", text)
        self.assertIn("לירן", text)

    def test_empty_list_says_so(self):
        self.assertIn("ריקה", format_full_list([], []))


if __name__ == "__main__":
    unittest.main()
