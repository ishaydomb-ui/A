"""A repeat failure should change the approach, not just raise a counter.

Every strategy here was derived from the eight real failures on record
(2026-09-16), not invented in advance. The headline finding is that the
bot's own diagnosis was wrong on most of them: `טבעפרוסט תרד 800 גרם` was
filed as "probably out of stock" on 09-10, and the household had it
delivered on 09-12 — the chain's feed carries `טבעפרוסט תרד` at ₪11.90
and the search term simply carried a size the catalogue keeps elsewhere.
"""
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from grocery_bot import failstrategy
from grocery_bot.storage import Storage


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def _price(self, store, name, price=10.0):
        with closing(self.storage._connect()) as conn:  # noqa: SLF001
            conn.execute(
                "INSERT INTO store_prices (store, barcode, name, price, observed_at, source)"
                " VALUES (?, ?, ?, ?, '2026-09-16', 'test')",
                (store, f"b{abs(hash(name+store))%10**9}", name, price),
            )
            conn.commit()

    def test_a_product_the_chain_lists_is_a_retry_not_a_shortage(self):
        # The disproof case. "no add control" is a guess from a missing
        # button; the chain's own feed settles it.
        self._price("tivtaam", "פלפל צהוב", 12.9)
        s = failstrategy.decide(self.storage, "פלפל צהוב", "tivtaam")
        self.assertEqual(s.action, "retry")
        self.assertIn("12.9", s.reason)

    def test_an_over_specified_term_is_shortened(self):
        # The real one: the feed calls it "טבעפרוסט תרד", the list asked
        # for "טבעפרוסט תרד 800 גרם".
        self._price("tivtaam", "טבעפרוסט תרד", 11.9)
        s = failstrategy.decide(self.storage, "טבעפרוסט תרד 800 גרם", "tivtaam")
        self.assertEqual(s.action, "shorten")
        self.assertEqual(s.suggested_term, "טבעפרוסט תרד")

    def test_absent_here_but_stocked_elsewhere_switches_chain(self):
        for i in range(6):
            self._price("tivtaam", f"טונה בהירה בשמן {i}")
        s = failstrategy.decide(self.storage, "טונה בהירה בשמן 3*80 גרם", "shufersal")
        self.assertEqual(s.action, "switch_chain")
        self.assertEqual(s.suggested_store, "tivtaam")

    def test_a_single_stray_match_elsewhere_is_not_a_chain_to_switch_to(self):
        self._price("tivtaam", "טונה בהירה בשמן")
        s = failstrategy.decide(self.storage, "טונה בהירה בשמן 3*80 גרם", "shufersal")
        self.assertEqual(s.action, "unavailable")

    def test_nothing_anywhere_is_the_only_giving_up(self):
        s = failstrategy.decide(self.storage, "מוצר שלא קיים בשום מקום", "tivtaam")
        self.assertEqual(s.action, "unavailable")

    def test_the_suggested_chain_is_named_not_keyed(self):
        for i in range(6):
            self._price("tivtaam", f"טונה בהירה בשמן {i}")
        s = failstrategy.decide(self.storage, "טונה בהירה בשמן 3*80", "shufersal")
        self.assertIn("טיב טעם", s.describe())
        self.assertNotIn("tivtaam", s.describe())


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def _fail(self, store, name, detail=""):
        from grocery_bot.models import CartAddResult

        self.storage.record_cart_failures([
            CartAddResult(item_name=name, store=store, status="not_found",
                          detail=detail)
        ])

    def _price(self, store, name, price=10.0):
        with closing(self.storage._connect()) as conn:  # noqa: SLF001
            conn.execute(
                "INSERT INTO store_prices (store, barcode, name, price, observed_at, source)"
                " VALUES (?, ?, ?, ?, '2026-09-16', 'test')",
                (store, f"b{abs(hash(name+store))%10**9}", name, price),
            )
            conn.commit()

    def test_no_failures_produces_no_message(self):
        self.assertEqual(failstrategy.format_review([]), "")

    def test_the_stock_claim_is_only_counted_where_it_was_actually_made(self):
        # Over-claiming here would be the same error the module exists to
        # catch: one item blamed on stock, one not.
        self._price("tivtaam", "פלפל צהוב", 12.9)
        self._price("tivtaam", "עגבניה", 5.0)
        self._fail("tivtaam", "פלפל צהוב", "no add control — probably out of stock")
        self._fail("tivtaam", "עגבניה", "the click did not change the cart")
        text = failstrategy.format_review(failstrategy.review(self.storage, days=90))
        self.assertIn("1 מהם סומנו", text)

    def test_an_item_never_blamed_on_stock_produces_no_claim_line(self):
        self._price("tivtaam", "עגבניה", 5.0)
        self._fail("tivtaam", "עגבניה", "the click did not change the cart")
        text = failstrategy.format_review(failstrategy.review(self.storage, days=90))
        self.assertNotIn("סומנו", text)

    def test_every_failure_gets_a_strategy(self):
        self._fail("tivtaam", "משהו", "")
        self._fail("shufersal", "משהו אחר", "")
        self.assertEqual(len(failstrategy.review(self.storage, days=90)), 2)

    def test_the_evidence_records_how_often_it_failed(self):
        self._fail("tivtaam", "משהו", "")
        strategy = failstrategy.review(self.storage, days=90)[0]
        self.assertTrue(any("נכשל" in e for e in strategy.evidence))



