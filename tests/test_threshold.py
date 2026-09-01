import datetime
import tempfile
import unittest
from pathlib import Path

from grocery_bot import threshold
from grocery_bot.multibuy import MultiBuyOffer
from grocery_bot.storage import Storage

# A realistic promotion window. A date years out is a gift coupon rather
# than a price cut, and is excluded on purpose.
SOON = (datetime.date.today() + datetime.timedelta(days=30)).isoformat() + "T00:00:00"


def _offer(name, regular, total, qty):
    return MultiBuyOffer(
        item_code=name, name=name, regular_price=regular,
        promo_total=total, min_qty=qty, description="d",
    )


class ShortfallTest(unittest.TestCase):
    def test_the_real_september_gap(self):
        check = threshold.ThresholdCheck(basket_total=560.79, threshold=599.0)
        self.assertEqual(check.shortfall, 38.21)
        self.assertTrue(check.worth_chasing)
        self.assertFalse(check.qualifies)

    def test_the_real_august_gap(self):
        check = threshold.ThresholdCheck(basket_total=521.19, threshold=599.0)
        self.assertEqual(check.shortfall, 77.81)
        self.assertTrue(check.worth_chasing)

    def test_a_qualifying_basket_says_so(self):
        check = threshold.ThresholdCheck(basket_total=650.0, threshold=599.0)
        self.assertTrue(check.qualifies)
        self.assertEqual(check.shortfall, 0.0)
        self.assertIn("עובר את הסף", threshold.format_check(check))

    def test_a_distant_threshold_is_not_worth_chasing(self):
        # Chasing ₪400 means buying things nobody wanted.
        check = threshold.ThresholdCheck(basket_total=180.0, threshold=599.0)
        self.assertFalse(check.worth_chasing)
        self.assertIn("לא שווה", threshold.format_check(check))


class UpsellTest(unittest.TestCase):
    def _check(self, total=560.79):
        return threshold.ThresholdCheck(
            basket_total=total,
            threshold=599.0,
            offers=[
                _offer("טונה", 28.50, 48.0, 2),      # +19.50 today, saves 4.50
                _offer("שוקולד", 25.90, 40.0, 2),    # +14.10 today, saves 5.90
                _offer("קטשופ", 16.50, 22.0, 2),     # +5.50 today, saves 5.50
                _offer("כבר מוזל", 10.0, 8.0, 1),    # not an upsell at all
            ],
        )

    def test_only_genuine_upsells_are_offered(self):
        names = [o.name for o in self._check().upsells]
        self.assertNotIn("כבר מוזל", names)
        self.assertEqual(len(names), 3)

    def test_no_single_offer_closes_the_real_gap(self):
        self.assertEqual(self._check().closing_offers, [])

    def test_a_combination_that_does_close_it_is_offered(self):
        # "None of these is enough" is true and useless when two of them
        # together would do it, and both were worth taking anyway.
        combination = self._check().combination_to_close()
        self.assertTrue(combination)
        spend = sum(o.extra_outlay for o in combination)
        self.assertGreaterEqual(spend, 38.21)

    def test_the_combination_is_chosen_by_value_not_tightest_fit(self):
        combination = self._check().combination_to_close()
        self.assertEqual(combination[0].name, "שוקולד")

    def test_a_single_offer_that_closes_it_is_preferred(self):
        check = threshold.ThresholdCheck(
            basket_total=590.0, threshold=599.0,
            offers=[_offer("טונה", 28.50, 48.0, 2)],
        )
        self.assertTrue(check.closing_offers)
        self.assertIn("יעביר אתכם את הסף", threshold.format_check(check))

    def test_no_combination_when_the_threshold_is_out_of_reach(self):
        check = threshold.ThresholdCheck(
            basket_total=100.0, threshold=599.0,
            offers=[_offer("טונה", 28.50, 48.0, 2)],
        )
        self.assertEqual(check.combination_to_close(), [])


class ReadFromOrderTest(unittest.TestCase):
    def test_reads_the_live_threshold_from_a_real_payload(self):
        order = {
            "entries": [
                {
                    "promotionOrderEntries": [
                        {
                            "conditionType": "Amount",
                            "conditionValue": 599.0,
                            "conditionActualValue": 560.79,
                            "promotionMessage": "599שח ומעלה-מתנה",
                        }
                    ]
                }
            ]
        }
        value, actual, reward = threshold.threshold_from_order(order)
        self.assertEqual((value, actual), (599.0, 560.79))
        self.assertIn("מתנה", reward)

    def test_quantity_conditions_are_not_a_spend_threshold(self):
        order = {
            "entries": [
                {"promotionOrderEntries": [{"conditionType": "Quantity",
                                            "conditionValue": 2.0,
                                            "conditionActualValue": 1.0}]}
            ]
        }
        self.assertIsNone(threshold.threshold_from_order(order))

    def test_an_order_without_promotions_is_safe(self):
        self.assertIsNone(threshold.threshold_from_order({"entries": [{}]}))


class ToneTest(unittest.TestCase):
    def test_it_never_claims_to_have_added_anything(self):
        # Buying something unwanted to reach a threshold is not a saving,
        # and deciding that is not the bot's call.
        check = threshold.ThresholdCheck(
            basket_total=560.79, threshold=599.0,
            offers=[_offer("טונה", 28.50, 48.0, 2)],
        )
        text = threshold.format_check(check)
        self.assertIn("ההחלטה שלכם", text)
        self.assertNotIn("הוספתי", text)


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(str(Path(self.tmp.name) / "t.sqlite3"))
        with self.storage._connect() as conn:  # noqa: SLF001 - test fixture
            conn.execute(
                "INSERT INTO catalog_products (item_code, name, price) VALUES (?,?,?)",
                ("A", "טונה", 28.50),
            )
            conn.execute(
                "INSERT INTO catalog_promotions (promotion_id, description, item_code, "
                "discounted_price, min_qty, discount_rate, starts_at, ends_at) "
                "VALUES ('1', '2ב48 טונה', 'A', 48.0, 2.0, 0.0, '', ?)",
                (SOON,),
            )
            conn.commit()

    def test_check_finds_the_offer_for_a_basket(self):
        result = threshold.check(self.storage, 560.79, ["A"])
        self.assertEqual(len(result.upsells), 1)
        self.assertEqual(result.upsells[0].unit_price, 24.0)


if __name__ == "__main__":
    unittest.main()
