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
        adapter._cart_count = mock.MagicMock(side_effect=[3, 3])
        row = mock.MagicMock()
        row.locator.return_value.count.return_value = 1
        result = adapter._add_row(row, "קוטג", 1)
        self.assertEqual(result.status, "error")
        self.assertIn("did not change the cart", result.detail)

    def test_a_click_that_does_change_the_cart_is_added(self):
        adapter = tivtaam.TivTaamAdapter.__new__(tivtaam.TivTaamAdapter)
        adapter.name = "tivtaam"
        adapter._page = mock.MagicMock()
        adapter._cart_count = mock.MagicMock(side_effect=[0, 2])
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
