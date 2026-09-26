"""Pick a working Israeli exit node instead of depending on one device.

The store traffic leaves through a Tailscale exit node on a device at the
user's home. For a long time that meant one Android TV box, which is
exactly as reliable as a TV box that gets switched off — it dropped four
times in a single afternoon, and Tailscale's own documentation is blunt
that an Android exit node "is not performant" and wants mains power.

Adding more devices does not help on its own: **Tailscale does not fail
over between exit nodes.** The client is pinned to one node, so a second
box sitting there online is never used while the pinned one sleeps. This
module is the missing piece — try the current node, and if it cannot
reach Israel, switch to another one that can.

Two properties matter more than speed here:

- **A node is only acceptable if it actually exits via Israel.** A phone
  is the most reliably-awake device in the house, and also the one that
  leaves the country. Selecting it abroad would produce HTTP 200
  geo-block pages that read like broken selectors, so every candidate is
  probed for country IL before being kept.
- **Switching must not disturb the rest of the machine.** Tailscale runs
  here in userspace mode serving a SOCKS5 port rather than as the default
  route, precisely because two other family bots share this box. Changing
  the exit node only changes what leaves through that port.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .connectivity import check_israeli_exit

logger = logging.getLogger(__name__)

DEFAULT_CLI = str(Path.home() / "tailscale" / "tailscale")
DEFAULT_SOCKET = str(Path.home() / ".config" / "tailscale" / "tailscaled.sock")
CLI_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class ExitNode:
    node_id: str
    hostname: str
    ip: str
    online: bool
    os_name: str = ""


# Ishay, 2026-09-20 (relayed by Arthur, usage-audit, with the quote and
# date; exitnode.py:101 confirmed at the time to have no explicit
# tiebreak between online nodes -- only Tailscale's own status-JSON
# order): "אני רוצה שהמחשב החדש יהיה בעדיפות ביצוע לפני המחשב הישן" --
# the new computer before the old one, liran-aba-pc before uset-pc.
# Matched on hostname first (case-insensitive, domain suffix stripped --
# Tailscale's own HostName field varies in exactly that way across
# clients) and IP as a fallback, so a hostname change alone doesn't
# silently drop the priority.
_PRIORITY_HOSTS = {"liran-aba-pc": 0, "uset-pc": 1}
_PRIORITY_IPS = {"100.64.121.81": 0, "100.98.50.71": 1}
_DEFAULT_PRIORITY = 2


def _priority(node: "ExitNode") -> int:
    host = (node.hostname or "").split(".")[0].lower()
    if host in _PRIORITY_HOSTS:
        return _PRIORITY_HOSTS[host]
    return _PRIORITY_IPS.get(node.ip, _DEFAULT_PRIORITY)


def _cli() -> list[str]:
    binary = os.environ.get("TAILSCALE_CLI_PATH", DEFAULT_CLI)
    socket = os.environ.get("TAILSCALE_SOCKET_PATH", DEFAULT_SOCKET)
    return [binary, f"--socket={socket}"]


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        _cli() + args,
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT_SECONDS,
    )


def list_exit_nodes() -> list[ExitNode]:
    """Every peer offering itself as an exit node, online first.

    Returns [] rather than raising: a broken Tailscale CLI should leave
    the caller on its existing route, not crash a shopping run.
    """
    try:
        result = _run(["status", "--json"])
        if result.returncode != 0:
            logger.warning("tailscale status failed: %s", result.stderr[:200])
            return []
        payload = json.loads(result.stdout)
    except Exception:
        logger.exception("Could not read Tailscale status")
        return []

    nodes = []
    for peer in (payload.get("Peer") or {}).values():
        if not peer.get("ExitNodeOption"):
            continue
        ips = peer.get("TailscaleIPs") or []
        nodes.append(
            ExitNode(
                node_id=peer.get("ID", ""),
                hostname=peer.get("HostName", ""),
                ip=ips[0] if ips else "",
                online=bool(peer.get("Online")),
                os_name=peer.get("OS", ""),
            )
        )
    # Online candidates first; an offline one is only worth trying if
    # nothing else is left, since Tailscale's view can lag reality.
    # Among online candidates, liran-aba-pc before uset-pc (see
    # _PRIORITY_HOSTS above); everything else keeps Tailscale's own
    # order (stable sort).
    return sorted(nodes, key=lambda n: (not n.online, _priority(n)))


def select_exit_node(node: ExitNode) -> bool:
    """Route the SOCKS proxy's traffic through `node`."""
    target = node.ip or node.hostname
    if not target:
        return False
    try:
        result = _run(["set", f"--exit-node={target}"])
    except Exception:
        logger.exception("Could not switch exit node to %s", node.hostname)
        return False
    if result.returncode != 0:
        logger.warning("Switching to %s failed: %s", node.hostname, result.stderr[:200])
        return False
    logger.info("Exit node switched to %s (%s)", node.hostname, target)
    return True


