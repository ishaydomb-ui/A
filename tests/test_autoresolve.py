import tempfile
import unittest
from pathlib import Path

from grocery_bot.models import CartAddResult
from grocery_bot.orchestrator import run_order_cycle
from grocery_bot.storage import Storage


def _card(name, code, price="6.10"):
    return {"name": name, "code": code, "price": price, "size": "250 גרם", "brand": "תנובה"}


class _SearchAdapter:
    """Returns a fixed candidate list, and records what was added."""

    name = "shufersal"

    def __init__(self, cards):
        self._cards = cards
        self.specific_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def search_and_add(self, term, quantity=1):
        return CartAddResult(
            item_name=term, store="shufersal", status="ambiguous",
            candidates=[c["name"] for c in self._cards],
            candidate_cards=self._cards, quantity=quantity,
        )

    def add_specific_product(self, label, quantity=1, product_code="", search_term=""):
        self.specific_calls.append((label, product_code))
        return CartAddResult(
            item_name=label, store="shufersal", status="added",
            quantity=quantity, product_code=product_code,
        )


class AutoResolveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.storage = Storage(str(Path(self._tmpdir.name) / "t.sqlite3"))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _cycle(self, adapter):
        return run_order_cycle(self.storage, {"shufersal": lambda: adapter})["shufersal"]

    def test_a_previously_bought_product_is_picked_without_asking(self) -> None:
        self.storage.add_base_list_item("תפוחי עץ")
        self.storage.remember_choice("shufersal", "תפוח עץ סמיט", "P_SMIT", "תפוח עץ סמיט")
        adapter = _SearchAdapter([_card("תפוח עץ גאלה", "P_GALA"), _card("תפוח עץ סמיט", "P_SMIT")])

        report = self._cycle(adapter)

        self.assertEqual(report.ambiguous, [])
        self.assertEqual(len(report.added), 1)
        self.assertEqual(adapter.specific_calls[0][1], "P_SMIT")
        self.assertEqual(self.storage.list_pending_ambiguities(), [])

    def test_an_auto_pick_is_flagged_so_it_is_not_silent(self) -> None:
        self.storage.add_base_list_item("תפוחי עץ")
        self.storage.remember_choice("shufersal", "תפוח עץ סמיט", "P_SMIT", "תפוח עץ סמיט")
        report = self._cycle(
            _SearchAdapter([_card("תפוח עץ גאלה", "P_GALA"), _card("תפוח עץ סמיט", "P_SMIT")])
        )
        self.assertEqual(report.added[0].auto_resolved, "history")

    def test_the_auto_pick_is_remembered_for_the_requested_term(self) -> None:
        """So the same search doesn't re-derive it every cycle."""
        self.storage.add_base_list_item("תפוחי עץ")
        self.storage.remember_choice("shufersal", "תפוח עץ סמיט", "P_SMIT", "תפוח עץ סמיט")
        self._cycle(_SearchAdapter([_card("תפוח עץ גאלה", "P_GALA"), _card("תפוח עץ סמיט", "P_SMIT")]))
        self.assertEqual(
            self.storage.preferred_for("shufersal", "תפוחי עץ")["product_code"], "P_SMIT"
        )

    def test_two_known_products_still_ask(self) -> None:
        self.storage.add_base_list_item("ביצים")
        self.storage.remember_choice("shufersal", "ביצים L", "P_L", "ביצים L")
        self.storage.remember_choice("shufersal", "ביצים XL", "P_XL", "ביצים XL")
        report = self._cycle(_SearchAdapter([_card("ביצים L", "P_L"), _card("ביצים XL", "P_XL")]))
        self.assertEqual(len(report.ambiguous), 1)

    def test_an_unknown_item_still_asks_and_stores_the_cards(self) -> None:
        self.storage.add_base_list_item("גבינה בולגרית")
        cards = [_card("בולגרית 5%", "P_1"), _card("בולגרית 16%", "P_2")]
        report = self._cycle(_SearchAdapter(cards))
        self.assertEqual(len(report.ambiguous), 1)
        pending = self.storage.list_pending_ambiguities()[0]
        self.assertEqual([c["code"] for c in pending["candidate_cards"]], ["P_1", "P_2"])


if __name__ == "__main__":
    unittest.main()
