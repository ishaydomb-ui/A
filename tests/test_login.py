import unittest
from unittest import mock

from grocery_bot.login import login_and_save_session


class ReuseBrowserTests(unittest.TestCase):
    """Renewal must reuse the caller's browser, never start a second one.

    A second sync_playwright() inside a running one raises "Sync API
    inside the asyncio loop", so an adapter renewing its own session
    would fail every time -- which is exactly what happened in practice.
    """

    def test_supplied_browser_is_used_and_playwright_not_restarted(self) -> None:
        browser = mock.MagicMock()
        page = browser.new_context.return_value.new_page.return_value
        page.url = "https://www.shufersal.co.il/online/he/my-account"
        page.locator.return_value.count.return_value = 0

        with mock.patch("playwright.sync_api.sync_playwright") as started:
            login_and_save_session(
                username="u", password="p", output_path="/tmp/x.json",
                proxy="socks5://localhost:1055", browser=browser,
            )
            started.assert_not_called()

        browser.new_context.assert_called_once()
        browser.close.assert_not_called()  # the caller still owns it

    def test_supplied_browser_context_is_closed(self) -> None:
        browser = mock.MagicMock()
        page = browser.new_context.return_value.new_page.return_value
        page.url = "https://www.shufersal.co.il/online/he/my-account"
        page.locator.return_value.count.return_value = 0

        login_and_save_session(
            username="u", password="p", output_path="/tmp/x.json",
            proxy="socks5://localhost:1055", browser=browser,
        )
        browser.new_context.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
