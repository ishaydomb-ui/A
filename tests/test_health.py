"""state/health.json writer (Basics in Order §1.1, 2026-09-26)."""
import json
import tempfile
import unittest
from pathlib import Path

from grocery_bot import health


class HealthFileTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "state" / "health.json"

    def test_jobs_merge_rather_than_overwrite(self):
        health.update("a", "ok", path=self.path, include_derived=False)
        health.update("b", "failed", "boom", path=self.path, include_derived=False)
        data = json.loads(self.path.read_text())
        self.assertEqual(data["schema"], "health/v1")
        self.assertEqual(data["agent"], "gordon")
        self.assertEqual(set(data["jobs"]), {"a", "b"})
        self.assertEqual(data["jobs"]["b"]["detail"], "boom")
        self.assertIn("+", data["jobs"]["a"]["last_run"][-6:])

    def test_not_ok_always_has_detail(self):
        health.update("a", "failed", path=self.path, include_derived=False)
        self.assertTrue(json.loads(self.path.read_text())["jobs"]["a"]["detail"])

    def test_unknown_status_is_refused(self):
        with self.assertRaises(ValueError):
            health.update("a", "green", path=self.path, include_derived=False)

    def test_expiries_name_both_required_sessions(self):
        _, expiries = health.derived(db_path=Path(self._tmp.name) / "none.sqlite3",
                                     sessions=Path(self._tmp.name))
        self.assertEqual(set(expiries), {"tivtaam-session", "behatsdaa"})
        self.assertIn("expired", expiries["behatsdaa"]["note"])



class ExpectedEveryTest(unittest.TestCase):
    def test_event_sources_and_jobs_carry_their_cadence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "health.json"
            health.update("grocery-prices", "ok", path=path, include_derived=False,
                          sources={"shufersal-orders": {"last_data_at": None, "status": "ok"}})
            data = json.loads(path.read_text())
        self.assertEqual(data["jobs"]["grocery-prices"]["expected_every_h"], 12)
        self.assertEqual(data["sources"]["shufersal-orders"]["expected_every_h"], 336)


class BrowserProbeTest(unittest.TestCase):
    def test_unconfigured_is_silent(self):
        from unittest import mock
        with mock.patch.dict("os.environ", {"GORDON_BROWSER_CDP_URL": ""}):
            self.assertIsNone(health.browser_probe())

    def test_a_closed_port_is_failed_with_an_owner_action(self):
        from unittest import mock
        with mock.patch.dict("os.environ", {"GORDON_BROWSER_CDP_URL": "http://127.0.0.1:9"}):
            name, line = health.browser_probe(timeout=0.5)
        self.assertEqual((name, line["status"]), ("gordonchrome", "failed"))
        self.assertTrue(line["detail"] and line["owner_action"])


if __name__ == "__main__":
    unittest.main()
