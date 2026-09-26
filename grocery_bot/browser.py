"""Where a store adapter's browser runs: the household's PC, or here.

Ishay, 2026-09-21: the store browsers may run inside a dedicated Chrome
on the household PC (`liran-aba-pc`), reached over Tailscale through
Chrome's remote-debugging port. That browser sits in Israel with a real
desktop fingerprint and a profile a person logged into once, so the
CDP path needs neither the SOCKS exit node nor a captured session
file. When the PC is off, everything falls back to today's local
Chromium behind `PLAYWRIGHT_PROXY`.

**One Chrome process per bot.** A debugging port belongs to a whole
Chrome process, not to a window: anyone on the port sees, and can
close, every window of that process. So Gordon's Chrome is its own
process with its own `--user-data-dir` and port (GordonChrome, 9224),
separate from MiriChrome (9222) and BobEdge (9223). Gordon must never
connect to those.
"""
from __future__ import annotations

import json
import logging
import urllib.request

from .connectivity import ExitStatus

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 3.0
CDP = "cdp"
LOCAL = "local"


def cdp_reachable(url: str, timeout: float = PROBE_TIMEOUT_SECONDS) -> bool:
    """True when `url` answers Chrome's /json/version within `timeout`."""
    if not url:
        return False
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/json/version", timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace") or "{}")
        return bool(payload.get("Browser") or payload.get("webSocketDebuggerUrl"))
    except Exception:  # noqa: BLE001 - unreachable is an answer, not an error
        return False


def cdp_url_for(config, store: str) -> str:
    """The CDP URL this store is configured to use, or "" for local."""
    url = getattr(config, "browser_cdp_url", "") or ""
    stores = getattr(config, "browser_cdp_stores", None) or []
    return url if url and store in stores else ""


def plan_for(config, probe=cdp_reachable) -> dict[str, str]:
    """Per enabled store: "cdp" when configured and reachable now, else "local".

    Reachability is probed once here, so a PC that is off costs one
    3-second probe per store per run and nothing else.
    """
    plan: dict[str, str] = {}
    reachable_cache: dict[str, bool] = {}
    for store in getattr(config, "enabled_stores", []) or []:
        url = cdp_url_for(config, store)
        if url:
            if url not in reachable_cache:
                reachable_cache[url] = bool(probe(url))
            plan[store] = CDP if reachable_cache[url] else LOCAL
        else:
            plan[store] = LOCAL
    return plan


def exit_status(config, probe=cdp_reachable) -> ExitStatus:
    """The Israeli-exit check, skipped only when no store will use the proxy.

    A run whose every enabled store goes through the remote Chrome never
    touches the SOCKS route, so probing (and possibly re-selecting) the
    exit node would be wasted work and a spurious "no connection" when
    the TV box is off. Any store still on the local browser keeps the
    real probe, failover included.
    """
    from .exitnode import ensure_israeli_exit

    plan = plan_for(config, probe=probe)
    if plan and all(mode == CDP for mode in plan.values()):
        return ExitStatus(True, "remote browser; exit node not needed", "IL")
    return ensure_israeli_exit(getattr(config, "playwright_proxy", ""), prefer_primary=True)
