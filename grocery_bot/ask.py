"""Answer a one-off price question: "is mustard at ₪20 any good?"

The household's questions do not always arrive inside an order cycle.
Standing in an aisle, or looking at an ad, the question is simply *is this
a good price* — and answering it well needs four different sources that
already exist here but were never pointed at a single question:

1. **Shufersal's public price feed** — the shelf price and any promotion.
2. **Tiv Taam and Victory**, live by barcode, so the answer is not
   Shufersal-only. Both are on Self-Point and answer in one call each.
3. **Price history**, to say whether the current price is normal for this
   product or a genuine dip.
4. **The household's own purchase history**, so "you usually pay ₪12"
   beats any market average.

The verdict is deliberately blunt — good, fair, or poor — because a
hedge helps nobody standing in a shop. Where the evidence is thin, the
answer says so rather than dressing a guess as a finding: with two days of
price history, "cheapest I have seen" would be a lie.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# "חרדל ב-20 שקל", "20 ש\"ח לחרדל", "מוסטרד 20₪" — the price can arrive
# in several shapes, and the product name is whatever is left.
_PRICE_PATTERNS = (
    r"ב\s*[-־]?\s*(\d+(?:\.\d+)?)\s*(?:ש[\"״']?ח|שקל|₪)?",
    r"(\d+(?:\.\d+)?)\s*(?:ש[\"״']?ח|שקלים|שקל|₪)",
    r"₪\s*(\d+(?:\.\d+)?)",
)

# Below this difference the two prices are the same price in practice.
_NOISE = 0.30

# A quote this far under everything found is a matching failure, not a deal.
_MISMATCH_RATIO = 0.6


def parse_question(text: str) -> tuple[str, float | None]:
    """Split "חרדל ב-20 שקל" into ("חרדל", 20.0).

    Returns the price as None when the question carries no number, in
    which case the caller should fall back to a plain price lookup.
    """
    cleaned = (text or "").strip()
    price = None
    for pattern in _PRICE_PATTERNS:
        match = re.search(pattern, cleaned)
        if match:
            price = float(match.group(1))
            cleaned = (cleaned[: match.start()] + " " + cleaned[match.end() :]).strip()
            break
    # Strip the words that frame a question rather than name a product.
    # Punctuation goes first: "טוב?" would otherwise survive a word-boundary
    # match and be searched for as part of the product name.
    cleaned = re.sub(r"[?!,.״\"']+", " ", cleaned)
    for filler in (
        "האם", "זה", "זהו", "טוב", "טובה", "מחיר", "שווה", "כדאי", "יקר",
        "זול", "מבצע", "אמיתי", "באמת", "עולה", "לקנות", "של", "כמה",
    ):
        cleaned = re.sub(rf"(?:^|\s){re.escape(filler)}(?=$|\s)", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(" -־"), price


@dataclass
class PriceVerdict:
    """What the bot knows about one product's price, across every source."""

    query: str
    quoted_price: float | None
    name: str = ""
    barcode: str = ""
    shufersal_price: float | None = None
    promo_price: float | None = None
    promo_text: str = ""
    tivtaam_price: float | None = None
    victory_price: float | None = None
    history_low: float | None = None
    history_high: float | None = None
    usually_paid: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def best_available(self) -> float | None:
        """Cheapest price found anywhere, promotions included."""
        candidates = [
            p
            for p in (
                self.promo_price,
                self.shufersal_price,
                self.tivtaam_price,
                self.victory_price,
            )
            if p
        ]
        return min(candidates) if candidates else None

    @property
    def best_source(self) -> str:
        best = self.best_available
        if best is None:
            return ""
        for price, label in (
            (self.promo_price, "שופרסל במבצע"),
            (self.shufersal_price, "שופרסל"),
            (self.tivtaam_price, "טיב טעם"),
            (self.victory_price, "ויקטורי"),
        ):
            if price and abs(price - best) < 0.01:
                return label
        return ""

    @property
    def verdict(self) -> str:
        """good | fair | poor | mismatch | unknown — never a hedge.

        ``mismatch`` exists because a confident wrong answer is the worst
        outcome here. Asked about milk at ₪7.35 the search returned a 2-litre
        jug at ₪19.60, and calling that "a great price" would be nonsense
        dressed as analysis: a quote far below every price found almost
        always means a different size or product was matched, not a bargain.
        """
        best = self.best_available
        if self.quoted_price is None or best is None:
            return "unknown"
        if self.quoted_price < best * _MISMATCH_RATIO:
            return "mismatch"
        gap = self.quoted_price - best
        if gap <= _NOISE:
            return "good"
        if gap <= best * 0.15:
            return "fair"
        return "poor"

    @property
    def overpay(self) -> float:
        best = self.best_available
        if self.quoted_price is None or best is None:
            return 0.0
        return round(max(0.0, self.quoted_price - best), 2)


