"""Infrastructure failure must not become product failure. Phase 4.

The run this prevents: 2026-09-17, the route dropped at item 7 and the
loop issued fourteen more page loads, each timing out alone, each
recorded as a product the shop does not stock.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from grocery_bot import breaker
from grocery_bot.models import CartAddResult, PlanTerm
from grocery_bot.orchestrator import add_terms_to_cart
from grocery_bot.storage import Storage


def _r(status, detail="", kind=""):
    return CartAddResult(item_name="x", store="tivtaam", status=status, detail=detail,
                         failure_kind=kind)


class ClassifyTests(unittest.TestCase):
    def test_socks_failure_is_infrastructure(self):
        self.assertEqual(breaker.classify(_r("error", "Page.goto: net::ERR_SOCKS_CONNECTION_FAILED")),
                         "infrastructure")

    def test_httpx_proxy_error_is_infrastructure(self):
        self.assertEqual(breaker.classify(_r("error", "ProxyError: could not connect")), "infrastructure")

    def test_a_bare_timeout_is_ambiguous_not_infrastructure(self):
        # Demonstrated live: same text from a dead route and from a slow site.
        self.assertEqual(breaker.classify(_r("error", "Page.goto: Timeout 30000ms exceeded.")),
                         "ambiguous")

    def test_session_expiry_is_its_own_class(self):
        self.assertEqual(breaker.classify(_r("error", "Session expired and could not be renewed")),
                         "session")

    def test_out_of_stock_is_product(self):
        self.assertEqual(breaker.classify(_r("not_found", "no add control on the row")), "product")

    def test_the_adapters_own_kind_wins(self):
        self.assertEqual(breaker.classify(_r("error", "whatever", kind="ambiguous")), "ambiguous")

    def test_success_is_none(self):
        self.assertEqual(breaker.classify(_r("added")), "none")


class ObserveTests(unittest.TestCase):
    def setUp(self):
        self.brk = breaker.Breaker("tivtaam", proxy="socks5://127.0.0.1:1055", run_id=1)

    def test_one_infrastructure_failure_trips_immediately(self):
        self.assertEqual(self.brk.observe(_r("error", "net::ERR_SOCKS_CONNECTION_FAILED")), "recover")

    def test_product_failures_never_trip(self):
        for _ in range(20):
            self.assertEqual(self.brk.observe(_r("not_found", "no add control")), "continue")

    def test_one_timeout_does_nothing(self):
        self.assertEqual(self.brk.observe(_r("error", "Timeout 30000ms exceeded")), "continue")

    def test_two_timeouts_with_a_dead_route_recover(self):
        with mock.patch.object(breaker, "probe_route", return_value=False):
            self.brk.observe(_r("error", "Timeout 30000ms exceeded"))
            self.assertEqual(self.brk.observe(_r("error", "Timeout 30000ms exceeded")), "recover")

    def test_two_timeouts_with_a_healthy_route_back_off_and_continue(self):
        # The 2026-09-17 case: curl reached the site while Playwright timed out.
        with mock.patch.object(breaker, "probe_route", return_value=True), \
             mock.patch.object(breaker, "probe_site", return_value=True), \
             mock.patch.object(breaker.time, "sleep") as slept:
            self.brk.observe(_r("error", "Timeout 30000ms exceeded"))
            self.assertEqual(self.brk.observe(_r("error", "Timeout 30000ms exceeded")), "continue")
            slept.assert_called_once()

    def test_a_success_resets_the_ambiguous_counter(self):
        self.brk.observe(_r("error", "Timeout 30000ms exceeded"))
        self.brk.observe(_r("added"))
        with mock.patch.object(breaker, "probe_route") as probe:
            self.brk.observe(_r("error", "Timeout 30000ms exceeded"))
            probe.assert_not_called()

    def test_recovery_budget_exhausted_stops_the_store(self):
        self.brk.recoveries = breaker.MAX_RECOVERIES
        self.assertEqual(self.brk.observe(_r("error", "net::ERR_SOCKS_CONNECTION_FAILED")), "stop")
        self.assertTrue(self.brk.halted)

    def test_disabled_breaker_always_continues(self):
        with mock.patch.dict("os.environ", {"GORDON_BREAKER": "off"}):
            brk = breaker.Breaker("tivtaam", "socks5://x", 1)
            self.assertEqual(brk.observe(_r("error", "net::ERR_SOCKS_CONNECTION_FAILED")), "continue")


class RecoveryTests(unittest.TestCase):
    class _Adapter:
        def __init__(self, session_ok=True):
            self.session_ok = session_ok
            self.session_checks = 0

        def ensure_session(self):
            self.session_checks += 1
            return self.session_ok

    def _status(self, ok):
        return type("S", (), {"available": ok, "detail": ""})()

    def test_route_then_session_both_ok_means_resume(self):
        brk = breaker.Breaker("shufersal", "socks5://x", 1)
        with mock.patch("grocery_bot.exitnode.ensure_israeli_exit", return_value=self._status(True)):
            ad = self._Adapter(True)
            self.assertTrue(brk.recover(ad))
            self.assertEqual(ad.session_checks, 1)
            self.assertFalse(brk.halted)

    def test_route_dead_means_stop(self):
        brk = breaker.Breaker("shufersal", "socks5://x", 1)
        with mock.patch("grocery_bot.exitnode.ensure_israeli_exit", return_value=self._status(False)):
            self.assertFalse(brk.recover(self._Adapter(True)))
            self.assertTrue(brk.halted)

    def test_route_ok_but_session_dead_means_stop(self):
        brk = breaker.Breaker("shufersal", "socks5://x", 1)
        with mock.patch("grocery_bot.exitnode.ensure_israeli_exit", return_value=self._status(True)):
            self.assertFalse(brk.recover(self._Adapter(False)))


class _RouteAdapter:
    """Fails with a SOCKS error from `dead_from` onwards until `revive()`."""

    name = "tivtaam"

    def __init__(self, dead_from: int):
        self.n = 0
        self.dead_from = dead_from
        self.dead = False
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ensure_session(self):
        return True

    def revive(self):
        self.dead = False

    def search_and_add(self, term, quantity=1):
        self.n += 1
        self.calls.append(term)
        if self.n >= self.dead_from and not self.dead and self.n == self.dead_from:
            self.dead = True
        if self.dead:
            return CartAddResult(item_name=term, store=self.name, status="error",
                                 detail="Page.goto: net::ERR_SOCKS_CONNECTION_FAILED")
        return CartAddResult(item_name=term, store=self.name, status="added",
                             quantity=quantity, verification="verified", product_code=f"P{term}")

    def add_specific_product(self, name, quantity=1, **kw):
        return self.search_and_add(name, quantity)


class NoCascadeTests(unittest.TestCase):
    """The 6/20 → 14 run, replayed against the breaker."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))
        self.terms = [PlanTerm(f"item{i:02d}", 1, "adhoc", str(i)) for i in range(1, 21)]

    def test_route_dies_at_7_recovers_and_resumes_at_7_same_run(self):
        ad = _RouteAdapter(dead_from=7)

        def recover(self_brk, adapter):
            self_brk.recoveries += 1
            ad.revive()
            return True

        run_id = self.storage.start_cart_run("manual")
        with mock.patch.object(breaker.Breaker, "recover", recover):
            add_terms_to_cart(self.storage, {"tivtaam": lambda: ad}, self.terms,
                              run_id=run_id, proxy="socks5://x")
        counts = self.storage.run_counts(run_id)
        self.assertEqual(counts["requested"], 20)
        self.assertEqual(counts.get("verified"), 20)          # all landed, same run
        self.assertNotIn("failed_infra", counts)              # the trip was re-attempted
        self.assertNotIn("failed_product", counts)
        # item07 was attempted twice (trip, then resume); nothing after it was skipped.
        self.assertEqual(ad.calls.count("item07"), 2)
        self.assertEqual(ad.calls[-1], "item20")

    def test_route_dies_and_stays_dead_leaves_the_rest_pending(self):
        ad = _RouteAdapter(dead_from=7)

        def recover(self_brk, adapter):
            self_brk.recoveries += 1
            self_brk.halted = True
            return False

        run_id = self.storage.start_cart_run("manual")
        with mock.patch.object(breaker.Breaker, "recover", recover):
            add_terms_to_cart(self.storage, {"tivtaam": lambda: ad}, self.terms,
                              run_id=run_id, proxy="socks5://x")
        counts = self.storage.run_counts(run_id)
        self.assertEqual(counts.get("verified"), 6)
        self.assertEqual(counts.get("failed_infra"), 1)       # exactly the one that tripped
        self.assertEqual(counts.get("pending"), 13)           # never attempted, never "failed"
        self.assertNotIn("failed_product", counts)
        self.assertEqual(len(ad.calls), 7)                    # no cascade of 14

    def test_infrastructure_failure_never_enters_product_failure_history(self):
        ad = _RouteAdapter(dead_from=3)
        with mock.patch.object(breaker.Breaker, "recover", lambda s, a: False):
            add_terms_to_cart(self.storage, {"tivtaam": lambda: ad}, self.terms[:5],
                              proxy="socks5://x")
        self.assertEqual(self.storage.repeat_failures(days=30, min_runs=1), [])

    def test_with_the_breaker_off_the_old_cascade_returns(self):
        # Rollback flag: documents what "off" buys, and that it is worse.
        ad = _RouteAdapter(dead_from=7)
        with mock.patch.dict("os.environ", {"GORDON_BREAKER": "off"}):
            run_id = self.storage.start_cart_run("manual")
            add_terms_to_cart(self.storage, {"tivtaam": lambda: ad}, self.terms,
                              run_id=run_id, proxy="socks5://x")
        self.assertEqual(self.storage.run_counts(run_id).get("failed_infra"), 14)


if __name__ == "__main__":
    unittest.main()
