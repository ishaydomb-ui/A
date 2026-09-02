"""The Tiv Taam cart adapter, and the boundary it must never cross."""
import pathlib
import unittest
from unittest import mock

from grocery_bot import chains
from grocery_bot.adapters import tivtaam
from grocery_bot.adapters.base import StoreAdapter


class SafetyTest(unittest.TestCase):
    """The hard rule: fill a cart, never pay."""

    def setUp(self):
        # Executable code only. The module docstring names `_checkout`
        # precisely to say it must never be called, and a naive text
        # search cannot tell a prohibition from a call.
        import ast

        source = pathlib.Path("grocery_bot/adapters/tivtaam.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(
                    body[0].value, ast.Constant
                ) and isinstance(body[0].value.value, str):
                    body.pop(0)
        self.code = ast.unparse(tree)

    def test_no_checkout_verb_exists_in_the_adapter(self):
        # Self-Point exposes _checkout. It must not be called, wrapped, or
        # reachable. A future edit has to delete this test on purpose.
        for forbidden in ("_checkout", "checkoutUpdate", "cancelTokenPayment",
                          "place_order", "submit_order"):
            self.assertNotIn(forbidden, self.code, forbidden)

    def test_the_adapter_exposes_no_payment_method(self):
        for name in dir(tivtaam.TivTaamAdapter):
            self.assertNotIn("checkout", name.lower())
            self.assertNotIn("pay", name.lower().replace("display", ""))

    def test_it_implements_the_shared_interface(self):
        self.assertTrue(issubclass(tivtaam.TivTaamAdapter, StoreAdapter))


class ConstructionTest(unittest.TestCase):
    def test_refuses_to_start_without_the_israeli_exit(self):
        # The geo-block answers 200 with a block page, so without a proxy
        # every later failure looks like a broken selector.
        with self.assertRaises(RuntimeError) as caught:
            tivtaam.TivTaamAdapter("data/sessions/tivtaam_storage_state.json", proxy="")
        self.assertIn("geo-block", str(caught.exception))

    def test_refuses_to_start_without_a_session(self):
        with self.assertRaises(RuntimeError) as caught:
            tivtaam.TivTaamAdapter("/nonexistent/session.json", proxy="socks5://x:1")
        self.assertIn("selfpoint_login", str(caught.exception))


class RowScopingTest(unittest.TestCase):
    """Why the adapter never indexes one list against another."""

    def test_a_row_without_an_add_button_is_reported_not_clicked(self):
        adapter = tivtaam.TivTaamAdapter.__new__(tivtaam.TivTaamAdapter)
        adapter.name = "tivtaam"
        row = mock.MagicMock()
        row.locator.return_value.count.return_value = 0
        result = adapter._add_row(row, "קוטג' 5% מהדרין", 1)
        self.assertEqual(result.status, "not_found")
        self.assertIn("out of stock", result.detail)

    def test_a_click_that_does_not_change_the_cart_is_an_error(self):
        # Live search returned 27 names against 26 buttons: one product
        # had no add control, which shifted every later pairing by one and
        # added a different product than the one asked for.
        adapter = tivtaam.TivTaamAdapter.__new__(tivtaam.TivTaamAdapter)
        adapter.name = "tivtaam"
        adapter._page = mock.MagicMock()
        # Counted on the cart's line elements, not the header. The header
        # count was seen reading 0 on a cart that held an item, which
        # reported two real adds as failures against the live site on
        # 2026-09-02; a returned value rather than a two-item side_effect
        # because the check now polls for the DOM to catch up.
        adapter._cart_line_count = mock.MagicMock(return_value=3)
        row = mock.MagicMock()
        row.locator.return_value.count.return_value = 1
        result = adapter._add_row(row, "קוטג", 1)
        self.assertEqual(result.status, "error")
        self.assertIn("did not change the cart", result.detail)

    def test_a_click_that_does_change_the_cart_is_added(self):
        adapter = tivtaam.TivTaamAdapter.__new__(tivtaam.TivTaamAdapter)
        adapter.name = "tivtaam"
        adapter._page = mock.MagicMock()
        adapter._cart_line_count = mock.MagicMock(side_effect=[0, 2])
        row = mock.MagicMock()
        row.locator.return_value.count.return_value = 1
        result = adapter._add_row(row, "קוטג", 1)
        self.assertEqual(result.status, "added")