class ConnectivityIsNotAProductFactTests(unittest.TestCase):
    """A dropped connection must not be remembered against an item.

    On 2026-09-17 the exit node failed mid-run and 14 items landed in
    `cart_failures` with ERR_SOCKS_CONNECTION_FAILED. In that table they
    read exactly like fourteen products the shop does not stock, and
    `failstrategy` would then propose shortening search terms that were
    never the problem. Raised by Miri from the same journal.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def _report(self, *results):
        from grocery_bot.models import OrderCycleReport

        report = OrderCycleReport(store="tivtaam")
        for r in results:
            report.record(r)
        return report

    def _result(self, name, status, detail):
        from grocery_bot.models import CartAddResult

        return CartAddResult(item_name=name, store="tivtaam", status=status,
                             detail=detail)

    def test_a_socks_failure_is_not_recorded_against_the_item(self):
        from grocery_bot.orchestrator import _remember_failures

        _remember_failures(self.storage, self._report(
            self._result("חלב", "error", "Page.goto: net::ERR_SOCKS_CONNECTION_FAILED"),
        ))
        self.assertEqual(self.storage.repeat_failures(days=90, min_runs=1), [])

    def test_an_expired_session_is_not_recorded_against_the_item(self):
        from grocery_bot.orchestrator import _remember_failures

        _remember_failures(self.storage, self._report(
            self._result("חלב", "error", "Session expired and could not be renewed"),
        ))
        self.assertEqual(self.storage.repeat_failures(days=90, min_runs=1), [])

    def test_a_genuine_product_failure_is_still_recorded(self):
        from grocery_bot.orchestrator import _remember_failures

        _remember_failures(self.storage, self._report(
            self._result("נקטרינה", "not_found", "no add control on the row"),
        ))
        rows = self.storage.repeat_failures(days=90, min_runs=1)
        self.assertEqual([r["item_name"] for r in rows], ["נקטרינה"])

    def test_a_mixed_run_records_only_the_product_half(self):
        from grocery_bot.orchestrator import _remember_failures

        _remember_failures(self.storage, self._report(
            self._result("חלב", "error", "net::ERR_SOCKS_CONNECTION_FAILED"),
            self._result("נקטרינה", "not_found", "no add control on the row"),
        ))
        rows = self.storage.repeat_failures(days=90, min_runs=1)
        self.assertEqual([r["item_name"] for r in rows], ["נקטרינה"])

if __name__ == "__main__":
    unittest.main()
