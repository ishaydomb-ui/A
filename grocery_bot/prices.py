"""Shufersal public price & promotion feed.

This is the half of the project that needs no Shufersal account and no
login: Israeli law (חוק המזון, 2014) obliges every chain to publish its
full price list and promotions as public XML, and Shufersal does so at
prices.shufersal.co.il. That host is ordinary IIS, not the CloudFront
setup fronting the shop itself, and — verified 2026-08-29 — it is *not*
geo-blocked, so it works from this server while the shop does not.

What the feed gives us that the shop can't:
  - the real current price of an item at a specific branch
  - every running promotion, with its discounted price and minimum qty

so the bot can answer "what does this cost / is there a deal on it"
even while cart automation is blocked.

File naming, as published (verified against the live listing):

    PriceFull7290027600007-001-009-20260828-030000.gz
    └type────┘└chain──────┘ └sub┘ └store└date───┘└time┘

`Price`/`Promo` are hourly deltas; `PriceFull`/`PromoFull` are the
complete snapshots, published a few times a day. We only use the Full
ones — a delta is only meaningful applied on top of a snapshot, and at
this volume re-reading the snapshot is far simpler and costs seconds.

The download links carry a short-lived Azure SAS signature (~30 min), so
they can't be cached or constructed by hand — the listing has to be
re-scraped each refresh. That's why this module always starts from the
listing rather than storing URLs.
"""
from __future__ import annotations

import gzip
import logging
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from xml.etree import ElementTree

logger = logging.getLogger(__name__)

PORTAL_URL = "https://prices.shufersal.co.il/"
CHAIN_ID = "7290027600007"

# The listing pages hold every branch and every file type interleaved, so
# the only reliable filter is the filename itself.
_LINK_RE = re.compile(r'href="(https://pricesprodpublic[^"]+)"')
_NAME_RE = re.compile(
    r"^(?P<type>[A-Za-z]+?)(?P<chain>\d{13})-(?P<sub>\d+)-(?P<store>\d+)"
    r"-(?P<date>\d{8})-(?P<time>\d{6})\.gz$"
)

# The listing runs to ~86 pages. We scan them concurrently; a snapshot is
# only refreshed a few times a day, so this cost is paid rarely.
DEFAULT_MAX_PAGES = 90
_HTTP_TIMEOUT = 60


@dataclass(frozen=True)
class FeedFile:
    name: str
    url: str
    file_type: str  # PriceFull | PromoFull | Price | Promo
    store_id: str  # zero-padded, e.g. "009"
    published_at: datetime


@dataclass(frozen=True)
class PricedProduct:
    item_code: str
    name: str
    manufacturer: str
    price: float
    unit_of_measure_price: float
    unit_of_measure: str
    quantity: str
    is_weighted: bool


@dataclass(frozen=True)
class PromotionItem:
    """One item's share of a promotion.

    A promotion can cover many items (e.g. "3 for 20₪" across a whole
    snack range), so this is flattened per item — that's the shape every
    lookup actually wants.
    """

    promotion_id: str
    description: str
    item_code: str
    discounted_price: float
    min_qty: float
    discount_rate: float
    starts_at: str
    ends_at: str


def _fetch(url: str, timeout: int = _HTTP_TIMEOUT) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "grocery-bot/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _parse_feed_name(name: str, url: str) -> FeedFile | None:
    match = _NAME_RE.match(name)
    if not match:
        return None
    try:
        published_at = datetime.strptime(
            match.group("date") + match.group("time"), "%Y%m%d%H%M%S"
        )
    except ValueError:
        return None
    return FeedFile(
        name=name,
        url=url,
        file_type=match.group("type"),
        store_id=match.group("store"),
        published_at=published_at,
    )


def list_feed_files(max_pages: int = DEFAULT_MAX_PAGES) -> list[FeedFile]:
    """Scrape the whole listing and return every file it advertises.

    The portal's own store/type dropdowns filter client-side via JS, not
    by query string (tested: `?ddlCategory=..&ddlStore=..` changes
    nothing), so filtering has to happen here, on the filename.
    """

    def scrape(page: int) -> list[FeedFile]:
        try:
            html = _fetch(f"{PORTAL_URL}?page={page}", timeout=30).decode("utf-8", "ignore")
        except Exception:
            logger.warning("Price feed: failed to read listing page %d", page, exc_info=True)
            return []
        found = []
        for raw_url in _LINK_RE.findall(html):
            url = unescape(raw_url)
            feed_file = _parse_feed_name(url.split("?")[0].split("/")[-1], url)
            if feed_file is not None:
                found.append(feed_file)
        return found

    files: list[FeedFile] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        for page_files in pool.map(scrape, range(1, max_pages + 1)):
            files.extend(page_files)
    return files


