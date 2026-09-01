import tempfile
import unittest
from datetime import date
from pathlib import Path

from grocery_bot import hotdeals, nudge
from grocery_bot.storage import Storage

TODAY = date(2026, 9, 10)


class TimingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(str(Path(self.tmp.name) / "t.sqlite3"))

    def _ordered_on(self, day: str):
        self.storage.record_last_purchase("shufersal", [("P_1", day)])

    def test_nothing_to_say_without_any_history(self):
        decision = nudge.decide(self.storage, today=TODAY)
        self.assertFalse(decision.due)
        self.assertEqual(decision.text, "")

    def test_silent_before_six_days(self):
        self._ordered_on("2026-09-07")
        self.assertFalse(nudge.decide(self.storage, today=TODAY).due)

    def test_speaks_on_the_sixth_day(self):
        self._ordered_on("2026-09-04")
        decision = nudge.decide(self.storage, today=TODAY)
        self.assertTrue(decision.due)
        self.assertEqual(decision.days_since_order, 6)

    def test_quiet_period_prevents_daily_nagging(self):
        self._ordered_on("2026-08-20")
        decision = nudge.decide(
            self.storage, today=TODAY, last_nudged=date(2026, 9, 9)
        )
        self.assertFalse(decision.due)

    def test_nudges_again_once_the_quiet_period_passes(self):
        self._ordered_on("2026-08-20")
        decision = nudge.decide(
            self.storage, today=TODAY, last_nudged=date(2026, 9, 1)
        )
        self.assertTrue(decision.due)

    def test_the_later_of_two_sources_wins(self):
        # order_log and the per-product dates are written by different
        # paths, so either can be stale. Taking the earlier one nagged
        # about a shop already done.
        with self.storage._connect() as conn:  # noqa: SLF001 - test fixture
            conn.execute(
                "INSERT INTO order_log (order_code, store, placed_at, item_count) "
                "VALUES ('X', 'shufersal', '2026-08-24T09:00:00', 30)"
            )
            conn.commit()
        self._ordered_on("2026-09-09")
        self.assertEqual(nudge.last_order_date(self.storage), date(2026, 9, 9))


class MessageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(str(Path(self.tmp.name) / "t.sqlite3"))
        self.storage.record_last_purchase("shufersal", [("P_1", "2026-09-01")])

    def test_asks_for_a_free_text_reply(self):
        text = nudge.compose(self.storage, 7, TODAY)
        self.assertIn("מה להוסיף", text)
        self.assertIn("9", nudge.compose(self.storage, 9, TODAY))

    def test_a_quiet_week_is_still_a_valid_message(self):
        # No pantry items due and no deals: the reminder alone is fine,
        # and must not produce empty section headers.
        text = nudge.compose(self.storage, 7, TODAY)
        self.assertNotIn("*כנראה נגמר במזווה:*\n\n", text)


