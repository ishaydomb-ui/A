"""Stop issuing cart mutations when the infrastructure is unhealthy.

Phase 4 of the reliability build (2026-09-17). The failure this exists
for was observed, not imagined: on 2026-09-17 the SOCKS route dropped
mid-run and the fill loop kept going — fourteen consecutive `page.goto`
calls, each timing out on its own, each recorded as a product failure.
Nothing distinguished "the eighth item failed" from "the network died at
the seventh".

This is the smallest mechanism that prevents a recurrence. It is not a
retry framework: one object per store per run, three counters, and two
probes. Everything it does is decided by **which kind** of failure it is
looking at, because the kinds behave differently and were measured to:

- **infrastructure, high confidence** — `ERR_SOCKS_*`, `ERR_PROXY_*`,
  `ERR_NAME_NOT_RESOLVED`, httpx `ProxyError`/`ConnectTimeout`. These
  never mean anything about a product. **Trip on one.**
- **session** — expired, browser closed. Recover the session; the route
  is fine. Never trips the infrastructure breaker.
- **product** — out of stock, no add control. **Never trips.**
- **ambiguous** — `Page.goto: Timeout`. Demonstrated to carry no
  information on its own: during the 2026-09-17 probe the same proxy
  fetched Shufersal's login page in 6.8s by curl while Playwright timed
  out three times, and the day before the identical text came from a
  dead route. So one timeout does nothing; **two consecutive** trigger a
  cheap out-of-band probe through the same route. Probe fails → treat as
  infrastructure. Probe succeeds → the *site* is slow; back off and
  continue, bounded.

Recovery is `exitnode.ensure_israeli_exit` — the failover that already
existed and only ever ran *before* a cycle — followed by a session check.
On success the caller re-attempts the item that tripped and continues in
the same run; on failure it stops that store and leaves the remaining
items `pending`. They are never recorded as product failures.

`GORDON_BREAKER=off` restores the old run-through behaviour exactly, for
rollback only.
"""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

# Failure details that describe the network rather than the product.
# Matched case-insensitively against the recorded detail. Every entry has
# been seen in this project's own cart_failures or journal.
INFRASTRUCTURE_MARKERS = (
    "ERR_SOCKS", "ERR_PROXY", "ERR_CONNECTION", "ERR_TUNNEL",
    "ERR_NAME_NOT_RESOLVED", "ERR_INTERNET_DISCONNECTED",
    "ProxyError", "ConnectTimeout", "ConnectError",
    "net::ERR_ABORTED",
)

SESSION_MARKERS = (
    "session expired", "could not be renewed", "browser has been closed",
    "context or browser has been closed", "target closed",
)

# Only this text is ambiguous. A generic Playwright timeout on navigation
# looks the same whether the route is dead or the site is slow.
AMBIGUOUS_MARKERS = ("timeout", "timed out")

# Two consecutive ambiguous failures before spending a probe on them.
AMBIGUOUS_BEFORE_PROBE = 2
# How many times one store may recover in one run before it is stopped.
MAX_RECOVERIES = 2
# Back-off when the probe says the route is fine and the site is not.
SITE_BACKOFF_SECONDS = 20

SITE_ROOTS = {
    "shufersal": "https://www.shufersal.co.il/online/he/",
    "tivtaam": "https://www.tivtaam.co.il/",
}


def enabled() -> bool:
    return os.environ.get("GORDON_BREAKER", "on").strip().lower() not in ("off", "0", "false")


def classify(result) -> str:
    """product | session | infrastructure | ambiguous | none.

    The adapter's own `failure_kind` wins when it set one; the detail text
    is the fallback, because Playwright errors arrive as bare `Exception`
    serialised with `str(exc)`.
    """
    status = getattr(result, "status", "") or ""
    if status not in ("error", "not_found"):
        return "none"
    kind = (getattr(result, "failure_kind", "") or "").strip()
    if kind in ("product", "session", "infrastructure", "ambiguous"):
        return kind
    low = ((getattr(result, "detail", "") or "")).lower()
    if any(m.lower() in low for m in INFRASTRUCTURE_MARKERS):
        return "infrastructure"
    if any(m in low for m in SESSION_MARKERS):
        return "session"
    if status == "not_found":
        return "product"
    if any(m in low for m in AMBIGUOUS_MARKERS):
        return "ambiguous"
    return "product"


