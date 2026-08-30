import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent


def _run(args, db_path):
    """Run the CLI with ONLY the database path in the environment.

    `env -i`-style on purpose: the household's other bot calls this to add
    groceries, and it must not need this project's Telegram token to do
    so. Inheriting the developer's shell would hide exactly that mistake.
    """
    return subprocess.run(
        [sys.executable, "-m", "grocery_bot.cli", *args],
        cwd=PROJECT,
        env={
            "GROCERY_BOT_DB_PATH": db_path,
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
        },
        capture_output=True,
        text=True,
    )


class IntegrationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = str(Path(self._tmpdir.name) / "t.sqlite3")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_add_item_needs_no_telegram_token(self) -> None:
        result = _run(["add-item", "חלב", "--by", "לירן"], self.db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("added", result.stdout)

    def test_list_items_needs_no_telegram_token(self) -> None:
        _run(["add-item", "חלב", "--by", "לירן"], self.db)
        result = _run(["list-items"], self.db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("חלב", result.stdout)

    def test_a_repeat_is_reported_as_already_present(self) -> None:
        _run(["add-item", "חלב", "--by", "לירן"], self.db)
        result = _run(["add-item", "חלב", "--by", "לירן"], self.db)
        self.assertEqual(result.returncode, 0)
        self.assertIn("already", result.stdout)

    def test_the_flag_value_does_not_leak_into_the_product_name(self) -> None:
        _run(["add-item", "חלב", "--by", "לירן"], self.db)
        listing = _run(["list-items"], self.db).stdout
        self.assertIn("חלב", listing)
        self.assertNotIn("חלב לירן", listing)

    def test_the_requester_is_shown_when_reading_back(self) -> None:
        _run(["add-item", "חלב", "--by", "לירן"], self.db)
        self.assertIn("לירן", _run(["list-items"], self.db).stdout)

    def test_missing_text_is_a_usage_error_not_a_crash(self) -> None:
        result = _run(["add-item", "--by", "לירן"], self.db)
        self.assertEqual(result.returncode, 2)

    def test_a_telegram_command_still_reports_the_missing_token(self) -> None:
        """The token is still required where it is genuinely needed."""
        result = _run(["deals"], self.db)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TELEGRAM_BOT_TOKEN", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()


class RemoveItemContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = str(Path(self._tmpdir.name) / "t.sqlite3")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_removes_a_pending_item_by_fuzzy_name(self) -> None:
        _run(["add-item", "חלב 3%", "--by", "לירן"], self.db)
        result = _run(["remove-item", "חלב"], self.db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("removed", result.stdout)
        self.assertNotIn("חלב", _run(["list-items"], self.db).stdout)

    def test_a_missing_item_is_a_distinct_exit_code(self) -> None:
        """Not-found must be tellable apart from a usage error."""
        result = _run(["remove-item", "לא קיים"], self.db)
        self.assertEqual(result.returncode, 1)

    def test_missing_text_is_a_usage_error(self) -> None:
        result = _run(["remove-item"], self.db)
        self.assertEqual(result.returncode, 2)

    def test_needs_no_telegram_token(self) -> None:
        _run(["add-item", "חלב", "--by", "לירן"], self.db)
        result = _run(["remove-item", "חלב"], self.db)
        self.assertEqual(result.returncode, 0, result.stderr)
