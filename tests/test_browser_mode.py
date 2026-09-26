"""The remote-Chrome (CDP) path: one tab in the household's browser, or local.

Ishay, 2026-09-21: the store browsers may run in a dedicated Chrome on
liran-aba-pc (GordonChrome, port 9224). These tests pin the contract:
the default context is used, only the tab we opened is closed, an
unreachable PC falls back to the local browser with one warning, the
per-store switch is honoured, and the exit-node probe is skipped only
when every enabled store is on the remote browser.
"""
import unittest
from unittest import mock

from grocery_bot import browser
from grocery_bot.config import Config
from grocery_bot.connectivity import ExitStatus

CDP = "http://100.64.121.81:9224"


def _config(stores, cdp_url=CDP, cdp_stores=None):
    return Config(
        telegram_bot_token="t", allowed_telegram_user_ids=[], db_path=":memory:",
        shufersal_storage_state_path="x.json", tivtaam_storage_state_path="y.json",
        enabled_stores=stores, playwright_proxy="socks5://localhost:1055",
        browser_cdp_url=cdp_url,
        browser_cdp_stores=cdp_stores if cdp_stores is not None else ["shufersal", "tivtaam"],
    )


class _FakePlaywright:
    """Enough of sync_playwright() for the constructors and close()."""

    def __init__(self, contexts):
        self.stopped = False
        self.browser = mock.Mock()
        self.browser.contexts = contexts
        self.browser.new_context.return_value = mock.Mock(name="fresh-context")
        self.chromium = mock.Mock()
        self.chromium.connect_over_cdp.return_value = self.browser
        self.chromium.launch.return_value = mock.Mock(name="local-browser")
        self.chromium.launch_persistent_context.return_value = mock.Mock(name="persistent")

    def start(self):
        return self

    def stop(self):
        self.stopped = True


class PlanTests(unittest.TestCase):
    def test_reachable_cdp_puts_configured_stores_on_cdp(self):
        plan = browser.plan_for(_config(["shufersal", "tivtaam"]), probe=lambda url: True)
        self.assertEqual(plan, {"shufersal": "cdp", "tivtaam": "cdp"})

    def test_per_store_switch_is_honoured(self):
        plan = browser.plan_for(_config(["shufersal", "tivtaam"], cdp_stores=["tivtaam"]),
                                probe=lambda url: True)
        self.assertEqual(plan, {"shufersal": "local", "tivtaam": "cdp"})

    def test_unreachable_cdp_is_local_everywhere(self):
        plan = browser.plan_for(_config(["shufersal", "tivtaam"]), probe=lambda url: False)
        self.assertEqual(plan, {"shufersal": "local", "tivtaam": "local"})

    def test_no_url_means_local_and_no_probe(self):
        calls = []
        plan = browser.plan_for(_config(["tivtaam"], cdp_url=""), probe=lambda url: calls.append(url) or True)
        self.assertEqual(plan, {"tivtaam": "local"})
        self.assertEqual(calls, [])

    def test_reachability_is_probed_once_per_url(self):
        calls = []
        browser.plan_for(_config(["shufersal", "tivtaam"]), probe=lambda url: calls.append(url) or True)
        self.assertEqual(len(calls), 1)


class ExitProbeTests(unittest.TestCase):
    def test_probe_skipped_only_when_every_store_is_remote(self):
        with mock.patch("grocery_bot.exitnode.ensure_israeli_exit") as probe:
            status = browser.exit_status(_config(["shufersal", "tivtaam"]), probe=lambda url: True)
        self.assertTrue(status.available)
        probe.assert_not_called()

    def test_probe_kept_when_one_store_stays_local(self):
        with mock.patch("grocery_bot.exitnode.ensure_israeli_exit",
                        return_value=ExitStatus(False, "down")) as probe:
            status = browser.exit_status(
                _config(["shufersal", "tivtaam"], cdp_stores=["tivtaam"]), probe=lambda url: True
            )
        self.assertFalse(status.available)
        probe.assert_called_once_with("socks5://localhost:1055", prefer_primary=True)

    def test_probe_kept_when_the_pc_is_off(self):
        with mock.patch("grocery_bot.exitnode.ensure_israeli_exit",
                        return_value=ExitStatus(True, "ok", "IL")) as probe:
            browser.exit_status(_config(["tivtaam"]), probe=lambda url: False)
        probe.assert_called_once()

    def test_telegram_wrapper_routes_through_the_plan(self):
        from grocery_bot import telegram_bot

        with mock.patch("grocery_bot.exitnode.ensure_israeli_exit") as probe, \
             mock.patch("grocery_bot.browser.cdp_reachable", return_value=True):
            status = telegram_bot.ensure_israeli_exit("socks5://x", _config(["tivtaam"]))
        self.assertTrue(status.available)
        probe.assert_not_called()


