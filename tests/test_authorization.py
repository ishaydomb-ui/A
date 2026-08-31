import unittest
from types import SimpleNamespace

from grocery_bot.config import Config
from grocery_bot.telegram_bot import _authorized


def _config(ids):
    return Config(
        telegram_bot_token="x", allowed_telegram_user_ids=ids, db_path=":memory:",
        shufersal_storage_state_path="", tivtaam_storage_state_path="",
        enabled_stores=["shufersal"],
    )


def _update(user_id):
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id))


class AuthorizationTests(unittest.TestCase):
    def test_an_empty_allowlist_denies_everyone(self) -> None:
        """Fails closed on purpose.

        The bot has a public @username, exposes the household's list and
        routines, and /start_order fills a real cart on a real store
        account. An open default fails silently; a closed one fails
        loudly and is fixed by one env var.
        """
        self.assertFalse(_authorized(_config([]), _update(999)))

    def test_a_listed_user_is_allowed(self) -> None:
        self.assertTrue(_authorized(_config([123]), _update(123)))

    def test_an_unlisted_user_is_denied(self) -> None:
        self.assertFalse(_authorized(_config([123]), _update(999)))

    def test_a_second_household_member_can_be_added(self) -> None:
        self.assertTrue(_authorized(_config([123, 456]), _update(456)))

    def test_a_missing_user_is_denied(self) -> None:
        update = SimpleNamespace(effective_user=None)
        self.assertFalse(_authorized(_config([123]), update))


if __name__ == "__main__":
    unittest.main()
