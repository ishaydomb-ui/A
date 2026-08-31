import unittest
from unittest import mock

from grocery_bot.adapters import selfpoint


def _product(barcode, name, price):
    return {
        "localBarcode": barcode,
        "names": {"1": {"short": name}},
        "branch": {"regularPrice": price},
    }


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class RetailerRegistryTest(unittest.TestCase):
    def test_both_chains_are_registered_with_a_branch(self):
        for key in ("tivtaam", "victory"):
            retailer = selfpoint.RETAILERS[key]
            self.assertTrue(retailer.retailer_id)
            # A price is per branch, not per chain — a retailer without one
            # would silently price the wrong store.
            self.assertTrue(retailer.branch_id)


class PricesByBarcodeTest(unittest.TestCase):
    def setUp(self):
        self.client = selfpoint.SelfPointPrices("victory", proxy="socks5://x:1")

    def _with_response(self, payload):
        return mock.patch.object(
            self.client._http, "get", return_value=FakeResponse(payload)
        )

    def test_maps_barcode_to_price_and_name(self):
        payload = {"total": 1, "products": [_product("7290004131074", "חלב 3%", 7.35)]}
        with self._with_response(payload):
            result = self.client.prices_by_barcode(["7290004131074"])
        self.assertEqual(result["7290004131074"]["price"], 7.35)
        self.assertEqual(result["7290004131074"]["name"], "חלב 3%")
        self.assertEqual(result["7290004131074"]["store"], "victory")

    def test_barcode_the_branch_does_not_carry_is_absent_not_guessed(self):
        payload = {"total": 1, "products": [_product("7290004131074", "חלב", 7.35)]}
        with self._with_response(payload):
            result = self.client.prices_by_barcode(["7290004131074", "7290000000009"])
        self.assertIn("7290004131074", result)
        self.assertNotIn("7290000000009", result)

    def test_product_without_a_branch_price_is_skipped(self):
        # Listed by the chain but not priced at this branch: not for sale
        # here, and inventing a price would be worse than omitting it.
        payload = {"products": [{"localBarcode": "7290004131074", "branch": {}}]}
        with self._with_response(payload):
            self.assertEqual(self.client.prices_by_barcode(["7290004131074"]), {})

    def test_api_error_is_raised_not_returned_as_empty(self):
        # An empty dict would read as "nothing is cheaper here", which is a
        # silently wrong answer rather than a visible failure.
        with self._with_response({"error": "Forbidden"}):
            with self.assertRaises(RuntimeError):
                self.client.prices_by_barcode(["7290004131074"])

    def test_duplicate_barcodes_are_requested_once(self):
        payload = {"products": [_product("7290004131074", "חלב", 7.35)]}
        with self._with_response(payload) as get:
            self.client.prices_by_barcode(["7290004131074", "7290004131074"])
            params = get.call_args.kwargs["params"]
        keys = [k for k in params if "localBarcode" in k]
        self.assertEqual(len(keys), 1)

    def test_large_basket_is_split_into_chunks(self):
        barcodes = [str(7290000000000 + n) for n in range(250)]
        with self._with_response({"products": []}) as get:
            self.client.prices_by_barcode(barcodes)
        self.assertEqual(get.call_count, 3)

    def test_filters_parameter_is_sent(self):
        # The endpoint answers "Forbidden" without it, which reads like a
        # permissions problem and is really a missing parameter.
        with self._with_response({"products": []}) as get:
            self.client.prices_by_barcode(["7290004131074"])
            params = get.call_args.kwargs["params"]
        self.assertEqual(
            params["filters[must][term][localBarcode][0]"], "7290004131074"
        )


class ProxyRequirementTest(unittest.TestCase):
    def test_refuses_to_start_without_an_israeli_exit(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError):
                selfpoint.SelfPointPrices("victory")


if __name__ == "__main__":
    unittest.main()