class ReachabilityTests(unittest.TestCase):
    def test_empty_url_is_unreachable(self):
        self.assertFalse(browser.cdp_reachable(""))

    def test_connection_error_is_unreachable_not_an_exception(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("refused")):
            self.assertFalse(browser.cdp_reachable(CDP))

    def test_version_answer_is_reachable(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = b'{"Browser": "Chrome/153"}'
        with mock.patch("urllib.request.urlopen", return_value=resp):
            self.assertTrue(browser.cdp_reachable(CDP))


class TivTaamRemoteTests(unittest.TestCase):
    def _adapter(self, reachable, contexts):
        from grocery_bot.adapters import tivtaam

        fake = _FakePlaywright(contexts)
        with mock.patch("playwright.sync_api.sync_playwright", return_value=fake), \
             mock.patch("grocery_bot.browser.cdp_reachable", return_value=reachable), \
             mock.patch("os.path.exists", return_value=True), \
             mock.patch("os.path.isdir", return_value=False):
            adapter = tivtaam.TivTaamAdapter("y.json", proxy="socks5://x", cdp_url=CDP)
        return adapter, fake

    def test_uses_the_default_context_and_a_fresh_tab(self):
        default_ctx = mock.Mock(name="default-context")
        adapter, fake = self._adapter(True, [default_ctx])
        fake.chromium.connect_over_cdp.assert_called_once_with(CDP)
        fake.chromium.launch.assert_not_called()
        self.assertIs(adapter._context, default_ctx)
        default_ctx.new_page.assert_called_once()
        fake.browser.new_context.assert_not_called()

    def test_close_closes_only_our_tab(self):
        default_ctx = mock.Mock(name="default-context")
        adapter, fake = self._adapter(True, [default_ctx])
        adapter.close()
        adapter._page.close.assert_called_once()
        default_ctx.close.assert_not_called()
        fake.browser.close.assert_not_called()
        self.assertTrue(fake.stopped)

    def test_unreachable_cdp_falls_back_to_local_with_one_warning(self):
        with self.assertLogs("grocery_bot.adapters.tivtaam", level="WARNING") as logs:
            adapter, fake = self._adapter(False, [])
        fake.chromium.connect_over_cdp.assert_not_called()
        fake.chromium.launch.assert_called_once()
        self.assertFalse(adapter._remote)
        self.assertEqual(len([l for l in logs.output if "falling back" in l]), 1)


class ShufersalRemoteTests(unittest.TestCase):
    def _adapter(self, reachable, contexts):
        from grocery_bot.adapters import shufersal

        fake = _FakePlaywright(contexts)
        with mock.patch("playwright.sync_api.sync_playwright", return_value=fake), \
             mock.patch("grocery_bot.browser.cdp_reachable", return_value=reachable), \
             mock.patch.object(shufersal.ShufersalAdapter, "_login") as login:
            adapter = shufersal.ShufersalAdapter(
                "/nonexistent/x.json", proxy="", username="u", password="p", cdp_url=CDP
            )
        return adapter, fake, login

    def test_remote_needs_no_proxy_no_session_file_no_login(self):
        default_ctx = mock.Mock(name="default-context")
        adapter, fake, login = self._adapter(True, [default_ctx])
        login.assert_not_called()
        self.assertIs(adapter._context, default_ctx)
        fake.chromium.launch.assert_not_called()

    def test_remote_renewal_logs_in_inside_our_tab_and_keeps_the_context(self):
        default_ctx = mock.Mock(name="default-context")
        adapter, fake, _login = self._adapter(True, [default_ctx])
        with mock.patch.object(adapter, "is_session_valid", side_effect=[False, True]), \
             mock.patch("grocery_bot.login.fill_login_form") as fill:
            self.assertTrue(adapter.ensure_session())
        fill.assert_called_once_with(adapter._page, "u", "p")
        default_ctx.close.assert_not_called()
        fake.browser.new_context.assert_not_called()

    def test_unreachable_without_proxy_still_refuses_like_before(self):
        from grocery_bot.adapters import shufersal

        fake = _FakePlaywright([])
        with mock.patch("playwright.sync_api.sync_playwright", return_value=fake), \
             mock.patch("grocery_bot.browser.cdp_reachable", return_value=False), \
             self.assertRaises(RuntimeError):
            shufersal.ShufersalAdapter("x.json", proxy="", cdp_url=CDP)


class FactoryTests(unittest.TestCase):
    def test_factories_pass_the_cdp_url_only_to_configured_stores(self):
        from grocery_bot import telegram_bot

        seen = {}

        class Fake:
            def __init__(self, path, **kw):
                seen[kw.get("cdp_url")] = True
                self.kw = kw

        with mock.patch.dict(telegram_bot.ADAPTER_CLASSES, {"shufersal": Fake, "tivtaam": Fake}):
            factories = telegram_bot._build_adapter_factories(
                _config(["shufersal", "tivtaam"], cdp_stores=["tivtaam"])
            )
            self.assertEqual(factories["tivtaam"]().kw["cdp_url"], CDP)
            self.assertEqual(factories["shufersal"]().kw["cdp_url"], "")


class ConfigTests(unittest.TestCase):
    def test_env_round_trip(self):
        env = {"GROCERY_TELEGRAM_BOT_TOKEN": "t", "GORDON_BROWSER_CDP_URL": " http://100.64.121.81:9224 ",
               "GORDON_BROWSER_CDP_STORES": "tivtaam"}
        with mock.patch.dict("os.environ", env, clear=False), \
             mock.patch("grocery_bot.config._shufersal_credentials", return_value=("", "", "env")):
            cfg = Config.from_env()
        self.assertEqual(cfg.browser_cdp_url, "http://100.64.121.81:9224")
        self.assertEqual(cfg.browser_cdp_stores, ["tivtaam"])

    def test_default_is_local_for_both(self):
        env = {"GROCERY_TELEGRAM_BOT_TOKEN": "t"}
        with mock.patch.dict("os.environ", env, clear=True), \
             mock.patch("grocery_bot.config._shufersal_credentials", return_value=("", "", "env")):
            cfg = Config.from_env()
        self.assertEqual(cfg.browser_cdp_url, "")
        self.assertEqual(browser.plan_for(cfg), {"shufersal": "local"})



class ShufersalFallbackTests(unittest.TestCase):
    """Local primary, GordonChrome while the exit node is down (Ishay, 26.09)."""

    def _cfg(self):
        import dataclasses
        return dataclasses.replace(_config(["shufersal", "tivtaam"], cdp_stores=["tivtaam"]),
                                   browser_cdp_fallback_stores=["shufersal"])

    def tearDown(self):
        browser._FALLBACK_ACTIVE.clear()

    def test_a_working_exit_keeps_shufersal_local(self):
        with mock.patch("grocery_bot.exitnode.ensure_israeli_exit", return_value=ExitStatus(True, "ok", "IL")):
            self.assertTrue(browser.exit_status(self._cfg(), probe=lambda url: True).available)
        self.assertEqual(browser.cdp_url_for(self._cfg(), "shufersal"), "")

    def test_a_dead_exit_moves_shufersal_to_the_remote_chrome(self):
        with mock.patch("grocery_bot.exitnode.ensure_israeli_exit", return_value=ExitStatus(False, "down")):
            status = browser.exit_status(self._cfg(), probe=lambda url: True)
        self.assertTrue(status.available)
        self.assertEqual(browser.cdp_url_for(self._cfg(), "shufersal"), CDP)

    def test_no_fallback_when_the_remote_chrome_is_down_too(self):
        with mock.patch("grocery_bot.exitnode.ensure_israeli_exit", return_value=ExitStatus(False, "down")):
            # tivtaam's own probe and the fallback probe both fail
            self.assertFalse(browser.exit_status(self._cfg(), probe=lambda url: False).available)
        self.assertEqual(browser.cdp_url_for(self._cfg(), "shufersal"), "")

    def test_the_fallback_ends_when_the_exit_returns(self):
        with mock.patch("grocery_bot.exitnode.ensure_israeli_exit", return_value=ExitStatus(False, "down")):
            browser.exit_status(self._cfg(), probe=lambda url: True)
        with mock.patch("grocery_bot.exitnode.ensure_israeli_exit", return_value=ExitStatus(True, "ok", "IL")):
            browser.exit_status(self._cfg(), probe=lambda url: True)
        self.assertEqual(browser.cdp_url_for(self._cfg(), "shufersal"), "")


if __name__ == "__main__":
    unittest.main()