class RegistrationTest(unittest.TestCase):
    def test_tivtaam_is_now_cart_capable(self):
        self.assertTrue(chains.can_fill_cart("tivtaam"))

    def test_price_only_chains_are_still_not_cart_capable(self):
        for chain in ("osherad", "ramilevy", "victory", "politzer"):
            self.assertFalse(chains.can_fill_cart(chain), chain)


if __name__ == "__main__":
    unittest.main()


class CartSummaryContractTest(unittest.TestCase):
    """Tiv Taam's cart reader, and the boundary it must not cross.

    Verified against the real account on 2026-09-02: an empty cart reads
    total 0.0 with no lines; one added product reads ₪42.20 (₪12.30 for
    the item plus ₪29.90 delivery) with the line named
    "במבה מאנצ בטעם חמוץ חריף 150 גרם"; clearing returns to 0.0 and no
    lines. The delivery fee is why the panel's own total is read rather
    than summing what we added.
    """

    def _adapter(self):
        adapter = tivtaam.TivTaamAdapter.__new__(tivtaam.TivTaamAdapter)
        adapter.name = "tivtaam"
        adapter._page = mock.MagicMock()
        adapter._opened = True
        adapter._reopen = mock.MagicMock()
        return adapter

    def test_an_unreadable_summary_is_not_an_empty_cart(self):
        # The distinction this adapter got wrong: the old reader returned
        # 0 when it matched nothing, so "I cannot see the cart" and "the
        # cart is empty" were the same value. ok=False says which.
        adapter = self._adapter()
        adapter._summary_text = mock.MagicMock(return_value=None)
        summary = adapter.cart_summary()
        self.assertFalse(summary["ok"])
        self.assertIsNone(summary["total"])

    def test_the_total_comes_from_the_panel_including_delivery(self):
        adapter = self._adapter()
        adapter._summary_text = mock.MagicMock(
            return_value='1 מוצרים\n1 מוצרים בעגלה\nסך הכל\n₪42.20\nלתשלום'
        )
        adapter._cart_line_names = mock.MagicMock(
            return_value=[{"qty": "1", "brand": "אסם", "name": "במבה", "price": "₪12.30"}]
        )
        summary = adapter.cart_summary()
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["total"], 42.20)
        self.assertEqual(len(summary["items"]), 1)

    def test_an_empty_cart_reads_as_empty_not_as_broken(self):
        adapter = self._adapter()
        adapter._summary_text = mock.MagicMock(
            return_value='0 מוצרים\n0 מוצרים בעגלה\nסך הכל\n₪0.00\nלתשלום'
        )
        adapter._cart_line_names = mock.MagicMock(return_value=[])
        summary = adapter.cart_summary()
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["total"], 0.0)
        self.assertEqual(summary["items"], [])

    def test_a_count_without_readable_lines_is_not_reported_as_empty(self):
        # The header knows there are two lines but the panel would not
        # open. Reporting zero items beside a non-zero total would read as
        # a cart that costs money and contains nothing.
        adapter = self._adapter()
        adapter._summary_text = mock.MagicMock(
            return_value='2 מוצרים\n2 מוצרים בעגלה\nסך הכל\n₪39.90\nלתשלום'
        )
        adapter._cart_line_names = mock.MagicMock(return_value=[])
        summary = adapter.cart_summary()
        self.assertEqual(len(summary["items"]), 2)

    def test_clear_cart_verifies_on_lines_not_on_the_header_count(self):
        # An earlier version trusted the header, which read 0 on a cart
        # that still held an item — so it reported success on a cart it
        # had not emptied. For a method whose whole job is putting the
        # household's cart back as it found it, that is the worst
        # available failure.
        adapter = self._adapter()
        adapter._open_cart_panel = mock.MagicMock(return_value=True)
        adapter._cart_count = mock.MagicMock(return_value=0)
        still_there = mock.MagicMock()
        still_there.count.return_value = 1
        adapter._page.locator.return_value = still_there
        self.assertFalse(adapter.clear_cart())

    # No checkout guard here on purpose: `SafetyTest` above already does
    # it properly, parsing the module and stripping docstrings first —
    # this file's own docstring names `_checkout` in order to forbid it,
    # and a plain text search cannot tell a prohibition from a call. Worth
    # knowing that the panel this reader opens also holds the pay button
    # (`.button.highlight.order`, `sideNavCtrl.checkoutV2`): reading the
    # total is allowed, and nothing here may go further.
