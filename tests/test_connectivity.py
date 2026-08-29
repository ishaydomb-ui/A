import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from grocery_bot.connectivity import check_israeli_exit
from grocery_bot.storage import Storage


def _curl_result(stdout: str, returncode: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class CheckIsraeliExitTests(unittest.TestCase):
    def test_no_proxy_configured_is_unavailable(self) -> None:
        status = check_israeli_exit("")
        self.assertFalse(status.available)

    def test_israeli_exit_is_available(self) -> None:
        with mock.patch(
            "subprocess.run",
            return_value=_curl_result('{"country": "IL", "org": "Hot-Net"}'),
        ):
            status = check_israeli_exit("socks5://localhost:1055")
        self.assertTrue(status.available)
        self.assertEqual(status.country, "IL")

    def test_non_israeli_exit_is_rejected(self) -> None:
        """A reachable but foreign exit is worse than none.

        The stores answer HTTP 200 with a geo-block placeholder, which
        parses as "every selector broke" rather than as a network problem.
        """
        with mock.patch(
            "subprocess.run", return_value=_curl_result('{"country": "FR", "org": "Contabo"}')
        ):
            status = check_israeli_exit("socks5://localhost:1055")
        self.assertFalse(status.available)
        self.assertEqual(status.country, "FR")

    def test_exit_node_down_is_unavailable_not_an_exception(self) -> None:
        with mock.patch("subprocess.run", return_value=_curl_result("", returncode=7)):
            status = check_israeli_exit("socks5://localhost:1055")
        self.assertFalse(status.available)

    def test_probe_timeout_is_unavailable_not_an_exception(self) -> None:
        with mock.patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="curl", timeout=12)
        ):
            status = check_israeli_exit("socks5://localhost:1055")
        self.assertFalse(status.available)

    def test_garbage_response_is_unavailable(self) -> None:
        with mock.patch("subprocess.run", return_value=_curl_result("<html>nope</html>")):
            status = check_israeli_exit("socks5://localhost:1055")
        self.assertFalse(status.available)


class DeferredCycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.storage = Storage(str(Path(self._tmpdir.name) / "test.sqlite3"))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_no_cycle_pending_initially(self) -> None:
        self.assertIsNone(self.storage.pending_deferred_cycle())

    def test_defer_and_read_back(self) -> None:
        cycle_id = self.storage.defer_cycle(chat_id=42, requested_by="ishay")
        pending = self.storage.pending_deferred_cycle()
        self.assertIsNotNone(pending)
        self.assertEqual(pending["id"], cycle_id)
        self.assertEqual(pending["chat_id"], 42)

    def test_repeated_requests_collapse_into_one(self) -> None:
        """Asking twice while the exit is down means "I want a cycle".

        Stacking them would run the cycle twice back to back and add
        every item to the real cart a second time.
        """
        first = self.storage.defer_cycle(chat_id=42, requested_by="ishay")
        second = self.storage.defer_cycle(chat_id=42, requested_by="liran")
        self.assertEqual(first, second)

    def test_done_cycle_stops_being_pending(self) -> None:
        cycle_id = self.storage.defer_cycle(chat_id=42, requested_by="ishay")
        self.storage.mark_deferred_cycle_done(cycle_id)
        self.assertIsNone(self.storage.pending_deferred_cycle())

    def test_new_cycle_can_be_queued_after_one_completes(self) -> None:
        first = self.storage.defer_cycle(chat_id=42, requested_by="ishay")
        self.storage.mark_deferred_cycle_done(first)
        second = self.storage.defer_cycle(chat_id=99, requested_by="liran")
        self.assertNotEqual(first, second)
        self.assertEqual(self.storage.pending_deferred_cycle()["chat_id"], 99)


if __name__ == "__main__":
    unittest.main()
