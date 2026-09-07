"""Resolve the household's terms against a chain's own published feed.

Built 2026-09-07, after a real order went wrong. Tiv Taam's adapter
resolves a product by typing into the site's autocomplete and reading
the dropdown; anything that returns more than one row is handed back to
the household as a question. On that order **39 of ~46 Tiv Taam items
came back ambiguous**, the questions were never asked (a separate crash
ate them), and the cart ended with 7 items while Shufersal's had most of
the list. Even had the questions arrived, 39 of them is not a workflow.

The dropdown was never the right source. Since 2026-09-06 this project
holds Tiv Taam's own published catalogue — 21,450 products, each with
the manufacturer's barcode — so a term can be resolved *here*, once,
deterministically, and the browser asked only to add a named product.
That is the "step 2" the handover has described as the real fix since
2026-09-02.

**Precision over coverage, deliberately.** What this writes is product
memory: a durable decision that spends money every week without asking
again. So only a confident match is seeded (rank 0 — the product's name
actually starts with the term). The looser rank-1 "substitute" match
that `basketview` shows the household for comparison is *not* good
enough to buy on: the same fallback paired "אצבעות גבינה צהובה" with
"אצבעות שוקולד קרם חלב". A term left unseeded simply goes through the
old path; a term seeded wrongly buys the wrong thing quietly, forever.

**Price-controlled products win ties.** Ishay, 2026-09-07: where a
comparable alternative exists he wants the supervised one. The chains
mark it in the product name itself ("חלב 1% קרטון - בפיקוח"), so a
candidate carrying that marker is preferred over an equally-ranked one
that does not — before price is considered at all, because the point is
the regulated staple rather than this week's cheapest thing.
"""
from __future__ import annotations

from dataclasses import dataclass

# How the chains write "this is a price-controlled product" into a name.
# Taken from real rows in both feeds, not invented.
_CONTROLLED_MARKERS = ("בפיקוח", "פיקוח ממשלתי", "מחיר מפוקח")

# Words that may follow a term without changing what the product is:
# packaging and measurement, nothing else. Anything not on this list is
# treated as a new noun and the match is refused.
_UNIT_WORDS = {
    "גרם", "גר", "ג", "קג", 'ק"ג', "קילו", "ליטר", "ל", "מל", 'מ"ל',
    "יח", "יחידות", "יחי", "אריזה", "ארוז", "מארז", "חבילה", "×", "x",
}


def is_price_controlled(name: str) -> bool:
    return any(marker in (name or "") for marker in _CONTROLLED_MARKERS)


@dataclass(frozen=True)
class Resolution:
    """One household term, resolved to one real product at one chain."""

    term: str
    barcode: str
    name: str
    price: float
    controlled: bool


def _qualifier_only(remainder: str) -> bool:
    """Is what follows the term just size/pack noise, not a new product?

    "דנונה דל לקטוז" + "200 גרם" is the same yoghurt in a stated size.
    "בננה" + "ציפס 200 גרם ששון הקולה" is a bag of crisps. Both end in a
    number, so a digit test cannot separate them; what separates them is
    a content word sitting *directly* after the term.
    """
    # A separator carries no meaning: "חלב 1% קרטון - בפיקוח" is the same
    # carton as "חלב 1% קרטון". Without this the price-controlled marker
    # itself read as a foreign noun and every controlled staple — the
    # exact thing Ishay asked to prefer — was refused.
    words = [w for w in remainder.split() if w not in {"-", "–", "|", ","}]
    if not words:
        return True
    first = words[0]
    return (
        first.isdigit()
        or any(ch.isdigit() for ch in first)
        or first in _UNIT_WORDS
        or is_price_controlled(remainder)
    )


def resolve_term(storage, store: str, term: str) -> Resolution | None:
    """The one product at `store` this term means, or None if unsure.

    Refuses far more often than it accepts, and that is the design. Two
    live errors from the first version, on the household's own terms:
    "בננה" resolved to בננה ציפס (a bag of crisps — the Tiv Taam feed
    carries no fresh bananas at all, so *every* candidate was wrong) and
    "מלפפון" to מלפפון במלח (pickles) because it sorted cheapest-first
    and pickles undercut cucumbers. A term left unresolved costs one
    question, asked once and remembered; a term resolved wrongly is
    bought quietly every week.
    """
    folded = (term or "").strip()
    if not folded:
        return None

    rows = storage.feed_candidates(store, folded)
    if not rows:
        return None

    safe = []
    for row in rows:
        name = row["name"].strip()
        if name == folded:
            safe.append((0, row))
            continue
        if name.startswith(folded) and _qualifier_only(name[len(folded):].strip()):
            safe.append((1, row))
    if not safe:
        return None

    # Exact name first, then the controlled staple, then the shortest
    # name — never the cheapest, which is what chose the pickles.
    safe.sort(key=lambda pair: (
        pair[0],
        not is_price_controlled(pair[1]["name"]),
        len(pair[1]["name"]),
    ))
    best = safe[0][1]
    return Resolution(
        term=folded,
        barcode=best["barcode"],
        name=best["name"],
        price=best["price"],
        controlled=is_price_controlled(best["name"]),
    )


def seed_memory(storage, store: str, terms, dry_run: bool = False) -> dict:
    """Remember one product per term, for every term we can be sure of.

    Returns a report rather than printing: the caller decides whether
    this is a CLI line, a Telegram message or a test assertion.
    """
    seeded, controlled, unresolved = [], [], []
    for term in dict.fromkeys(t for t in terms if (t or "").strip()):
        hit = resolve_term(storage, store, term)
        if hit is None:
            unresolved.append(term)
            continue
        if not dry_run:
            storage.remember_choice(
                store=store,
                term=term,
                product_code=hit.barcode,
                product_name=hit.name,
            )
        seeded.append(hit)
        if hit.controlled:
            controlled.append(hit)
    return {
        "store": store,
        "seeded": seeded,
        "controlled": controlled,
        "unresolved": unresolved,
    }


def format_seed_report(report: dict, limit: int = 12) -> str:
    seeded, unresolved = report["seeded"], report["unresolved"]
    lines = [
        f"{report['store']}: resolved {len(seeded)}, "
        f"unsure about {len(unresolved)}"
    ]
    if report["controlled"]:
        lines.append(f"  price-controlled picks: {len(report['controlled'])}")
        for hit in report["controlled"][:limit]:
            lines.append(f"    ✓ {hit.term} → {hit.name} ({hit.price:.2f}₪)")
    if unresolved:
        lines.append(f"  left to the old path: {', '.join(unresolved[:limit])}")
    return "\n".join(lines)
