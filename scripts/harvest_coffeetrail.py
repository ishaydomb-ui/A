"""Harvest coffeetrail.co.il's public coffee-cart directory.

Ishay's request, 2026-09-05: a household reference of Israeli coffee
carts along hiking/driving routes, read-only to Miri, same seam pattern
as the benefits catalog (`grocery_bot.benefits_catalog`).

**No login, no credentials.** Access facts verified by Rob (portfolio-
strategy) 2026-09-05, re-checked here rather than re-discovered: plain
`curl` from this VPS gets HTTP 200 (no geo-block, unlike Shufersal), no
bot wall (Cloudflare + LiteSpeed cache only, not Incapsula/Akamai), and
`robots.txt` disallows only `wp-admin`, `wc-logs`, `woocommerce_uploads`,
`?add-to-cart=` and search pages — `/coffeecart/` is unrestricted.

**Enumeration:** `job_listing-sitemap.xml` lists every cart directly —
405 of them, one `<loc>` each (plus the `/listings/` index itself,
skipped). No blind crawling needed.

**Per-cart data:** each page carries exactly one
`<script type="application/ld+json">…</script>` block with
`"@type": "LocalBusiness"` — parse that, not the HTML. **Measured
2026-09-05 across a diverse initial sample: `openingHours`, `sameAs`,
`logo`, `photo` and a real `contactPoint.telephone` were consistently
EMPTY on every LocalBusiness node checked** — Ishay's field list names
what the schema *can* carry, not what every cart actually populates.
Extracted anyway, generically, in case a future or already-fuller
listing has them; do not assume presence.

`dateModified` isn't on the LocalBusiness node itself, but the same
page's Yoast `@graph` carries a `WebPage` node with one (confirmed
identical to the sitemap's own `<lastmod>` on the one row checked) —
extracted from there, falling back to the sitemap value. Either way,
this is the resumability signal: unchanged `dateModified` since the last
harvest skips the re-fetch.

**Taxonomies** (region 43 · road 25 · foodtype 8 · type 4 · diners 3),
each with its own sitemap of term-archive URLs. **No REST API exposes
per-cart term membership, and no per-cart-page HTML marker distinguishes
"this cart's own region" from the site's own sitewide region mega-menu**
(checked: the same ~30 region links appear on every single cart page —
that is the nav widget, not the assignment). The one path that DOES
work — crawling every term archive page and reading off which
`/coffeecart/<slug>/` links it lists — is **best-effort, not
exhaustive**: the Jerusalem region page's own copy says "35 listings"
while only 29 static links are in the page HTML; the rest load through
an AJAX "load more" this script does not drive. So `terms.json`'s
membership lists are a **floor**, not a claim of completeness — say so
to Miri, don't round it up.

**Politeness** (Ishay's own terms): one request every 1.5s (±0.5s
jitter), strictly sequential, this is a small business's site. Monthly
refresh is the expectation, not continuous polling.

Output: `data/coffeetrail/carts.json` (dict, keyed by slug — a dict, not
append-only JSONL, because a re-run *updates* changed carts in place
rather than appending a new line) and `data/coffeetrail/terms.json`
(taxonomy dictionaries + best-effort membership). Both gitignored (see
.gitignore) as a large regenerable external corpus, not for privacy.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

BASE = Path(__file__).resolve().parent.parent
OUT_DIR = BASE / "data" / "coffeetrail"
CARTS_JSON = OUT_DIR / "carts.json"
TERMS_JSON = OUT_DIR / "terms.json"

SITE = "https://coffeetrail.co.il"
LISTING_SITEMAP = f"{SITE}/job_listing-sitemap.xml"
TAXONOMY_SITEMAPS = {
    "region": f"{SITE}/region-sitemap.xml",
    "road": f"{SITE}/road-sitemap.xml",
    "foodtype": f"{SITE}/foodtype-sitemap.xml",
    "type": f"{SITE}/type-sitemap.xml",
    "diners": f"{SITE}/diners-sitemap.xml",
}

UA = "Mozilla/5.0 (compatible; GroceryBot/1.0; family research; +https://coffeetrail.co.il)"
DELAY_SECONDS = 1.5
JITTER_SECONDS = 0.5

_LOC_RE = re.compile(r"<loc>([^<]+)</loc>")
_LASTMOD_RE = re.compile(r"<lastmod>([^<]+)</lastmod>")
_URL_BLOCK_RE = re.compile(r"<url>(.*?)</url>", re.S)
_JSONLD_RE = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_CART_LINK_RE = re.compile(r"/coffeecart/([a-z0-9-]+)/")
_TITLE_RE = re.compile(r"<title>([^<]+)</title>")


def _sleep():
    time.sleep(DELAY_SECONDS + random.random() * JITTER_SECONDS)


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", text or "")).strip()


def _slug_from_url(url: str) -> str:
    return urlparse(url).path.strip("/").rsplit("/", 1)[-1]


def _fetch(client: httpx.Client, url: str) -> str | None:
    try:
        response = client.get(url)
    except Exception as exc:
        print(f"  request failed for {url}: {str(exc)[:100]}", flush=True)
        return None
    if response.status_code != 200:
        print(f"  HTTP {response.status_code} for {url}", flush=True)
        return None
    return response.text


def list_carts(client: httpx.Client) -> list[dict]:
    """[{'url':..., 'slug':..., 'lastmod':...}] from the job_listing sitemap."""
    xml = _fetch(client, LISTING_SITEMAP)
    if xml is None:
        return []
    carts = []
    for block in _URL_BLOCK_RE.findall(xml):
        loc = _LOC_RE.search(block)
        if not loc:
            continue
        url = loc.group(1).strip()
        if not url.rstrip("/").endswith("/listings"):
            if "/coffeecart/" in url:
                lastmod = _LASTMOD_RE.search(block)
                carts.append({
                    "url": url,
                    "slug": _slug_from_url(url),
                    "lastmod": lastmod.group(1).strip() if lastmod else "",
                })
    return carts


def _extract_local_business(html: str) -> dict | None:
    for block in _JSONLD_RE.findall(html):
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict) and data.get("@type") == "LocalBusiness":
            return data
    return None


def _extract_date_modified(html: str) -> str:
    for block in _JSONLD_RE.findall(html):
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("@graph"), list):
            for node in data["@graph"]:
                if isinstance(node, dict) and node.get("@type") == "WebPage" and node.get("dateModified"):
                    return node["dateModified"]
    return ""


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_text(value) -> str:
    """A field schema.org allows as a plain string but some CMSes emit as
    a list of strings (found live, 2026-09-05: one cart's `logo` was a
    list and crashed a 15-minute run on record ~30 of 405 — the
    politeness delay makes that expensive to repeat). Joins a list,
    passes a string through, empty otherwise."""
    if isinstance(value, list):
        return " ".join(str(v).strip() for v in value if v).strip()
    return (value or "").strip() if isinstance(value, str) else ""


def _as_url(value) -> str:
    """A single image/link field: plain URL string, an ImageObject dict
    ({"url": ...} or {"contentUrl": ...}), or a list of either — schema.org
    permits all three shapes for `logo`. Returns the first URL found."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return _as_text(value.get("url") or value.get("contentUrl") or "")
    if isinstance(value, list):
        for item in value:
            url = _as_url(item)
            if url:
                return url
    return ""


