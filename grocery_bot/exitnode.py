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
    return sorted(nodes, key=lambda n: not n.online)


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


def ensure_israeli_exit(proxy: str):
    """Return a usable Israeli exit, switching nodes if the current one is down.

    Tries the currently selected node first so a healthy setup costs one
    probe and no reconfiguration. Only if that fails does it walk the
    other candidates, keeping the first that genuinely reaches Israel.
    """
    status = check_israeli_exit(proxy)
    if status.available:
        return status

    candidates = list_exit_nodes()
    if not candidates:
        logger.info("No exit nodes advertised; nothing to fail over to")
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
    # Nothing worked: leave the last attempt in place and report honestly,
    # so the caller queues the cycle instead of running it into a wall.
    return check_israeli_exit(proxy)