def latest_file(files: list[FeedFile], file_type: str, store_id: str) -> FeedFile | None:
    """Newest file of one type for one branch, or None if the feed has none."""
    padded = store_id.zfill(3)
    candidates = [f for f in files if f.file_type == file_type and f.store_id == padded]
    return max(candidates, key=lambda f: f.published_at) if candidates else None


def _download_xml(url: str) -> str:
    # utf-8-sig: these files are BOM-prefixed, which trips a plain utf-8 parse.
    return gzip.decompress(_fetch(url)).decode("utf-8-sig", "ignore")


def _text(element, tag: str, default: str = "") -> str:
    found = element.find(tag)
    return (found.text or default).strip() if found is not None and found.text else default


def _number(element, tag: str, default: float = 0.0) -> float:
    try:
        return float(_text(element, tag) or default)
    except ValueError:
        return default


def parse_prices(xml: str) -> list[PricedProduct]:
    """Parse a PriceFull document into products.

    Items whose price is missing or zero are dropped: the feed carries
    delisted/placeholder rows, and a 0₪ "price" would silently look like
    the cheapest option in every comparison.
    """
    root = ElementTree.fromstring(xml)
    products = []
    for item in root.iter("Item"):
        code = _text(item, "ItemCode")
        price = _number(item, "ItemPrice")
        if not code or price <= 0:
            continue
        products.append(
            PricedProduct(
                item_code=code,
                name=_text(item, "ItemName"),
                manufacturer=_text(item, "ManufactureName"),
                price=price,
                unit_of_measure_price=_number(item, "UnitOfMeasurePrice"),
                unit_of_measure=_text(item, "UnitOfMeasure"),
                quantity=_text(item, "Quantity"),
                is_weighted=_text(item, "bIsWeighted") == "1",
            )
        )
    return products


def parse_promotions(xml: str) -> list[PromotionItem]:
    """Flatten a PromoFull document to one row per (promotion, item)."""
    root = ElementTree.fromstring(xml)
    rows = []
    for promo in root.iter("Promotion"):
        promotion_id = _text(promo, "PromotionID")
        description = _text(promo, "PromotionDescription")
        starts_at = _text(promo, "PromotionStartDateTime")
        ends_at = _text(promo, "PromotionEndDateTime")
        for item in promo.iter("PromotionItem"):
            code = _text(item, "ItemCode")
            if not code:
                continue
            rows.append(
                PromotionItem(
                    promotion_id=promotion_id,
                    description=description,
                    item_code=code,
                    discounted_price=_number(item, "DiscountedPrice"),
                    min_qty=_number(item, "MinQty", 1.0),
                    discount_rate=_number(item, "DiscountRate"),
                    starts_at=starts_at,
                    ends_at=ends_at,
                )
            )
    return rows


def fetch_branch_snapshot(
    store_id: str, max_pages: int = DEFAULT_MAX_PAGES
) -> tuple[list[PricedProduct], list[PromotionItem], dict[str, str]]:
    """Download and parse the current full price + promo snapshot for a branch.

    Returns (products, promotions, source_info). Promotions failing on
    their own are tolerated — prices alone are still useful, and a
    partial answer beats no answer.
    """
    files = list_feed_files(max_pages)
    if not files:
        raise RuntimeError("Price feed listing returned no files at all.")

    price_file = latest_file(files, "PriceFull", store_id)
    if price_file is None:
        raise RuntimeError(
            f"No PriceFull file published for branch {store_id}. "
            "Check the branch id against the dropdown at prices.shufersal.co.il."
        )
    products = parse_prices(_download_xml(price_file.url))

    promotions: list[PromotionItem] = []
    promo_file = latest_file(files, "PromoFull", store_id)
    if promo_file is not None:
        try:
            promotions = parse_promotions(_download_xml(promo_file.url))
        except Exception:
            logger.exception("Price feed: promotions failed to parse; continuing with prices only")

    source_info = {
        "price_file": price_file.name,
        "price_published_at": price_file.published_at.isoformat(),
        "promo_file": promo_file.name if promo_file else "",
    }
    return products, promotions, source_info
