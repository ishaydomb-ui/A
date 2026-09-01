"""Where a price came from, and where an item would go.

Both questions were asked by the household after seeing a deals message
with no chain on it: "when you send deals I need to know where they're
from", and "when I say add it to the cart, how do you know whether to add
the Shufersal one or the Tiv Taam one".

The second is the dangerous one. There is exactly one chain whose cart
this project can fill, so "add it" always means Shufersal — and a deal
spotted at Osher Ad would be filled at Shufersal's price without anyone
noticing the substitution.
"""
import unittest

from grocery_bot import chains, hotdeals, radar
from grocery_bot.models import CartAddResult
from grocery_bot.orchestrator import OrderCycleReport, format_report_summary


def _deal(chain, name="מוצר", price=10.0, reference=20.0):
    return hotdeals.HotDeal(
        barcode="1", name=name, chain=chain, price=price, reference_price=reference
    )


class CartCapabilityTest(unittest.TestCase):
    def test_only_shufersal_can_be_filled_today(self):
        self.assertTrue(chains.can_fill_cart("shufersal"))
        for chain in ("osherad", "ramilevy", "politzer", "keshet", "freshmarket"):
            self.assertFalse(chains.can_fill_cart(chain), chain)

    def test_chains_we_read_prices_from_are_not_all_fillable(self):
        # Price data and cart access are different capabilities; conflating
        # them is what turns a real deal into a promise that fails.
        readable = set(chains.CHAIN_NAMES)
        self.assertTrue(chains.CART_CAPABLE < readable)


class DealProvenanceTest(unittest.TestCase):
    def test_every_deal_line_names_its_chain(self):
        text = hotdeals.format_deals([_deal("osherad")], [])
        self.assertIn("אושר עד", text)

    def test_a_chain_with_no_cart_is_marked(self):
        text = hotdeals.format_deals([_deal("osherad")], [])
        self.assertIn("🔗", text)
        self.assertIn("להזמין ידנית", text)

    def test_a_fillable_chain_is_not_marked(self):
        text = hotdeals.format_deals([_deal("shufersal")], [])
        self.assertNotIn("🔗", text)

    def test_the_stockup_list_names_its_source(self):
        # It is built entirely from the Shufersal feed but read as
        # chain-neutral beside a cross-chain list that names one per line.
        header = radar.format_stockup_deals(
            [
                radar.StockUpDeal(
                    bought_name="x", catalog_name="x", shelf_price=20.0,
                    deal_price=10.0, description="d", pantryable=True,
                )
            ]
        )
        self.assertIn("שופרסל", header.splitlines()[0])


class CartDestinationTest(unittest.TestCase):
    def test_the_reply_says_which_chain_it_went_to(self):
        report = OrderCycleReport(store="shufersal")
        report.record(CartAddResult(item_name="חלב", store="shufersal", status="added"))
        text = format_report_summary({"shufersal": report})
        self.assertIn("שופרסל", text)

    def test_the_internal_key_is_not_shown_to_the_household(self):
        report = OrderCycleReport(store="shufersal")
        report.record(CartAddResult(item_name="חלב", store="shufersal", status="added"))
        self.assertNotIn("*shufersal*", format_report_summary({"shufersal": report}))


if __name__ == "__main__":
    unittest.main()


class StockupLinksToOtherChainsTest(unittest.TestCase):
    """/stockup is Shufersal-only by construction, so it says where to look.

    Asked for after a /stockup message that listed only Shufersal deals
    with no way to reach the cross-chain ones.
    """

    def _deal(self):
        return radar.StockUpDeal(
            bought_name="x", catalog_name="x", shelf_price=20.0,
            deal_price=10.0, description="d", pantryable=True,
        )

    def test_the_message_carries_no_deep_link(self):
        # A t.me deep link tapped from inside the bot's own chat sends a
        # bare /start with the payload stripped, so it looks broken and
        # does nothing. The cross-chain list is a button on the message
        # instead.
        text = radar.format_stockup_deals([self._deal()], "TestBot")
        self.assertNotIn("t.me", text)
        self.assertNotIn("start=chaindeals", text)

    def test_the_button_is_attached_by_the_bot_not_the_formatter(self):
        import pathlib as _p

        source = _p.Path("grocery_bot/telegram_bot.py").read_text()
        self.assertIn('callback_data="chaindeals"', source)
        self.assertIn("on_chain_deals_button", source)

    def test_the_two_lists_stay_separate(self):
        # /stockup answers "what is unusually cheap where I shop";
        # /chaindeals answers "who else is cheaper". Merging them makes
        # one list that answers neither cleanly.
        stockup = radar.format_stockup_deals([self._deal()], "TestBot")
        self.assertIn("שופרסל", stockup.splitlines()[0])
