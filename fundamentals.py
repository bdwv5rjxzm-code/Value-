#!/usr/bin/env python3
"""
Point-in-time fundamentals cache. OBSERVABLES ONLY.

One JSON file per ticker per fiscal year at data/<TICKER>/<FY>.json. Writes are atomic.

Three rules carry the whole design (rationale in RATIONALE.md):

1. A cached fiscal year is an ASSERTION ABOUT THE PAST and is never re-fetched.
   Superseding one requires --refresh, which prints a field-by-field diff first.
2. The cache stores observables only. The validator REFUSES judgment inputs, because a
   file that could carry a growth assumption would quietly become one.
3. `fiscal_year` is a path label. `period_type` and `period_end` are the claim, so a TTM
   figure ending 2026 can sit under a 2025 key and say so. A record without period_end
   is unverified and warns on every load.
"""
import argparse
import json
import os
import sys
import tempfile

# Anything the analyst decides. Present in a record => the record is rejected.
JUDGMENT_FIELDS = {
    "growth", "margin_target", "sales_to_capital", "rd_life", "terminal_roic",
    "terminal_growth", "beta_u", "erp", "crp", "spread", "mature_spread",
    "tax_marginal", "mos", "years_high", "years_fade", "derive_terminal_roic",
}

# Observables the cache may hold, and the hybrid_valuation.py attribute each fills.
OBSERVABLE_FIELDS = [
    "revenue", "margin", "shares", "cash", "debt", "invested_capital", "tax_eff",
    "nol", "price", "rf", "non_op_assets", "minority_interest", "options_val",
    "reported_fcf", "owner_earnings", "rd",
]

# Fields expressed in percent. A value in (0, 1) is almost certainly a fraction that
# should have been multiplied by 100 — 0.141 for 14.1% sits inside every range check
# and yields a plausible valuation. Set units_checked to override deliberately.
PERCENT_FIELDS = {"margin", "tax_eff", "rf"}

META_FIELDS = {"ticker", "fiscal_year", "period_type", "period_end", "currency",
               "units", "units_checked", "source", "note"}


# --------------------------------------------------------------------- storage

def path_for(data_dir, ticker, fy):
    return os.path.join(data_dir, ticker.upper(), f"{int(fy)}.json")


def list_cached(data_dir):
    """Yield (TICKER, fiscal_year, path) for every record on disk."""
    out = []
    if not os.path.isdir(data_dir):
        return out
    for ticker in sorted(os.listdir(data_dir)):
        d = os.path.join(data_dir, ticker)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if name.endswith(".json"):
                try:
                    out.append((ticker.upper(), int(name[:-5]), os.path.join(d, name)))
                except ValueError:
                    continue
    return out


def get(data_dir, ticker, fy):
    """Return (record, path). Raises LookupError if absent, ValueError if unreadable."""
    p = path_for(data_dir, ticker, fy)
    if not os.path.exists(p):
        raise LookupError(f"no cached record at {p}")
    try:
        with open(p) as f:
            rec = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"{p} is not valid JSON: {e}")
    if not rec.get("period_end"):
        print(f"[cache] WARNING: {rec.get('ticker', '?')} FY{rec.get('fiscal_year', '?')} "
              f"has no period_end, so it has never been reconciled against a filing. "
              f"Treat the figures as unverified.", file=sys.stderr)
    return rec, p


