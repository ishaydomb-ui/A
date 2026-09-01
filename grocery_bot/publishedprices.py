"""Read any Israeli chain's price feed from the public transparency portal.

Israeli law requires every chain to publish full price and promotion
files. Several do it through one shared Cerberus portal at
`url.publishedprices.co.il`, where each chain is a username with an empty
password — public data behind a formality, not a credential.

Two things cost an hour each and are worth writing down:

**The CSRF token is in a `<meta>` tag, not the form.** Posting the login
without it returns HTTP 200 and the login page again, which reads exactly
like wrong credentials. The username is fine; the token is missing.

**The file listing is a DataTables JSON endpoint**, `/file/json/dir`, not
the HTML page. Scraping the page yields nothing because the rows are
fetched separately.

What makes this cheap: the published format is mandated, so the *same*
parser that reads Shufersal reads Rami Levy unchanged — verified, 3,668
products — and every file keys products by EAN barcode, which is already
how cross-chain comparison works here.
"""
from __future__ import annotations

import gzip
import re
from dataclasses import dataclass
from datetime import date, datetime

import httpx

PORTAL = "https://url.publishedprices.co.il"

# Chains confirmed reachable with an empty password on 2026-09-01.
# Yeinot Bitan is deliberately absent: seven candidate usernames were
# tried and none worked, and it has since become Carrefour, which appears
# to publish elsewhere. Better an honest gap than a chain that silently
# never updates.
PORTAL_CHAINS = {
    "ramilevy": "RamiLevi",
    "yohananof": "yohananof",
    "osherad": "osherad",
    "keshet": "Keshet",
    "politzer": "politzer",
    "freshmarket": "freshmarket",
}

_CSRF = re.compile(r'name="csrftoken" content="([^"]+)"')

# A feed can be present, parse perfectly, and still be long dead: on
# 2026-09-01 Yohananof's newest full snapshot was from December 2024.
# Comparing against twenty-month-old prices is worse than not comparing,
# because it is confidently wrong rather than visibly missing.
MAX_FEED_AGE_DAYS = 7


@dataclass(frozen=True)
class PortalFile:
    name: str
    size: int
    modified: str

    @property
    def kind(self) -> str:
        for prefix in ("PriceFull", "PromoFull", "Price", "Promo", "StoresFull"):
            if self.name.startswith(prefix):
                return prefix
        return "other"

    @property
    def published_on(self) -> date | None:
        """The date inside the filename, which is the only honest one.

        The portal's modification time changes when a file is re-uploaded
        unchanged, so it can make a two-year-old snapshot look like
        today's.
        """
        found = re.search(r"(20\d{6})", self.name)
        if not found:
            return None
        try:
            return datetime.strptime(found.group(1), "%Y%m%d").date()
        except ValueError:
            return None

    def age_days(self, today: date | None = None) -> int | None:
        published = self.published_on
        if published is None:
            return None
        return ((today or date.today()) - published).days

    @property
    def branch_id(self) -> str:
        """The store id embedded in the filename, e.g. ...-001-737-..."""
        parts = self.name.split("-")
        return parts[2] if len(parts) > 2 else ""


class PublishedPrices:
    """A logged-in session against one chain's folder on the portal."""

    def __init__(self, chain: str, proxy: str | None = None, timeout: int = 120):
        if chain not in PORTAL_CHAINS:
            raise KeyError(f"unknown portal chain {chain!r}; known: {sorted(PORTAL_CHAINS)}")
        self.chain = chain
        # The portal is served from Israel and is not geo-blocked, but the
        # proxy is used when given so all store traffic shares one exit.
        client_args = {"timeout": timeout, "follow_redirects": True}
        if proxy:
            client_args["proxy"] = proxy.replace("socks5://", "socks5h://")
        self._http = httpx.Client(**client_args)
        self._csrf = ""
        self._login()

    def _token(self, html: str) -> str:
        found = _CSRF.search(html)
        return found.group(1) if found else self._csrf

    def _login(self) -> None:
        page = self._http.get(f"{PORTAL}/login")
        self._csrf = self._token(page.text)
        response = self._http.post(
            f"{PORTAL}/login/user",
            data={
                "r": "",
                "username": PORTAL_CHAINS[self.chain],
                "password": "",
                "csrftoken": self._csrf,
            },
        )
        # The portal answers 200 with the login page again on failure, so
        # the status code proves nothing; the absence of the sign-in form
        # is the only reliable signal.
        if "Sign In" in response.text:
            raise RuntimeError(
                f"{self.chain}: portal login rejected — the username may have "
                "changed, or the chain may have moved off this portal"
            )
        listing = self._http.get(f"{PORTAL}/file")
        self._csrf = self._token(listing.text)

    def files(self, limit: int = 2000) -> list[PortalFile]:
        """Everything currently published for this chain, newest last."""
        response = self._http.post(
            f"{PORTAL}/file/json/dir",
            data={
                "sEcho": "1",
                "iDisplayStart": "0",
                "iDisplayLength": str(limit),
                "cd": "/",
                "csrftoken": self._csrf,
            },
        )
        rows = response.json().get("aaData", [])
        return [
            PortalFile(
                name=row.get("name", ""),
                size=int(row.get("size") or 0),
                modified=row.get("ftime", ""),
            )
            for row in rows
            if row.get("name")
        ]

    def latest(self, kind: str = "PriceFull", branch_id: str = "") -> PortalFile | None:
        """The newest file of a kind, optionally for one branch.

        Filenames carry a timestamp, so sorting by name is sorting by
        time — and is not fooled by a modification date that changes when
        a file is re-uploaded unchanged.
        """
        candidates = [f for f in self.files() if f.kind == kind]
        if branch_id:
            candidates = [f for f in candidates if f.branch_id == branch_id]
        return max(candidates, key=lambda f: f.name) if candidates else None

    def download_xml(self, file: PortalFile) -> str:
        """Fetch and un-gzip one feed file."""
        response = self._http.get(f"{PORTAL}/file/d/{file.name}")
        response.raise_for_status()
        raw = response.content
        if file.name.endswith(".gz"):
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", errors="replace")
