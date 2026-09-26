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


if __name__ == "__main__":
    unittest.main()
