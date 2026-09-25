import unittest
from unittest import mock

from grocery_bot.config import Config

BASE_ENV = {"GROCERY_TELEGRAM_BOT_TOKEN": "t"}


class ShufersalCredentialSourceTests(unittest.TestCase):
    """Bitwarden is the primary channel (Ishay, 2026-09-18); plain env vars
    must keep working unchanged whenever the vault isn't configured."""

    def test_falls_back_to_env_vars_when_bitwarden_unavailable(self) -> None:
        env = {**BASE_ENV, "SHUFERSAL_USERNAME": "envuser", "SHUFERSAL_PASSWORD": "envpass"}
        with mock.patch.dict("os.environ", env, clear=True), \
             mock.patch("grocery_bot.bitwarden.available", return_value=False):
            config = Config.from_env()
        self.assertEqual(config.shufersal_username, "envuser")
        self.assertEqual(config.shufersal_password, "envpass")
        self.assertEqual(config.shufersal_credential_source, "env")

    def test_uses_bitwarden_when_available_and_item_matches(self) -> None:
        env = {**BASE_ENV, "SHUFERSAL_USERNAME": "envuser", "SHUFERSAL_PASSWORD": "envpass"}
        with mock.patch.dict("os.environ", env, clear=True), \
             mock.patch("grocery_bot.bitwarden.available", return_value=True), \
             mock.patch("grocery_bot.bitwarden.username_for", return_value="vaultuser"), \
             mock.patch("grocery_bot.bitwarden.password_for", return_value="vaultpass"):
            config = Config.from_env()
        self.assertEqual(config.shufersal_username, "vaultuser")
        self.assertEqual(config.shufersal_password, "vaultpass")
        self.assertEqual(config.shufersal_credential_source, "bitwarden")

    def test_falls_back_to_env_when_bitwarden_available_but_no_matching_item(self) -> None:
        env = {**BASE_ENV, "SHUFERSAL_USERNAME": "envuser", "SHUFERSAL_PASSWORD": "envpass"}
        with mock.patch.dict("os.environ", env, clear=True), \
             mock.patch("grocery_bot.bitwarden.available", return_value=True), \
             mock.patch("grocery_bot.bitwarden.username_for", return_value=None), \
             mock.patch("grocery_bot.bitwarden.password_for", return_value=None):
            config = Config.from_env()
        self.assertEqual(config.shufersal_username, "envuser")
        self.assertEqual(config.shufersal_credential_source, "env")

    def test_bitwarden_exception_falls_back_to_env_without_raising(self) -> None:
        env = {**BASE_ENV, "SHUFERSAL_USERNAME": "envuser", "SHUFERSAL_PASSWORD": "envpass"}
        with mock.patch.dict("os.environ", env, clear=True), \
             mock.patch("grocery_bot.bitwarden.available", return_value=True), \
             mock.patch("grocery_bot.bitwarden.username_for", side_effect=RuntimeError("bw unlock failed")):
            config = Config.from_env()
        self.assertEqual(config.shufersal_username, "envuser")
        self.assertEqual(config.shufersal_credential_source, "env")

    def test_no_env_vars_and_no_bitwarden_leaves_empty_strings(self) -> None:
        with mock.patch.dict("os.environ", BASE_ENV, clear=True), \
             mock.patch("grocery_bot.bitwarden.available", return_value=False):
            config = Config.from_env()
        self.assertEqual(config.shufersal_username, "")
        self.assertEqual(config.shufersal_password, "")
        self.assertEqual(config.shufersal_credential_source, "env")


if __name__ == "__main__":
    unittest.main()
