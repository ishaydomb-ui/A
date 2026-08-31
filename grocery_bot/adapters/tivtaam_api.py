"""Tiv Taam / Self-Point REST client.

Tiv Taam runs on the Self-Point platform, which — unlike Shufersal — has a
real JSON API (`api.self-point.com`) behind the AngularJS front end. That
makes this adapter far thinner than the Shufersal one: no tile scraping, no
CSRF dance, no waiting on carousels to stop intercepting clicks.

Authentication
--------------
Login is protected by a checkbox reCAPTCHA, so it cannot be performed
headlessly. It is done once by a human through the noVNC desktop
(`scripts/tivtaam_login.py`), which writes a Playwright storage_state.
The session token lives *inside* the ``frontend`` localStorage key, nested
under ``session`` — not as a top-level ``session`` key. Four separate
capture attempts failed on exactly that assumption; do not "simplify" it
back.

The token is sent both as a ``token`` query parameter and as a Bearer
header, because the front end sends both and the API is inconsistent about
which it honours per route.

Boundaries
----------
See CLAUDE.md. This client deliberately exposes no payment surface: card
tokens present in order payloads are stripped by :func:`strip_payment` on
the way in, and there is no method here that can reach a checkout step.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

API_BASE = "https://api.self-point.com"
RETAILER_ID = 1062
BRANCH_ID = 924
APP_ID = 4

DEFAULT_STATE_PATH = Path("data/sessions/tivtaam_storage_state.json")

# Fields in an order payload that carry card data. Stripped before anything
# reaches the database or a log line — the account holds real money and the
# household's card is not ours to hold.
_PAYMENT_SECRET_FIELDS = ("paymentToken", "cardToken", "creditCard", "sessionCD")


class TivTaamAuthError(RuntimeError):
    """The stored session is missing, malformed, or no longer accepted."""


@dataclass(frozen=True)
class TivTaamSession:
    username: str
    user_id: int
    token: str
    loyalty_club_card_id: str | None = None

    @classmethod
    def from_storage_state(cls, path: str | Path | None = None) -> "TivTaamSession":
        path = Path(path or DEFAULT_STATE_PATH)
        if not path.exists():
            raise TivTaamAuthError(
                f"no Tiv Taam session at {path} — run scripts/tivtaam_login.py"
            )
        state = json.loads(path.read_text())
        for origin in state.get("origins", []):
            for item in origin.get("localStorage", []):
                if item.get("name") != "frontend":
                    continue
                session = (json.loads(item["value"]) or {}).get("session") or {}
                if session.get("token"):
                    return cls(
                        username=session.get("username", ""),
                        user_id=int(session["userId"]),
                        token=session["token"],
                        loyalty_club_card_id=session.get("loyaltyClubCardId"),
                    )
        raise TivTaamAuthError(
            f"{path} has no frontend.session.token — the capture ran but the "
            "user was never logged in"
        )


def strip_payment(payload: Any) -> Any:
    """Recursively remove card/payment-token fields from an API payload.

    The *payment method* (cash, card, club credit) is business-relevant and
    is kept; the card token that identifies the household's actual card is
    not, and never touches disk.
    """
    if isinstance(payload, dict):
        return {
            k: strip_payment(v)
            for k, v in payload.items()
            if k not in _PAYMENT_SECRET_FIELDS
        }
    if isinstance(payload, list):
        return [strip_payment(v) for v in payload]
    return payload


class TivTaamApi:
    """Read-oriented client for one logged-in Tiv Taam household account."""

    def __init__(
        self,
        session: TivTaamSession | None = None,
        proxy: str | None = None,
        timeout: int = 30,
    ):
        self.session = session or TivTaamSession.from_storage_state()
        # Tiv Taam geo-blocks non-Israeli traffic the same way Shufersal
        # does, so the Israeli exit is mandatory here too.
        self.proxy = proxy or os.environ.get("PLAYWRIGHT_PROXY")
        if not self.proxy:
            raise RuntimeError(
                "PLAYWRIGHT_PROXY is unset — Tiv Taam geo-blocks this server "
                "and returns plausible-looking failures rather than an error"
            )
        self.timeout = timeout
        # socks5h:// so DNS resolves at the Israeli exit, not here.
        socks = self.proxy.replace("socks5://", "socks5h://")
        self._http = httpx.Client(
            proxy=socks,
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.session.token}",
            },
        )

    # -- plumbing ---------------------------------------------------------

    def get(self, path: str, **params) -> Any:
        url = f"{API_BASE}/v2/retailers/{RETAILER_ID}/{path.lstrip('/')}"
        params = {"appId": APP_ID, "token": self.session.token, **params}
        response = self._http.get(url, params=params, timeout=self.timeout)
        # The API answers 200 with an error body as often as it uses a status
        # code, so shape is checked rather than trusted.
        try:
            payload = response.json()
        except ValueError:
            raise TivTaamAuthError(
                f"{path} returned non-JSON ({response.status_code}) — this is "
                "what a geo-block or an expired session looks like here"
            )
        if isinstance(payload, dict) and payload.get("error"):
            if str(payload.get("errorCode")) in ("ST63", "ST59"):
                raise TivTaamAuthError(f"{path}: {payload['error']}")
            raise RuntimeError(f"{path}: {payload}")
        return payload

    # -- reads ------------------------------------------------------------

    def orders(self, size: int = 50, start: int = 0) -> dict:
        """Order history, newest first, with card data removed."""
        payload = self.get(
            f"users/{self.session.user_id}/orders",
            **{
                "from": start,
                "size": size,
                "getLiveResults": "false",
                "orderBy[0][id]": "desc",
            },
        )
        return strip_payment(payload)

    def order(self, order_id: int) -> dict:
        """One order in full, including its lines."""
        payload = self.get(
            f"branches/{BRANCH_ID}/users/{self.session.user_id}/orders/{order_id}"
        )
        return strip_payment(payload)

    def coupons(self) -> list:
        """Coupons currently offered to this household."""
        return self.get(f"branches/{BRANCH_ID}/users/{self.session.user_id}/coupons")

    def is_alive(self) -> bool:
        """Cheap check that the stored session is still accepted."""
        try:
            self.orders(size=1)
            return True
        except TivTaamAuthError:
            return False
