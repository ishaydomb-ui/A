import json
import subprocess
import unittest
from unittest import mock

from grocery_bot.connectivity import ExitStatus
from grocery_bot.exitnode import ExitNode, ensure_israeli_exit, list_exit_nodes, select_exit_node


def _status_json(peers):
    return json.dumps({"Peer": {str(i): p for i, p in enumerate(peers)}})


def _peer(host, online=True, exit_option=True, ip="100.0.0.1", os_name="android"):
    return {
        "HostName": host, "Online": online, "ExitNodeOption": exit_option,
        "TailscaleIPs": [ip], "OS": os_name, "ID": "id-" + host,
    }


def _ok(stdout="", code=0):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr="")


class ListExitNodesTests(unittest.TestCase):
    def test_only_peers_offering_themselves_are_listed(self) -> None:
        payload = _status_json([_peer("box"), _peer("laptop", exit_option=False)])
        with mock.patch("subprocess.run", return_value=_ok(payload)):
            self.assertEqual([n.hostname for n in list_exit_nodes()], ["box"])

    def test_online_nodes_come_first(self) -> None:
        payload = _status_json([_peer("asleep", online=False), _peer("awake")])
        with mock.patch("subprocess.run", return_value=_ok(payload)):
            self.assertEqual([n.hostname for n in list_exit_nodes()], ["awake", "asleep"])

    def test_a_broken_cli_is_not_fatal(self) -> None:
        """A shopping run must not die because Tailscale's CLI misbehaved."""
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            self.assertEqual(list_exit_nodes(), [])

    def test_a_failing_command_is_not_fatal(self) -> None:
        with mock.patch("subprocess.run", return_value=_ok("", code=1)):
            self.assertEqual(list_exit_nodes(), [])

    def test_garbage_output_is_not_fatal(self) -> None:
        with mock.patch("subprocess.run", return_value=_ok("not json")):
            self.assertEqual(list_exit_nodes(), [])


class SelectTests(unittest.TestCase):
    def test_switch_uses_the_node_ip(self) -> None:
        with mock.patch("subprocess.run", return_value=_ok()) as run:
            self.assertTrue(select_exit_node(ExitNode("i", "box", "100.0.0.5", True)))
        self.assertIn("--exit-node=100.0.0.5", run.call_args[0][0])

    def test_a_node_without_an_address_is_refused(self) -> None:
        self.assertFalse(select_exit_node(ExitNode("i", "", "", True)))

    def test_a_failed_switch_reports_false(self) -> None:
        with mock.patch("subprocess.run", return_value=_ok(code=1)):
            self.assertFalse(select_exit_node(ExitNode("i", "box", "100.0.0.5", True)))


class FailoverTests(unittest.TestCase):
    def test_a_healthy_exit_is_left_alone(self) -> None:
        """The common case must cost one probe and change nothing."""
        with mock.patch(
            "grocery_bot.exitnode.check_israeli_exit",
            return_value=ExitStatus(True, "fine", "IL"),
        ), mock.patch("grocery_bot.exitnode.select_exit_node") as switch:
            self.assertTrue(ensure_israeli_exit("socks5://x").available)
            switch.assert_not_called()

    def test_switches_to_a_working_node_when_the_current_one_is_down(self) -> None:
        probes = [ExitStatus(False, "down"), ExitStatus(True, "ok", "IL")]
        with mock.patch(
            "grocery_bot.exitnode.check_israeli_exit", side_effect=probes
        ), mock.patch(
            "grocery_bot.exitnode.list_exit_nodes",
            return_value=[ExitNode("i", "phone", "100.0.0.9", True)],
        ), mock.patch("grocery_bot.exitnode.select_exit_node", return_value=True) as switch:
            self.assertTrue(ensure_israeli_exit("socks5://x").available)
            switch.assert_called_once()

    def test_a_node_outside_israel_is_skipped(self) -> None:
        """A phone abroad exits via the wrong country and must be rejected.

        Accepting it would serve HTTP 200 geo-block pages that look like
        broken selectors rather than a network problem.
        """
        probes = [
            ExitStatus(False, "down"),
            ExitStatus(False, "abroad", "GR"),
            ExitStatus(True, "ok", "IL"),
        ]
        nodes = [ExitNode("a", "phone", "100.0.0.9", True), ExitNode("b", "box", "100.0.0.8", True)]
        with mock.patch(
            "grocery_bot.exitnode.check_israeli_exit", side_effect=probes
        ), mock.patch("grocery_bot.exitnode.list_exit_nodes", return_value=nodes), mock.patch(
            "grocery_bot.exitnode.select_exit_node", return_value=True
        ) as switch:
            self.assertTrue(ensure_israeli_exit("socks5://x").available)
            self.assertEqual(switch.call_count, 2)

    def test_all_nodes_down_reports_unavailable(self) -> None:
        with mock.patch(
            "grocery_bot.exitnode.check_israeli_exit", return_value=ExitStatus(False, "down")
        ), mock.patch(
            "grocery_bot.exitnode.list_exit_nodes",
            return_value=[ExitNode("i", "box", "100.0.0.8", True)],
        ), mock.patch("grocery_bot.exitnode.select_exit_node", return_value=True):
            self.assertFalse(ensure_israeli_exit("socks5://x").available)

    def test_no_candidates_reports_the_original_failure(self) -> None:
        with mock.patch(
            "grocery_bot.exitnode.check_israeli_exit", return_value=ExitStatus(False, "down")
        ), mock.patch("grocery_bot.exitnode.list_exit_nodes", return_value=[]):
            self.assertFalse(ensure_israeli_exit("socks5://x").available)


if __name__ == "__main__":
    unittest.main()