def current_exit_id() -> str:
    """Tailscale's ID for the exit node in use now, or "" if none/unknown."""
    try:
        result = _run(["status", "--json"])
        if result.returncode != 0:
            return ""
        return str((json.loads(result.stdout).get("ExitNodeStatus") or {}).get("ID") or "")
    except Exception:
        logger.exception("Could not read the current exit node")
        return ""


def _return_to_primary(proxy: str):
    """Move back to liran-aba-pc when it is online and we are elsewhere.

    Basics in Order §3 (Ishay, 26.09.2026, via Boss under Mandate 1):
    "exit node ב-liran-aba-pc עם האייפון כגיבוי". Failover alone never
    comes back: once on the iPhone it stayed there while the PC sat idle
    and online (found 26.09 — the iPhone route then failed the Tiv Taam
    feed). Returns the probe on the primary if it works; otherwise puts
    the previous node back and returns None.
    """
    nodes = [n for n in list_exit_nodes() if n.online]
    primary = next((n for n in nodes if _priority(n) == 0), None)
    if primary is None:
        return None
    current = current_exit_id()
    if current and current == primary.node_id:
        return None
    if not select_exit_node(primary):
        return None
    probed = check_israeli_exit(proxy)
    if probed.available:
        logger.info("Returned to primary exit node %s", primary.hostname)
        return probed
    logger.info("Primary exit %s not usable (%s); staying on backup", primary.hostname, probed.detail)
    previous = next((n for n in nodes if n.node_id == current), None)
    if previous is not None:
        select_exit_node(previous)
    return None


def ensure_israeli_exit(proxy: str, prefer_primary: bool = False):
    """Return a usable Israeli exit, switching nodes if the current one is down.

    Tries the currently selected node first so a healthy setup costs one
    probe and no reconfiguration. Only if that fails does it walk the
    other candidates, keeping the first that genuinely reaches Israel.

    `prefer_primary` (run starts only, never the mid-run breaker probe —
    switching routes under a live browser session is its own failure)
    first moves back to liran-aba-pc if it is online and not in use.
    """
    if prefer_primary:
        back = _return_to_primary(proxy)
        if back is not None:
            return back
    status = check_israeli_exit(proxy)
    if status.available:
        return status

    advertised = list_exit_nodes()
    # Only nodes Tailscale currently sees as online. Without this filter
    # the walk would happily *select* a node that has been unreachable
    # for days, and the loop's own "leave the last attempt in place"
    # then left the proxy pointed at it — which is precisely what was
    # found on 2026-09-09: the exit node set to a TV box last seen eight
    # days earlier, while the household's phone sat online and unused.
    # One transient probe failure was enough to park the whole system on
    # a dead route, and every store request failed until someone noticed.
    candidates = [node for node in advertised if node.online]
    skipped = [node.hostname for node in advertised if not node.online]
    if skipped:
        logger.info("Ignoring offline exit nodes: %s", ", ".join(skipped))
    if not candidates:
        logger.info("No online exit nodes advertised; nothing to fail over to")
        return status

    for node in candidates:
        if not select_exit_node(node):
            continue
        probed = check_israeli_exit(proxy)
        if probed.available:
            logger.info("Failed over to exit node %s", node.hostname)
            return probed
        logger.info(
            "Exit node %s is not usable (%s); trying the next one",
            node.hostname,
            probed.detail,
        )

    # Nothing reached Israel. Leave the selection on an online node
    # anyway: a route that is merely failing right now can recover on its
    # own, while one pointed at a device that is switched off cannot.
    if candidates:
        select_exit_node(candidates[0])
    return check_israeli_exit(proxy)