def _as_url_list(value) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    return [url for url in (_as_url(item) for item in items) if url]


def _as_str_list(value) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    return [_as_text(item) for item in items if _as_text(item)]


def parse_cart(html: str, url: str, slug: str, sitemap_lastmod: str) -> dict | None:
    lb = _extract_local_business(html)
    if lb is None:
        return None
    address = lb.get("address") if isinstance(lb.get("address"), dict) else {}
    geo = lb.get("geo") if isinstance(lb.get("geo"), dict) else {}
    contact = lb.get("contactPoint") if isinstance(lb.get("contactPoint"), dict) else {}
    lat = _as_float(geo.get("latitude")) or _as_float(address.get("lat"))
    lng = _as_float(geo.get("longitude")) or _as_float(address.get("lng"))
    return {
        "slug": slug,
        "url": url,
        "name": _as_text(lb.get("name")),
        "legal_name": _as_text(lb.get("legalName")),
        "description": _strip_html(_as_text(lb.get("description"))),
        "address_text": _as_text(address.get("address")),
        "lat": lat,
        "lng": lng,
        "has_map": _as_url(lb.get("hasMap")),
        "phone": _as_text(contact.get("telephone")),
        "opening_hours": _as_str_list(lb.get("openingHours")),
        "same_as": _as_url_list(lb.get("sameAs")),
        "logo": _as_url(lb.get("logo")),
        "photos": _as_url_list(lb.get("photo") or lb.get("photos")),
        "date_modified": _extract_date_modified(html) or sitemap_lastmod,
    }


