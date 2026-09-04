"""Face value -> card charge, for every behatsdaa wallet rate.

A behatsdaa wallet is loaded at face value and the card is charged
`face x (1 - rate)`, so a charge can be worked backwards to a wallet
without any logged-in session. The budget project uses this to file each
load under the right category instead of calling all of them groceries.

Emits two things, because the table alone is a trap:

1. `behatsdaa_charge_decode.csv` — face x rate -> charge, for a realistic
   grid of load sizes plus the ones actually observed.
2. A collision report. Different (face, wallet) pairs can produce the
   *same* charge, and a decoder that assumes uniqueness will map real
   money to the wrong category with no sign that it did.

Rates come from `catalog_tagged.csv` (see docs/BENEFITS.md). The ceilings
there are maxBalance, NOT a monthly deposit cap — this script deliberately
does not model a monthly limit, because we do not have one.
"""
from __future__ import annotations

import argparse
import collections
import csv
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "benefits" / "behatsdaa_charge_decode.csv"

# wallet -> discount rate. Two wallets genuinely share 15%.
RATES = {
    "מזון+אונליין": 0.07,
    "קרפור": 0.10,
    "רשתות בהצדעה": 0.15,
    "פייטר": 0.15,
    "מסעדות": 0.20,
    "מבצע הוקרה": 0.25,
    "ראש השנה": 0.30,
}

# Load sizes seen in the household's real activity, 2026.
OBSERVED = [105, 130, 200, 260, 300, 340, 370, 390, 430, 500, 700, 709, 800, 950, 1000]


def face_values(step: int, top: int) -> list[int]:
    grid = set(range(step, top + 1, step))
    grid.update(OBSERVED)
    return sorted(grid)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", type=int, default=10)
    parser.add_argument("--top", type=int, default=3000)
    args = parser.parse_args()

    faces = face_values(args.step, args.top)
    OUT.parent.mkdir(parents=True, exist_ok=True)

    charge_to_pairs: dict[str, list[tuple[int, str]]] = collections.defaultdict(list)
    with OUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["face_value", "wallet", "rate_pct", "card_charge", "observed_load"])
        for face in faces:
            for wallet, rate in RATES.items():
                charge = round(face * (1 - rate), 2)
                writer.writerow([face, wallet, int(rate * 100), f"{charge:.2f}",
                                 "yes" if face in OBSERVED else ""])
                charge_to_pairs[f"{charge:.2f}"].append((face, wallet))

    print(f"wrote {OUT} ({len(faces)} face values x {len(RATES)} wallets)")

    # --- collisions -------------------------------------------------
    same_rate, cross_rate = 0, []
    for charge, pairs in charge_to_pairs.items():
        if len(pairs) < 2:
            continue
        rates_involved = {RATES[w] for _, w in pairs}
        if len(rates_involved) == 1:
            same_rate += 1          # the 15%/15% pair — inherent, not resolvable
        else:
            cross_rate.append((charge, pairs))

    print(f"\ncharges hit by the two 15% wallets only: {same_rate} "
          f"(inherent — same rate, cannot be separated by amount)")
    print(f"charges reachable from DIFFERENT rates: {len(cross_rate)}")
    print("\nexamples where a decoder would have to guess:")
    for charge, pairs in sorted(cross_rate, key=lambda kv: float(kv[0]))[:12]:
        shown = ", ".join(f"₪{f}@{int(RATES[w]*100)}%" for f, w in pairs)
        print(f"  charge ₪{charge:>9}  <-  {shown}")

    observed_ambiguous = [
        (c, p) for c, p in cross_rate
        if any(f in OBSERVED for f, _ in p)
    ]
    print(f"\ncollisions involving an actually-observed load size: {len(observed_ambiguous)}")
    for charge, pairs in sorted(observed_ambiguous, key=lambda kv: float(kv[0]))[:10]:
        shown = ", ".join(
            f"₪{f}@{int(RATES[w]*100)}%" + ("*" if f in OBSERVED else "")
            for f, w in pairs
        )
        print(f"  charge ₪{charge:>9}  <-  {shown}   (* = a load size we have seen)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
