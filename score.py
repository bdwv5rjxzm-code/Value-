#!/usr/bin/env python3
"""
Buffett-style verdict from dimension scores. Rubric v1.0.

Applies, in order: Price score -> caps -> gates -> floors -> verdict matrix.

The Price score is COMPUTED from price / value-per-share rather than judged, because
the judged column drifted: ADBE, TCOM and APP were recorded at 4, 4 and 5 against
bands that give 3, 3 and 3. Pass --price only when the value came from outside the
model (the G9 financials substitution), and say so in the write-up.

Examples:
  python3 score.py --understand 4 --moat 4 --financials 4 --management 4 \
      --value-per-share 322.15 --price 263.03 --p90 480 --oe-yield 5.1 --rf 4.79

  python3 score.py --understand 2 --moat 3 --financials 4 --management 3 \
      --value-per-share 412.98 --price 323.00

  python3 score.py --understand 4 --moat 4 --financials 4 --management 5 \
      --price 3 --substitution "insurer: price-to-book vs own history"
"""
import argparse
import json
import sys

RUBRIC_VERSION = "1.0"

WEIGHTS = {"understand": 0.15, "moat": 0.35, "financials": 0.30, "management": 0.20}

# price / base value per share -> Price score. Anchored on a 25% margin of safety, so
# price at or below the buy-below price (0.75x value) scores 4 or better.
PRICE_BANDS = [(0.60, 5), (0.75, 4), (1.00, 3), (1.30, 2)]

MATRIX = [
    (4.5, {5: "BUY", 4: "BUY", 3: "BUY_IF_DISCOUNT", 2: "WATCH", 1: "WATCH"}),
    (4.0, {5: "BUY", 4: "BUY", 3: "WATCH", 2: "WATCH", 1: "WATCH"}),
    (3.5, {5: "BUY_CIGAR", 4: "WATCH", 3: "WATCH", 2: "PASS", 1: "PASS"}),
    (3.0, {5: "WATCH", 4: "WATCH", 3: "PASS", 2: "PASS", 1: "PASS"}),
    (0.0, {5: "PASS", 4: "PASS", 3: "PASS", 2: "PASS", 1: "PASS"}),
]

BANDS = [(4.5, "exceptional"), (4.0, "wonderful"), (3.5, "good"),
         (3.0, "fair"), (0.0, "weak")]


def band(q):
    for lo, name in BANDS:
        if q >= lo:
            return name
    return "weak"


def price_score(price, vps, p90=None, oe_yield=None, rf=None, growth=None):
    """
    Returns (score, ratio, notes). Caps only ever move the score DOWN.

    A negative value per share emits no buy-below in the model, so it cannot be
    banded: score 1 and say why.
    """
    notes = []
    if vps is None:
        raise ValueError("need --value-per-share, or --price as an explicit score")
    if vps <= 0:
        return 1, None, ["value per share is not positive: the assumptions do not "
                         "describe a going concern, so no discount exists to score"]
    ratio = price / vps
    score = 1
    for hi, s in PRICE_BANDS:
        if ratio <= hi:
            score = s
            break
    if p90 is not None and price > p90:
        if score > 1:
            notes.append(f"capped at 1: price {price:,.2f} is above the P90 of "
                         f"{p90:,.2f}, so even the optimistic decile does not reach it")
        score = min(score, 1)
    if oe_yield is not None and rf is not None and oe_yield < rf:
        if growth is None or growth < 8:
            if score > 2:
                notes.append(f"capped at 2: owner-earnings yield {oe_yield:.2f}% is "
                             f"below the risk-free {rf:.2f}% with growth under 8% — "
                             "less than a bond, for more risk")
            score = min(score, 2)
    return score, ratio, notes