def evaluate(
    storage,
    text: str,
    selfpoint_factory=None,
) -> PriceVerdict:
    """Answer a price question from every source available.

    ``selfpoint_factory`` builds a price client for a chain key; it is
    injected so the other chains can be skipped when the Israeli exit is
    down, rather than failing the whole answer.
    """
    query, quoted = parse_question(text)
    verdict = PriceVerdict(query=query, quoted_price=quoted)
    if not query:
        return verdict

    matches = storage.search_with_deals(query, 1)
    if not matches:
        verdict.notes.append("לא מצאתי את המוצר בקטלוג")
        return verdict

    product, promo = matches[0]
    verdict.name = product.name
    verdict.barcode = product.item_code
    verdict.shufersal_price = product.price
    if promo and promo.discounted_price and promo.discounted_price < product.price:
        verdict.promo_price = promo.discounted_price
        verdict.promo_text = promo.description

    low, high = _history_range(storage, product.item_code)
    verdict.history_low, verdict.history_high = low, high

    if selfpoint_factory:
        for key, attr in (("tivtaam", "tivtaam_price"), ("victory", "victory_price")):
            try:
                found = selfpoint_factory(key).prices_by_barcode([product.item_code])
            except Exception:
                # A chain being unreachable must not sink the whole answer;
                # the rest of the sources are still worth reporting.
                verdict.notes.append(f"לא הצלחתי לבדוק ב{key}")
                continue
            row = found.get(product.item_code)
            if row:
                setattr(verdict, attr, row["price"])
    return verdict


def _history_range(storage, item_code: str) -> tuple[float | None, float | None]:
    from contextlib import closing

    with closing(storage._connect()) as conn:  # noqa: SLF001 - storage-internal
        row = conn.execute(
            "SELECT MIN(COALESCE(promo_price, price)) AS lo, MAX(price) AS hi, "
            "COUNT(*) AS days FROM price_history WHERE item_code = ?",
            (str(item_code),),
        ).fetchone()
    if not row or not row["days"]:
        return None, None
    return row["lo"], row["hi"]


def format_verdict(verdict: PriceVerdict, history_days: int = 0) -> str:
    """A short Telegram answer to a price question."""
    from .mdtext import escape

    if not verdict.name:
        return f"לא מצאתי מוצר בשם *{escape(verdict.query)}* בקטלוג."

    head = {
        "good": "✅ *מחיר טוב*",
        "fair": "🟡 *סביר, יש זול יותר*",
        "poor": "❌ *מחיר גבוה*",
        "mismatch": "❓ *כנראה לא אותו מוצר*",
        "unknown": "ℹ️",
    }[verdict.verdict]

    lines = [f"{head} — {escape(verdict.name)}", ""]
    if verdict.quoted_price is not None:
        lines.append(f"שאלת על *₪{verdict.quoted_price:.2f}*")
    for price, label in (
        (verdict.shufersal_price, "שופרסל"),
        (verdict.tivtaam_price, "טיב טעם"),
        (verdict.victory_price, "ויקטורי"),
    ):
        if price:
            lines.append(f"• {label}: ₪{price:.2f}")
    if verdict.promo_price:
        lines.append(f"• 🏷 במבצע: *₪{verdict.promo_price:.2f}* — {escape(verdict.promo_text)}")

    if verdict.verdict == "mismatch":
        lines.append("")
        lines.append(
            "_המחיר ששאלת עליו נמוך בהרבה מכל מה שמצאתי — כנראה מדובר "
            "בגודל או במוצר אחר. תנסו לנסח מדויק יותר (גודל, מותג)._"
        )
    elif verdict.overpay:
        lines.append("")
        lines.append(
            f"_יקר ב-₪{verdict.overpay:.2f} מהזול ביותר שמצאתי ({verdict.best_source})._"
        )
    elif verdict.verdict == "good" and verdict.quoted_price is not None:
        lines.append("")
        lines.append("_זה המחיר הטוב ביותר שמצאתי._")

    # Two days of history cannot support "the cheapest ever seen", and
    # saying so is better than implying a depth of evidence we lack.
    if history_days >= 14 and verdict.history_low:
        lines.append(f"_טווח היסטורי: ₪{verdict.history_low:.2f}–₪{verdict.history_high:.2f}_")
    elif verdict.history_low:
        lines.append("_(היסטוריית מחירים עדיין קצרה מכדי להשוות מגמה)_")

    for note in verdict.notes:
        lines.append(f"_{escape(note)}_")
    return "\n".join(lines)