class HotDealTest(unittest.TestCase):
    def _deal(self, name, price, reference, chain="ramilevy", often=False):
        return hotdeals.HotDeal(
            barcode="1", name=name, chain=chain, price=price,
            reference_price=reference, bought_often=often,
        )

    def test_expensive_keepers_qualify_on_shekels(self):
        # 23% off nappies is worth more than half off a tin of corn.
        deal = self._deal("האגיס אקסטרה קר מידה 4", 46.90, 60.90)
        self.assertTrue(deal.stockable)
        self.assertTrue(deal.worth_reporting)

    def test_a_small_saving_on_a_keeper_is_not_enough(self):
        self.assertFalse(self._deal("נייר טואלט", 29.0, 32.0).worth_reporting)

    def test_perishables_must_clear_on_percentage(self):
        self.assertFalse(self._deal("מלפפון", 9.0, 10.0, often=True).worth_reporting)
        self.assertTrue(self._deal("מלפפון", 7.0, 10.0, often=True).worth_reporting)

    def test_a_price_rise_is_never_a_deal(self):
        self.assertFalse(self._deal("חלב", 12.0, 10.0, often=True).worth_reporting)

    def test_one_family_cannot_fill_the_message(self):
        # The first run returned six lines of nappies out of eight: all
        # true, all the same decision, and everything else pushed off.
        deals = [
            self._deal(f"האגיס מידה {n}", 46.90, 60.90) for n in range(6)
        ] + [self._deal("נייר טואלט לח", 20.0, 40.0)]
        kept = hotdeals._dedupe(
            [hotdeals.HotDeal(str(i), d.name, d.chain, d.price, d.reference_price)
             for i, d in enumerate(deals)]
        )
        nappies = [d for d in kept if "האגיס" in d.name]
        self.assertLessEqual(len(nappies), hotdeals.MAX_PER_FAMILY)
        self.assertTrue(any("נייר טואלט" in d.name for d in kept))

    def test_stockable_detection_covers_the_baby_categories(self):
        for name in ("חיתולי פמפרס", "מגבונים לחים", "סימילאק גולד", "מוצץ אבנט"):
            self.assertTrue(hotdeals.is_stockable(name), name)
        self.assertFalse(hotdeals.is_stockable("עגבניות שרי"))

    def test_unfamiliar_chains_are_marked_in_the_message(self):
        text = hotdeals.format_deals([self._deal("האגיס מידה 4", 46.90, 60.90)])
        self.assertIn("⚡", text)

    def test_no_deals_produces_no_section(self):
        self.assertEqual(hotdeals.format_deals([]), "")


class TwoBucketTest(unittest.TestCase):
    """Deals the household buys, and deals that are simply remarkable."""

    def _deal(self, name, price, reference, often=False, chain="ramilevy"):
        return hotdeals.HotDeal(
            barcode=name, name=name, chain=chain, price=price,
            reference_price=reference, bought_often=often,
        )

    def test_a_novel_product_qualifies_only_when_remarkable(self):
        # The household is happy to try something new at a good price,
        # but does not want a catalogue.
        mild = self._deal("תותים", 8.0, 10.0)
        deep = self._deal("אפרול 1 ליטר", 69.90, 120.90)
        self.assertFalse(mild.exceptional)
        self.assertTrue(deep.exceptional)

    def test_a_familiar_product_qualifies_on_a_lower_bar(self):
        self.assertTrue(self._deal("מלפפון", 7.0, 10.0, often=True).worth_reporting)

    def test_an_implausible_discount_is_a_feed_error_not_a_deal(self):
        # A 95%-off line is a misplaced decimal, and reporting it burns
        # the trust the rest of the list depends on.
        self.assertFalse(self._deal("משהו", 1.0, 100.0).plausible)
        self.assertFalse(self._deal("משהו", 1.0, 100.0).exceptional)

    def test_something_they_buy_never_appears_under_never_bought(self):
        # Yellow peppers are tier A for this household and landed under
        # "even if you have not bought it" purely because five nappy
        # deals outranked them. Exercised through the real split rather
        # than by asserting a property of one object.
        import tempfile as _tempfile

        tmp = _tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        storage = Storage(str(Path(tmp.name) / "t.sqlite3"))
        with storage._connect() as conn:  # noqa: SLF001 - test fixture
            conn.execute(
                "INSERT INTO catalog_products (item_code, name, price) VALUES (?,?,?)",
                ("PEP", "פלפל צהוב", 12.90),
            )
            conn.execute(
                "INSERT INTO stock_items (store, product_code, product_name, tier, share) "
                "VALUES ('shufersal', 'P_PEP', 'פלפל צהוב', 'A', 0.9)"
            )
            conn.commit()
        storage.record_store_prices(
            "ramilevy",
            [{"barcode": "PEP", "name": "פלפל צהוב", "price": 4.0,
              "observed_at": "2026-09-01"}],
        )
        relevant, exceptional = hotdeals.find(storage, chains=["ramilevy"])
        self.assertIn("פלפל צהוב", [d.name for d in relevant])
        self.assertNotIn("פלפל צהוב", [d.name for d in exceptional])

    def test_promotions_are_a_deal_source_not_just_other_chains(self):
        # A 2+1 at their own shop leaves the shelf price untouched, so
        # cross-chain comparison alone cannot see the commonest Israeli
        # deal there is.
        import inspect

        self.assertIn("_promotion_deals", inspect.getsource(hotdeals.scan))

    def test_message_separates_the_two_lists(self):
        text = hotdeals.format_deals(
            [self._deal("חיתולי האגיס", 45.0, 60.90)],
            [self._deal("אפרול", 69.90, 120.90)],
        )
        self.assertIn("מבצעים על מה שאתם קונים", text)
        self.assertIn("גם אם לא קניתם", text)


class CardReminderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(str(Path(self.tmp.name) / "t.sqlite3"))

    def test_asks_when_the_month_is_unconfirmed(self):
        from grocery_bot import cardreminder

        prompt = cardreminder.decide(self.storage, date(2026, 9, 1))
        self.assertTrue(prompt.should_ask)
        self.assertIn("700", prompt.text)

    def test_stops_asking_once_confirmed(self):
        from grocery_bot import cardreminder

        cardreminder.confirm(self.storage, today=date(2026, 9, 1))
        self.assertFalse(cardreminder.decide(self.storage, date(2026, 9, 20)).should_ask)

    def test_asks_again_next_month(self):
        from grocery_bot import cardreminder

        # The allowance does not roll over, so each month is its own
        # decision and its own ₪49.
        cardreminder.confirm(self.storage, today=date(2026, 9, 1))
        self.assertTrue(cardreminder.decide(self.storage, date(2026, 10, 1)).should_ask)

    def test_reads_a_yes(self):
        from grocery_bot import cardreminder

        for reply in ("הטענתי", "כן טענתי", "סידרתי את זה"):
            self.assertTrue(cardreminder.looks_confirmed(reply), reply)

    def test_a_negation_is_never_a_yes(self):
        from grocery_bot import cardreminder

        # "לא הטענתי" contains "הטענתי" and must not be read as done.
        for reply in ("לא הטענתי", "עוד לא", "לא הספקתי"):
            self.assertFalse(cardreminder.looks_confirmed(reply), reply)

    def test_empty_reply_is_not_a_confirmation(self):
        from grocery_bot import cardreminder

        self.assertFalse(cardreminder.looks_confirmed(""))


if __name__ == "__main__":
    unittest.main()


class CardPromptTimingTest(unittest.TestCase):
    """A monthly question riding on a shopping-cadence trigger.

    Flagged by the bot that delivers the nudge: the card allowance is
    monthly and the nudge fires six days after a shop, so a month of
    frequent shopping can pass with the question arriving late. The
    answer is not a second proactive message — it is to also ask inside
    a hand-off the household started themselves.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(str(Path(self.tmp.name) / "t.sqlite3"))

    def test_the_two_surfaces_share_one_monthly_state(self):
        from grocery_bot import cardreminder

        # Confirming anywhere silences it everywhere for that month, so
        # the household is never asked twice about one allowance.
        self.assertTrue(cardreminder.decide(self.storage, date(2026, 9, 1)).should_ask)
        cardreminder.confirm(self.storage, today=date(2026, 9, 1))
        self.assertFalse(cardreminder.decide(self.storage, date(2026, 9, 20)).should_ask)
        self.storage.record_last_purchase("shufersal", [("P_1", "2026-09-01")])
        text = nudge.compose(self.storage, 7, date(2026, 9, 20))
        self.assertNotIn("הטענת את הכרטיס", text)

    def test_an_unconfirmed_month_still_asks_in_the_nudge(self):
        self.storage.record_last_purchase("shufersal", [("P_1", "2026-09-01")])
        text = nudge.compose(self.storage, 7, date(2026, 9, 20))
        self.assertIn("הטענת את הכרטיס", text)