def harvest_carts(client: httpx.Client, restart: bool) -> dict:
    existing: dict = {}
    if CARTS_JSON.exists() and not restart:
        try:
            existing = json.loads(CARTS_JSON.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}

    print("fetching listing sitemap...", flush=True)
    carts = list_carts(client)
    print(f"{len(carts)} carts in the sitemap", flush=True)
    _sleep()

    fetched, skipped, failed = 0, 0, 0
    for i, entry in enumerate(carts):
        slug, url, lastmod = entry["slug"], entry["url"], entry["lastmod"]
        prior = existing.get(slug)
        if prior and prior.get("date_modified") == lastmod and lastmod:
            skipped += 1
            continue

        html = _fetch(client, url)
        if html is None:
            failed += 1
            _sleep()
            continue
        try:
            row = parse_cart(html, url, slug, lastmod)
        except Exception as exc:
            # One record's unexpected shape must not cost the other 404 —
            # this already happened once (a list-shaped `logo` killed a
            # run at record 30 of 405). Skip and keep going; a re-run
            # retries it since it never got saved.
            print(f"  parse failed for {url}: {str(exc)[:120]}", flush=True)
            failed += 1
            _sleep()
            continue
        if row is None:
            print(f"  no LocalBusiness JSON-LD on {url}", flush=True)
            failed += 1
            _sleep()
            continue
        existing[slug] = row
        fetched += 1

        if fetched % 20 == 0:
            CARTS_JSON.write_text(
                json.dumps(existing, ensure_ascii=False, indent=1, sort_keys=True),
                encoding="utf-8",
            )
            print(f"  [{i+1}/{len(carts)}] {fetched} fetched, {skipped} unchanged, "
                  f"{failed} failed — checkpoint saved", flush=True)
        _sleep()

    CARTS_JSON.write_text(
        json.dumps(existing, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )
    print(f"carts: {fetched} fetched, {skipped} unchanged, {failed} failed, "
          f"{len(existing)} total -> {CARTS_JSON}", flush=True)
    return existing


def harvest_terms(client: httpx.Client) -> dict:
    terms: dict = {}
    for taxonomy, sitemap_url in TAXONOMY_SITEMAPS.items():
        print(f"fetching {taxonomy} term list...", flush=True)
        xml = _fetch(client, sitemap_url)
        _sleep()
        if xml is None:
            continue
        term_urls = [m.group(1).strip() for m in _LOC_RE.finditer(xml)]
        entries = {}
        for term_url in term_urls:
            slug = _slug_from_url(term_url)
            html = _fetch(client, term_url)
            _sleep()
            if html is None:
                entries[slug] = {"name": slug, "carts": [], "note": "fetch failed"}
                continue
            title_match = _TITLE_RE.search(html)
            title = title_match.group(1).strip() if title_match else slug
            cart_slugs = sorted(set(_CART_LINK_RE.findall(html)))
            entries[slug] = {
                "name": title,
                "carts": cart_slugs,
                "note": "best-effort: first server-rendered page only, "
                        "the theme paginates further results via AJAX "
                        "this harvester does not drive — a floor, not exhaustive",
            }
        terms[taxonomy] = entries
        print(f"  {taxonomy}: {len(entries)} terms", flush=True)
        TERMS_JSON.write_text(
            json.dumps(terms, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8",
        )
    return terms


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restart", action="store_true",
                         help="ignore existing carts.json, refetch everything")
    parser.add_argument("--carts-only", action="store_true",
                         help="skip the taxonomy term crawl")
    parser.add_argument("--terms-only", action="store_true",
                         help="skip the cart crawl, only refresh terms.json")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=30, headers={"User-Agent": UA}, follow_redirects=True) as client:
        if not args.terms_only:
            harvest_carts(client, args.restart)
        if not args.carts_only:
            harvest_terms(client)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