def probe_route(proxy: str | None) -> bool:
    """Is the Israeli exit reachable right now? ~1–2s."""
    if not proxy:
        return False
    try:
        from .exitnode import check_israeli_exit

        return bool(check_israeli_exit(proxy).available)
    except Exception:  # noqa: BLE001
        logger.exception("Route probe raised")
        return False


def probe_site(store: str, proxy: str | None, timeout: float = 8.0) -> bool:
    """Does the chain answer through the same route? One GET, ~1–7s."""
    url = SITE_ROOTS.get(store)
    if not url or not proxy:
        return False
    try:
        import httpx

        socks = proxy.replace("socks5://", "socks5h://")
        with httpx.Client(proxy=socks, timeout=timeout, follow_redirects=True) as http:
            return http.get(url).status_code < 500
    except Exception:  # noqa: BLE001
        logger.info("Site probe for %s failed", store, exc_info=True)
        return False


class Breaker:
    """Per store, per run. Ask it after every result what to do next."""

    def __init__(self, store: str, proxy: str | None = None, run_id=None):
        self.store = store
        self.proxy = proxy or os.environ.get("PLAYWRIGHT_PROXY")
        self.run_id = run_id
        self.consecutive_ambiguous = 0
        self.recoveries = 0
        self.halted = False
        self.enabled = enabled()

    # -- decisions --------------------------------------------------------

    def observe(self, result) -> str:
        """continue | recover | stop.

        `recover` means: stop mutating, try to repair, re-attempt the item.
        `stop` means: this store is done for this run; leave the rest
        pending.
        """
        if not self.enabled:
            return "continue"
        kind = classify(result)
        if kind in ("none", "product"):
            self.consecutive_ambiguous = 0
            return "continue"
        if kind == "infrastructure":
            logger.warning("BREAKER run=%s store=%s tripped kind=infrastructure",
                           self.run_id, self.store)
            return self._recover_or_stop()
        if kind == "session":
            logger.warning("BREAKER run=%s store=%s session failure; recovering session",
                           self.run_id, self.store)
            return self._recover_or_stop()
        # ambiguous
        self.consecutive_ambiguous += 1
        if self.consecutive_ambiguous < AMBIGUOUS_BEFORE_PROBE:
            return "continue"
        self.consecutive_ambiguous = 0
        if probe_route(self.proxy):
            if probe_site(self.store, self.proxy):
                logger.warning(
                    "BREAKER run=%s store=%s repeated timeouts but route and site answer; "
                    "backing off %ss", self.run_id, self.store, SITE_BACKOFF_SECONDS)
                time.sleep(SITE_BACKOFF_SECONDS)
                return "continue"
            logger.warning("BREAKER run=%s store=%s route ok, site not answering; backing off",
                           self.run_id, self.store)
            time.sleep(SITE_BACKOFF_SECONDS)
            return "continue"
        logger.warning("BREAKER run=%s store=%s repeated timeouts and route probe failed; "
                       "treating as infrastructure", self.run_id, self.store)
        return self._recover_or_stop()

    def _recover_or_stop(self) -> str:
        if self.recoveries >= MAX_RECOVERIES:
            logger.warning("BREAKER run=%s store=%s recovery budget exhausted; stopping store",
                           self.run_id, self.store)
            self.halted = True
            return "stop"
        return "recover"

    # -- recovery ---------------------------------------------------------

    def recover(self, adapter) -> bool:
        """Repair route, then session. True means the caller may resume."""
        self.recoveries += 1
        route_ok = False
        try:
            from .exitnode import ensure_israeli_exit

            route_ok = bool(ensure_israeli_exit(self.proxy).available) if self.proxy else False
        except Exception:  # noqa: BLE001
            logger.exception("Route recovery raised")
        session_ok = False
        if route_ok:
            try:
                ensure = getattr(adapter, "ensure_session", None)
                if ensure is not None:
                    session_ok = bool(ensure())
                else:
                    valid = getattr(adapter, "is_session_valid", None)
                    session_ok = bool(valid()) if valid is not None else True
            except Exception:  # noqa: BLE001
                logger.exception("Session recovery raised")
        logger.warning("RECOVERY run=%s store=%s attempt=%d route=%s session=%s",
                       self.run_id, self.store, self.recoveries,
                       "ok" if route_ok else "fail", "ok" if session_ok else "fail")
        if not (route_ok and session_ok):
            self.halted = True
        return route_ok and session_ok
