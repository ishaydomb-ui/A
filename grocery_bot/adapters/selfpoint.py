"""Live prices from any Self-Point chain, by barcode, without an account.

Tiv Taam and Victory both run on Self-Point, so one client serves both —
and any Israeli chain later found on the same platform, by adding a row
to :data:`RETAILERS`.

The important discovery
-----------------------
``/v2/retailers/{rid}/branches/{bid}/products`` answers ``Forbidden`` to
every plain query, which reads like a permissions wall and cost a long
detour. It is not one: the endpoint simply requires an Elasticsearch-shaped
``filters`` parameter, and refuses anything else. Given one, it accepts a
*list* of barcodes and returns them all in a single call:

    filters[must][term][localBarcode][0]=7290004131074
    filters[must][term][localBarcode][1]=7290004127329

That makes a whole basket one request per chain, and it needs no login —
which matters, because it turns cross-chain comparison from "what the
household paid in July" into today's shelf price.

Prices live at ``branch.regularPrice``; a product missing that field is
not carried by that branch, which is a real answer and not an error.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

API_BASE = "https://api.self-point.com"
APP_ID = 4

# Elasticsearch will not accept an unbounded terms list, and a very long
# query string gets rejected upstream before it reaches the API.
MAX_BARCODES_PER_CALL = 100


@dataclass(frozen=True)
class Retailer:
    """One chain on Self-Point, pinned to the branch this household uses."""

    key: str
    name: str
    retailer_id: int
    branch_id: int


# Branches are the ones this household actually shops: Tiv Taam's online
# branch, and Victory's nearest store. A price is per branch, not per
# chain, so these are not interchangeable.
RETAILERS = {
    "tivtaam": Retailer("tivtaam", "טיב טעם", 1062, 924),
    "victory": Retailer("victory", "ויקטורי", 1470, 2447),
}


class SelfPointPrices:
    """Read-only price lookup. Deliberately has no account, cart or checkout."""

    def __init__(self, retailer: Retailer | str, proxy: str | None = None, timeout: int = 45):
        self.retailer = (
            RETAILERS[retailer] if isinstance(retailer, str) else retailer
        )
        # Both chains geo-block non-Israeli traffic, and the block arrives
        # as plausible-looking content rather than an error.
        proxy = proxy or os.environ.get("PLAYWRIGHT_PROXY")
        if not proxy:
            raise RuntimeError(
                "PLAYWRIGHT_PROXY is unset — Self-Point chains geo-block this "
                "server and fail in ways that look like bad selectors"
            )
        self._http = httpx.Client(
            proxy=proxy.replace("socks5://", "socks5h://"),
            timeout=timeout,
            headers={"Accept": "application/json"},
        )

    def prices_by_barcode(self, barcodes) -> dict[str, dict]:
        """Current shelf price per barcode at this branch.

        Barcodes the branch does not carry are simply absent from the
        result — the caller is expected to treat that as "not sold here"
        rather than substituting a guess.
        """
        wanted = [str(b) for b in dict.fromkeys(barcodes) if str(b).strip()]
        found: dict[str, dict] = {}
        for start in range(0, len(wanted), MAX_BARCODES_PER_CALL):
            found.update(self._fetch_chunk(wanted[start : start + MAX_BARCODES_PER_CALL]))
        return found

    def _fetch_chunk(self, barcodes: list[str]) -> dict[str, dict]:
        params = {"appId": APP_ID, "from": 0, "size": len(barcodes)}
        for index, barcode in enumerate(barcodes):
            params[f"filters[must][term][localBarcode][{index}]"] = barcode
        url = (
            f"{API_BASE}/v2/retailers/{self.retailer.retailer_id}"
            f"/branches/{self.retailer.branch_id}/products"
        )
        response = self._http.get(url, params=params)
        payload = response.json()
        if isinstance(payload, dict) and payload.get("error"):
            raise RuntimeError(f"{self.retailer.key} products: {payload}")

        results = {}
        for product in payload.get("products", []):
            barcode = str(product.get("localBarcode") or product.get("barcode") or "")
            price = (product.get("branch") or {}).get("regularPrice")
            if not barcode or price is None:
                continue
            results[barcode] = {
                "barcode": barcode,
                "name": _name_of(product),
                "price": float(price),
                "store": self.retailer.key,
            }
        return results


def _name_of(product: dict) -> str:
    """Hebrew short name, falling back through the shapes the API uses."""
    names = product.get("names") or {}
    hebrew = names.get("1") or {}
    return (
        hebrew.get("short")
        or hebrew.get("long")
        or product.get("localName")
        or product.get("name")
        or ""
    )