def put(data_dir, rec, refresh=False):
    """Write atomically. Refuses to overwrite without refresh=True."""
    p = path_for(data_dir, rec["ticker"], rec["fiscal_year"])
    if os.path.exists(p) and not refresh:
        raise FileExistsError(
            f"{p} already exists. A cached fiscal year is an assertion about the past; "
            f"superseding it needs --refresh, which prints the diff first.")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(rec, f, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return p


def diff(old, new):
    """Field-by-field diff, printed before any --refresh overwrite."""
    rows = []
    for k in sorted(set(old) | set(new)):
        a, b = old.get(k, "—"), new.get(k, "—")
        if a != b:
            rows.append((k, a, b))
    return rows


# ------------------------------------------------------------------ validation

def validate(rec):
    """Return a list of problems. Empty list means the record is usable."""
    problems = []

    for f in ("ticker", "fiscal_year"):
        if not rec.get(f):
            problems.append(f"missing required field: {f}")

    stray = set(rec) & JUDGMENT_FIELDS
    if stray:
        problems.append(
            f"judgment inputs are not cacheable: {', '.join(sorted(stray))}. The cache "
            f"holds observables only; these belong on the command line.")

    unknown = set(rec) - META_FIELDS - set(OBSERVABLE_FIELDS) - JUDGMENT_FIELDS
    if unknown:
        problems.append(f"unrecognised fields: {', '.join(sorted(unknown))}")

    if not rec.get("units_checked"):
        for f in PERCENT_FIELDS:
            v = rec.get(f)
            if isinstance(v, (int, float)) and 0 < v < 1:
                problems.append(
                    f"{f}={v} is in (0, 1). Percent fields are in percent: 14.1 not "
                    f"0.141. A fraction here passes every range check and yields a "
                    f"plausible valuation. Set units_checked true to override.")

    for f in ("revenue", "shares", "invested_capital"):
        v = rec.get(f)
        if v is not None and v <= 0:
            problems.append(f"{f}={v} must be positive")

    for f in ("cash", "debt", "non_op_assets", "minority_interest", "options_val", "nol"):
        v = rec.get(f)
        if v is not None and v < 0:
            problems.append(f"{f}={v} cannot be negative")

    # Catches a share count in absolute terms against revenue in millions, the single
    # most common unit error. A heuristic, not a proof.
    price, shares, revenue = rec.get("price"), rec.get("shares"), rec.get("revenue")
    if all(isinstance(x, (int, float)) for x in (price, shares, revenue)) and revenue > 0:
        ps = price * shares / revenue
        if not 0.01 <= ps <= 500:
            problems.append(
                f"implied price-to-sales {ps:,.2f} is outside 0.01-500. Check that "
                f"shares ({shares:,.6g}) and revenue ({revenue:,.6g}) are in the same "
                f"units, and that price is per share.")

    rd = rec.get("rd")
    if rd is not None:
        if isinstance(rd, str):
            try:
                rd = [float(x) for x in rd.split(",")]
            except ValueError:
                problems.append("rd must be numbers, current year first")
                rd = []
        if isinstance(rd, list) and any(x < 0 for x in rd):
            problems.append("rd values cannot be negative")

    return problems


def apply_to_args(args, rec):
    """
    Fill observable argparse attributes that are still None. Returns the names filled.
    Precedence is explicit flag > cache > default, so anything already set is untouched.
    """
    filled = []
    for f in OBSERVABLE_FIELDS:
        if f not in rec or rec[f] is None:
            continue
        if getattr(args, f, "missing") is None:
            v = rec[f]
            if f == "rd" and isinstance(v, list):
                v = ",".join(str(x) for x in v)
            setattr(args, f, v)
            filled.append(f)
    return filled


# ------------------------------------------------------------------------ CLI

def _record_from_args(k):
    rec = {"ticker": k.ticker.upper(), "fiscal_year": int(k.fiscal_year)}
    for f in ("period_type", "period_end", "currency", "units", "note"):
        if getattr(k, f, None) is not None:
            rec[f] = getattr(k, f)
    if k.units_checked:
        rec["units_checked"] = True
    if k.provider or k.source_note:
        rec["source"] = {"provider": k.provider or "manual",
                         "note": k.source_note or ""}
    for f in OBSERVABLE_FIELDS:
        v = getattr(k, f, None)
        if v is not None:
            rec[f] = [float(x) for x in v.split(",")] if f == "rd" else v
    return rec


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-dir", default="data")
    sub = p.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("put", help="write or refresh a record")
    w.add_argument("ticker")
    w.add_argument("fiscal_year", type=int)
    for f in OBSERVABLE_FIELDS:
        w.add_argument(f"--{f.replace('_', '-')}",
                       type=str if f == "rd" else float, default=None)
    w.add_argument("--period-type", default="FY",
                   help="FY, TTM, Q4 — what the figures actually cover")
    w.add_argument("--period-end", default=None,
                   help="the claim this record makes, e.g. 2026-03-31. A record without "
                        "it is unverified and warns on every load.")
    w.add_argument("--currency", default=None)
    w.add_argument("--units", default=None, help="e.g. 'millions'")
    w.add_argument("--units-checked", action="store_true",
                   help="override the percent-fraction guard, deliberately")
    w.add_argument("--provider", default=None, help="e.g. 'SEC EDGAR 20-F'")
    w.add_argument("--source-note", default=None, help="accession number, filing date")
    w.add_argument("--note", default=None)
    w.add_argument("--refresh", action="store_true",
                   help="supersede an existing record; prints the diff first")

    i = sub.add_parser("init", help="create an empty stub to fill in")
    i.add_argument("ticker")
    i.add_argument("fiscal_year", type=int)

    s = sub.add_parser("show", help="print one record")
    s.add_argument("ticker")
    s.add_argument("fiscal_year", type=int)

    sub.add_parser("list", help="list every cached record")
    sub.add_parser("validate", help="validate every cached record")

    k = p.parse_args()

    if k.cmd == "list":
        rows = list_cached(k.data_dir)
        if not rows:
            print(f"no records under {k.data_dir}/")
            return
        for t, fy, path in rows:
            try:
                with open(path) as f:
                    rec = json.load(f)
            except Exception:
                print(f"  {t:8} FY{fy}   UNREADABLE  {path}")
                continue
            mark = rec.get("period_end") or "UNVERIFIED"
            print(f"  {t:8} FY{fy}   {rec.get('period_type', '?'):4} ending {mark:12}"
                  f"  {rec.get('currency', '?')} {rec.get('units', '?')}")
        return

    if k.cmd == "validate":
        bad = 0
        for t, fy, path in list_cached(k.data_dir):
            with open(path) as f:
                rec = json.load(f)
            problems = validate(rec)
            if problems:
                bad += 1
                print(f"  {t} FY{fy}: INVALID")
                for m in problems:
                    print(f"    - {m}")
            else:
                print(f"  {t} FY{fy}: ok")
        sys.exit(1 if bad else 0)

    if k.cmd == "show":
        try:
            rec, path = get(k.data_dir, k.ticker, k.fiscal_year)
        except (LookupError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(2)
        print(f"# {path}")
        print(json.dumps(rec, indent=1, sort_keys=True))
        return

    if k.cmd == "init":
        stub = {"ticker": k.ticker.upper(), "fiscal_year": int(k.fiscal_year),
                "period_type": "FY", "period_end": None, "currency": None,
                "units": "millions", "source": {"provider": "manual", "note": ""}}
        try:
            print(f"created {put(k.data_dir, stub)}")
        except FileExistsError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(2)
        return

    # put
    rec = _record_from_args(k)
    problems = validate(rec)
    if problems:
        print("error: record is invalid, nothing written:", file=sys.stderr)
        for m in problems:
            print(f"  - {m}", file=sys.stderr)
        sys.exit(2)

    existing = None
    if os.path.exists(path_for(k.data_dir, rec["ticker"], rec["fiscal_year"])):
        with open(path_for(k.data_dir, rec["ticker"], rec["fiscal_year"])) as f:
            existing = json.load(f)

    if existing is not None:
        rows = diff(existing, rec)
        if not k.refresh:
            print(f"error: {rec['ticker']} FY{rec['fiscal_year']} is already cached. "
                  f"A cached fiscal year is an assertion about the past; superseding it "
                  f"needs --refresh.", file=sys.stderr)
            sys.exit(2)
        print(f"# superseding {rec['ticker']} FY{rec['fiscal_year']}")
        if not rows:
            print("  (no field changed)")
        for f, a, b in rows:
            print(f"  {f:20} {a} -> {b}")

    print(f"wrote {put(k.data_dir, rec, refresh=k.refresh)}")


if __name__ == "__main__":
    main()
