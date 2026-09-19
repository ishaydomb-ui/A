import unittest
from unittest import mock

from grocery_bot import bitwarden


class AvailableTests(unittest.TestCase):
    def test_false_when_env_vars_missing(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch("grocery_bot.bitwarden.os.path.exists", return_value=True):
            self.assertFalse(bitwarden.available())

    def test_false_when_bw_binary_missing(self) -> None:
        env = {"BW_CLIENTID": "a", "BW_CLIENTSECRET": "b", "BW_PASSWORD": "c"}
        with mock.patch.dict("os.environ", env, clear=True), \
             mock.patch("grocery_bot.bitwarden.os.path.exists", return_value=False):
            self.assertFalse(bitwarden.available())

    def test_true_when_binary_and_all_vars_present(self) -> None:
        env = {"BW_CLIENTID": "a", "BW_CLIENTSECRET": "b", "BW_PASSWORD": "c"}
        with mock.patch.dict("os.environ", env, clear=True), \
             mock.patch("grocery_bot.bitwarden.os.path.exists", return_value=True):
            self.assertTrue(bitwarden.available())


class PasswordForTests(unittest.TestCase):
    def setUp(self) -> None:
        bitwarden._SESSION.update(key=None, at=0.0, synced=0.0)

    def test_none_when_unavailable(self) -> None:
        with mock.patch("grocery_bot.bitwarden.available", return_value=False):
            self.assertIsNone(bitwarden.password_for("https://example.com"))

    def test_none_when_url_empty(self) -> None:
        with mock.patch("grocery_bot.bitwarden.available", return_value=True):
            self.assertIsNone(bitwarden.password_for(""))

    def test_returns_password_of_first_matching_item(self) -> None:
        items = [{"login": {"password": "s3cret", "username": "u"}}]
        with mock.patch("grocery_bot.bitwarden.available", return_value=True), \
             mock.patch("grocery_bot.bitwarden._items", return_value=items):
            self.assertEqual(bitwarden.password_for("https://example.com"), "s3cret")

    def test_none_when_no_items_match(self) -> None:
        with mock.patch("grocery_bot.bitwarden.available", return_value=True), \
             mock.patch("grocery_bot.bitwarden._items", return_value=[]):
            self.assertIsNone(bitwarden.password_for("https://example.com"))

    def test_none_on_lookup_exception_never_raises(self) -> None:
        with mock.patch("grocery_bot.bitwarden.available", return_value=True), \
             mock.patch("grocery_bot.bitwarden._items", side_effect=RuntimeError("bw unlock failed")):
            self.assertIsNone(bitwarden.password_for("https://example.com"))


class UsernameForTests(unittest.TestCase):
    def test_returns_username_of_first_matching_item(self) -> None:
        items = [{"login": {"password": "s3cret", "username": "ishay@example.com"}}]
        with mock.patch("grocery_bot.bitwarden.available", return_value=True), \
             mock.patch("grocery_bot.bitwarden._items", return_value=items):
            self.assertEqual(bitwarden.username_for("https://example.com"), "ishay@example.com")


class ItemsRetryTests(unittest.TestCase):
    """A cached session key can die because another process (Nigel) unlocked
    the same shared vault -- one retry with a fresh key, not a silent []."""

    def setUp(self) -> None:
        bitwarden._SESSION.update(key="stale-key", at=__import__("time").time(), synced=0.0)

    def test_failed_listing_retries_once_with_fresh_session(self) -> None:
        ok_result = mock.Mock(returncode=0, stdout='[{"login": {"password": "p"}}]')
        fail_result = mock.Mock(returncode=1, stdout="")
        calls = {"n": 0}

        def fake_run(args, timeout=40, extra_env=None):
            if args[:2] == ["list", "items"]:
                calls["n"] += 1
                return fail_result if calls["n"] == 1 else ok_result
            if args[0] == "unlock":
                return mock.Mock(returncode=0, stdout="fresh-key\n")
            return mock.Mock(returncode=0, stdout='{"status": "unlocked"}')

        with mock.patch("grocery_bot.bitwarden._run", side_effect=fake_run), \
             mock.patch("grocery_bot.bitwarden._sync"):
            items = bitwarden._items("https://example.com")

        self.assertEqual(calls["n"], 2)
        self.assertEqual(items[0]["login"]["password"], "p")

    def test_second_failure_returns_empty_not_raise(self) -> None:
        fail_result = mock.Mock(returncode=1, stdout="")

        def fake_run(args, timeout=40, extra_env=None):
            if args[:2] == ["list", "items"]:
                return fail_result
            if args[0] == "unlock":
                return mock.Mock(returncode=0, stdout="fresh-key\n")
            return mock.Mock(returncode=0, stdout='{"status": "unlocked"}')

        with mock.patch("grocery_bot.bitwarden._run", side_effect=fake_run), \
             mock.patch("grocery_bot.bitwarden._sync"):
            self.assertEqual(bitwarden._items("https://example.com"), [])


if __name__ == "__main__":
    unittest.main()