def verdict(scores, price, gates, ratio):
    # Integer hundredths avoid float noise; ties round DOWN, matching the rubric's
    # "when torn, take the lower score".
    q100 = (15 * scores["understand"] + 35 * scores["moat"]
            + 30 * scores["financials"] + 20 * scores["management"])
    q = (q100 // 10) / 10
    reasons = []

    if scores["understand"] == 1:
        gates = list(gates) + ["G1 outside circle of competence (Understandability 1/5)"]
    if gates:
        reasons.append("Gate failed: " + "; ".join(gates)
                       + ". A gate is a condition not acceptable at any price.")
        return q, "PASS", reasons

    if scores["understand"] <= 2:
        reasons.append(f"Floor: Understandability {scores['understand']}/5. A business "
                       "needing specialist knowledge to judge, or whose product may be "
                       "obsolete within a decade, cannot be forecast ten years out; the "
                       "rest of the card describes the past, not the future.")
        return q, "PASS", reasons
    if scores["moat"] <= 2:
        reasons.append(f"Floor: Moat {scores['moat']}/5. A cheap price does not repair a "
                       "business without pricing power; time erodes the discount rather "
                       "than closing it.")
        return q, "PASS", reasons
    if scores["financials"] <= 2:
        reasons.append(f"Floor: Financials {scores['financials']}/5. The ten-year record "
                       "does not support a forecast.")
        return q, "PASS", reasons
    if scores["management"] == 1:
        reasons.append("Floor: Management 1/5. Accounting or governance concerns make "
                       "the numbers untrustworthy.")
        return q, "PASS", reasons

    for lo, row in MATRIX:
        if q >= lo:
            cell = row[price]
            break

    discount = None if ratio is None else (1 - ratio) * 100

    if cell == "BUY_IF_DISCOUNT":
        if discount is not None and discount >= 15:
            reasons.append(f"Quality {q} (exceptional) at Price 3 with a {discount:.0f}% "
                           "discount: a wonderful business at a fair price. The margin "
                           "of safety is thin, so size accordingly.")
            return q, "BUY", reasons
        shown = "unknown" if discount is None else f"{discount:.0f}%"
        reasons.append(f"Quality {q} (exceptional) but the discount is {shown} (under "
                       "15%). Wonderful business, not yet a wonderful price. Wait for "
                       "the buy-below price.")
        return q, "WATCH", reasons

    if cell == "BUY_CIGAR":
        reasons.append(f"Quality {q} (good, not wonderful) at Price 5: a fair business "
                       "at a wonderful price — the cigar-butt buy. Proceed only with a "
                       "discount of 50% or more and a clean balance sheet.")
        return q, "BUY*", reasons

    name = band(q)
    if cell == "BUY":
        reasons.append(f"Quality {q} ({name}) at Price {price}/5: strong business with a "
                       "real margin of safety.")
    elif cell == "WATCH":
        if q >= 4.0:
            reasons.append(f"Quality {q} ({name}) but Price {price}/5: the business "
                           "qualifies, the price does not. Wait for the buy-below price.")
        else:
            reasons.append(f"Quality {q} ({name}) at Price {price}/5: cheap enough to "
                           "watch, not good enough to buy at anything but a deep "
                           "discount.")
    else:
        reasons.append(f"Quality {q} ({name}) at Price {price}/5: neither the business "
                       "nor the price clears the bar.")
    return q, cell, reasons


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    for k in ("understand", "moat", "financials", "management"):
        p.add_argument(f"--{k}", type=int, required=True, choices=range(1, 6),
                       metavar="1-5")
    p.add_argument("--value-per-share", type=float, default=None,
                   help="base value/share from hybrid_valuation.py")
    p.add_argument("--price", type=float, required=True,
                   help="market price; or the Price score 1-5 directly when "
                        "--value-per-share is omitted (G9 substitution only)")
    p.add_argument("--p90", type=float, default=None,
                   help="P90 of the model's independent-draw range")
    p.add_argument("--oe-yield", type=float, default=None,
                   help="owner-earnings yield in percent")
    p.add_argument("--rf", type=float, default=None,
                   help="risk-free rate in percent: Treasury for USD, Bund for EUR")
    p.add_argument("--growth", type=float, default=None,
                   help="base-case growth in percent, for the bond-yield cap")
    p.add_argument("--gate", action="append", default=[],
                   help="a failed gate, repeatable")
    p.add_argument("--substitution", default=None,
                   help="G9: the model does not apply; names the method used instead")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()

    notes = []
    if a.value_per_share is None:
        if not float(a.price).is_integer() or not 1 <= a.price <= 5:
            p.error("without --value-per-share, --price must be a score 1-5")
        pscore, ratio = int(a.price), None
        notes.append("Price score supplied directly, not computed from the model.")
        if not a.substitution:
            notes.append("[!] No --substitution given. State in the write-up where the "
                         "value came from.")
    else:
        try:
            pscore, ratio, notes = price_score(a.price, a.value_per_share, a.p90,
                                               a.oe_yield, a.rf, a.growth)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(2)

    if a.substitution:
        notes.append(f"G9 substitution: {a.substitution}. hybrid_valuation.py does not "
                     "apply to this structure.")

    scores = {k: getattr(a, k) for k in WEIGHTS}
    q, v, reasons = verdict(scores, pscore, a.gate, ratio)

    out = {"rubric_version": RUBRIC_VERSION, "scores": scores, "price_score": pscore,
           "price_ratio": ratio, "gates": a.gate, "substitution": a.substitution,
           "quality": q, "quality_band": band(q), "verdict": v,
           "reasons": reasons, "notes": notes}
    if a.json:
        print(json.dumps(out, indent=2))
        return

    print(f"VERDICT  (rubric v{RUBRIC_VERSION})")
    print(f"  Quality: {q:.1f}/5 ({band(q)})  =  0.15x{scores['understand']}"
          f" + 0.35x{scores['moat']} + 0.30x{scores['financials']}"
          f" + 0.20x{scores['management']}")
    if ratio is not None:
        buy_below = a.value_per_share * 0.75
        print(f"  Price:   {pscore}/5   price {a.price:,.2f} = {ratio:.2f}x value "
              f"{a.value_per_share:,.2f}   buy-below {buy_below:,.2f}")
    else:
        print(f"  Price:   {pscore}/5   (supplied)")
    print(f"  Verdict: {v}")
    for r in reasons:
        print(f"  Why: {r}")
    for n in notes:
        print(f"  Note: {n}")


if __name__ == "__main__":
    main()
