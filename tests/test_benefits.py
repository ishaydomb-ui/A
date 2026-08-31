import unittest

from grocery_bot import benefits


def _order(month_day, total, payments):
    return {
        "timePlaced": f"{month_day}T10:00:00.000Z",
        "totalAmount": total,
        "payments": [
            {"paymentMethodId": m, "amountCharged": a} for m, a in payments
        ],
    }


class CoinRateTest(unittest.TestCase):
    def test_published_three_percent(self):
        self.assertEqual(benefits.coins_earned_on(100), 3.0)

    def test_matches_a_real_basket(self):
        # The 2026-08-06 order: ₪478.39 billed.
        self.assertAlmostEqual(benefits.coins_earned_on(478.39), 14.35, places=2)


class RedemptionThresholdTest(unittest.TestCase):
    def test_below_threshold_cannot_be_spent(self):
        # The app showed a 3.2-coin balance; that is not a missed saving,
        # it simply cannot be redeemed yet.
        self.assertFalse(benefits.redeemable(3.2))
        self.assertEqual(benefits.coins_needed(3.2), 5.8)

    def test_at_threshold_unlocks(self):
        self.assertTrue(benefits.redeemable(9.0))
        self.assertEqual(benefits.coins_needed(9.0), 0.0)

    def test_spend_needed_to_unlock(self):
        # 5.8 coins short at 3% means roughly ₪193 of further spend.
        self.assertAlmostEqual(benefits.spend_to_unlock(3.2), 193.33, places=2)


class CardTest(unittest.TestCase):
    def test_full_allowance_saves_seven_percent(self):
        position = benefits.BenefitPosition("2026-06", spend=2032.07, card_used=700.0,
                                            coins_redeemed=50.79)
        self.assertEqual(position.card_saved, 49.0)
        self.assertEqual(position.card_remaining, 0.0)
        self.assertEqual(position.card_forgone, 0.0)

    def test_unused_allowance_is_reported_as_forgone(self):
        # Spent ₪2,000 but loaded nothing: the whole ₪700 was available
        # and would have been spent anyway.
        position = benefits.BenefitPosition("2026-05", spend=2000.0, card_used=0.0,
                                            coins_redeemed=0.0)
        self.assertEqual(position.card_remaining, 700.0)
        self.assertEqual(position.card_forgone, 49.0)

    def test_no_saving_claimed_for_groceries_not_bought(self):
        # A ₪200 month cannot save 7% on ₪700 of card. Claiming otherwise
        # would invent a saving out of an allowance with nothing to spend
        # it on.
        position = benefits.BenefitPosition("2026-02", spend=200.0, card_used=0.0,
                                            coins_redeemed=0.0)
        self.assertEqual(position.card_forgone, 14.0)

    def test_allowance_never_goes_negative(self):
        position = benefits.BenefitPosition("2026-04", spend=1500.0, card_used=900.0,
                                            coins_redeemed=0.0)
        self.assertEqual(position.card_remaining, 0.0)


class EffectiveCostTest(unittest.TestCase):
    def test_coins_alone_when_no_card_left(self):
        self.assertEqual(benefits.effective_cost(100.0, card_available=0.0), 97.0)

    def test_card_and_coins_stack(self):
        # ₪100 fully covered by card: 7% + 3% = ₪10 off.
        self.assertEqual(benefits.effective_cost(100.0, card_available=100.0), 90.0)

    def test_card_only_applies_to_the_covered_portion(self):
        # ₪1,000 basket with ₪700 of allowance: 7% of 700 plus 3% of 1000.
        self.assertEqual(benefits.effective_cost(1000.0, card_available=700.0), 921.0)
        self.assertAlmostEqual(
            benefits.effective_discount_rate(1000.0, 700.0), 0.079, places=3
        )

    def test_empty_basket_is_free_not_an_error(self):
        self.assertEqual(benefits.effective_cost(0.0, card_available=700.0), 0.0)
        self.assertEqual(benefits.effective_discount_rate(0.0), 0.0)


class PositionsFromOrdersTest(unittest.TestCase):
    def test_splits_card_from_coins_by_payment_method(self):
        orders = [
            _order("2026-06-05", 716.73, [(11, 30.13), (2, 686.60)]),
            _order("2026-06-16", 455.35, [(11, 20.66), (30, 355.02), (2, 79.67)]),
        ]
        positions = benefits.positions_from_orders(orders)
        june = positions["2026-06"]
        self.assertEqual(june.spend, 1172.08)
        self.assertEqual(june.card_used, 355.02)
        self.assertEqual(june.coins_redeemed, 50.79)

    def test_credit_card_payments_are_not_counted_as_a_benefit(self):
        positions = benefits.positions_from_orders(
            [_order("2026-07-29", 838.34, [(2, 838.34)])]
        )
        july = positions["2026-07"]
        self.assertEqual(july.card_used, 0.0)
        self.assertEqual(july.coins_redeemed, 0.0)
        # The full allowance was available against real spend.
        self.assertEqual(july.card_forgone, 49.0)

    def test_orders_without_a_date_are_skipped_not_guessed(self):
        self.assertEqual(benefits.positions_from_orders([{"totalAmount": 100}]), {})


if __name__ == "__main__":
    unittest.main()
