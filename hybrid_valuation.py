#!/usr/bin/env python3
"""
HYBRID INTRINSIC VALUATION — Damodaran mechanics, Buffett discipline.

Bottom-up converging cost of capital, growth charged via sales-to-capital, invested
capital tracked so terminal ROIC can be derived, R&D capitalised, full equity bridge.
Buffett layer on top: owner-earnings yield as an independent check, terminal-share and
FCF cross-checks, margin of safety on the output, and a judgment budget because in
backtesting a mechanical rule beat discretion.

Rationale for every design decision is in HANDBOOK.md. Do not reverse one without
reading it. All monetary inputs in the same units; rates in percent.
"""
import argparse
import json
import sys
import textwrap
from copy import deepcopy

try:
    import fundamentals                     # optional: only --from-cache needs it
except ImportError:                         # keeps the model runnable standalone, so
    fundamentals = None                     # the cache layer can be dropped from context

OBSERVABLE = ["revenue", "margin", "shares", "cash", "debt", "invested_capital",
              "price", "rf", "non_op_assets", "minority_interest", "options_val"]
# Observables with no sensible default: required unless --from-cache supplies them.
REQUIRED_OBSERVABLE = ["--revenue", "--margin", "--shares", "--invested-capital",
                       "--tax-eff", "--price"]
# Observables that default to zero (or to a standard rate) when neither the command
# line nor the cache supplies them.
OBSERVABLE_DEFAULTS = [("--cash", 0.0), ("--debt", 0.0), ("--nol", 0.0),
                       ("--non-op-assets", 0.0), ("--minority-interest", 0.0),
                       ("--options-val", 0.0), ("--rf", 4.79), ("--reported-fcf", 0.0),
                       ("--owner-earnings", 0.0)]
JUDGMENT = {  # input: (pessimistic, optimistic) as multiplicative or absolute bounds
    "growth": ("mult", 0.6, 1.4),
    "margin_target": ("mult", 0.8, 1.2),
    "sales_to_capital": ("mult", 0.7, 1.5),
    "terminal_growth": ("abs", -1.0, 0.0),   # cap at rf handled downstream
    "terminal_roic": ("mult", 0.75, 1.3),
    "beta_u": ("mult", 1.25, 0.8),           # higher beta = pessimistic
    "erp": ("abs", 1.0, -0.75),
    "spread": ("abs", 1.5, -0.75),
}

# STRESS is what gets perturbed; JUDGMENT is what cannot be cached. They differ because
# `rf` is a cached market observable (point-in-time, for backtests) yet is the second-
# largest lever in the model: 4.79% -> 3.0% moves value +38% to +66%. Same for
# mature_spread, which prices the rate discounting 55-85% of firm value. Nothing here is
# read back into a valuation; it only perturbs one.
STRESS = dict(JUDGMENT)
STRESS["rf"] = ("abs", 1.0, -1.0)             # +/-100bp on the risk-free rate
STRESS["mature_spread"] = ("abs", 0.75, -0.5)  # terminal credit spread


def capitalize_rd(rd, life):
    """
    Damodaran R&D capitalisation. rd[0] is the current year, rd[1] the prior year, etc.

    Research asset  = sum of the UNAMORTISED portion of each year's spend.
                      Current year is 100% unamortised, year -1 is (life-1)/life, etc.
    Amortisation    = the slice of each prior year's spend expensed this year.

    Adjusted EBIT   = stated EBIT + current-year R&D - amortisation
    Adjusted capital= stated invested capital + research asset

    Consistency rule: if R&D is added back to earnings it must also be treated as
    reinvestment, or value is created out of nothing. Here that happens through the
    enlarged capital base, which lowers sales-to-capital and raises the cost of growth.

    DIFFERENT DATA REQUIREMENTS: the asset reads rd[0..life-1] and is COMPLETE at `life`
    years; amortisation reads rd[1..life] and needs `life + 1`. So `life` years gives a
    correct asset and an amortisation short by one year, which OVERSTATES the EBIT
    uplift and biases value UP (5 years on a 5-year life: uplift ~55% too high).
    """
    if life < 1:
        raise ValueError("R&D amortisable life must be at least 1 year.")
    n = len(rd)
    asset = sum(rd[i] * (life - i) / life for i in range(min(life, n)))
    amort = sum(rd[i] / life for i in range(1, min(life + 1, n)))
    asset_years_used = min(life, n)                    # of `life` needed
    amort_years_used = max(0, min(life + 1, n) - 1)    # of `life` needed
    asset_complete = asset_years_used >= life
    amort_complete = amort_years_used >= life
    status = ("complete" if amort_complete
              else "amortisation_short" if asset_complete
              else "insufficient_data")
    return dict(research_asset=asset, amortisation=amort, current_rd=rd[0],
                ebit_uplift=rd[0] - amort, years_supplied=n, status=status,
                asset_years_used=asset_years_used, asset_years_needed=life,
                amort_years_used=amort_years_used, amort_years_needed=life,
                asset_complete=asset_complete, amort_complete=amort_complete,
                years_used=min(life + 1, n), complete=amort_complete)


def cost_of_capital(rf, erp, beta, debt, equity, tax, spread, crp=0.0, debt_ratio=None):
    """
    Market-equity weights make value a function of the price being judged, backwards:
    ORCL price $40 -> value $81, $640 -> $67. Pass debt_ratio for a target structure, or
    use value_converged() to weight on the model's own equity value instead.
    """
    if debt_ratio is not None:
        if not 0.0 <= debt_ratio < 1.0:
            raise ValueError(f"Debt ratio {debt_ratio} must be in [0, 1).")
        we, wd = 1.0 - debt_ratio, debt_ratio
        dm = wd / we
        beta_l = beta * (1 + (1 - tax) * dm)
        ke = rf + beta_l * (erp + crp)
        kd = (rf + spread) * (1 - tax)
        return dict(beta_levered=beta_l, cost_of_equity=ke, cost_of_debt=kd,
                    w_equity=we, w_debt=wd, wacc=we * ke + wd * kd)
    total = equity + debt
    if total <= 0:
        raise ValueError("Equity + debt must be positive.")
    dm = debt / equity if equity > 0 else 0.0
    beta_l = beta * (1 + (1 - tax) * dm)
    ke = rf + beta_l * (erp + crp)
    kd = (rf + spread) * (1 - tax)
    we, wd = equity / total, debt / total
    return dict(beta_levered=beta_l, cost_of_equity=ke, cost_of_debt=kd,
                w_equity=we, w_debt=wd, wacc=we * ke + wd * kd)


def value(p):
    """p is a dict of already-converted (decimal) parameters."""
    n = p["years_high"] + p["years_fade"]
    g_term = min(p["terminal_growth"], p["rf"])          # hard cap, Damodaran rule
    if p["shares"] <= 0:
        raise ValueError("Share count must be positive: value per share is undefined "
                         "with zero shares outstanding.")
    if p["sales_to_capital"] <= 0:
        raise ValueError("Sales-to-capital must be positive.")
    equity_mkt = p["price"] * p["shares"]
    dr = p.get("debt_ratio")

    c0 = cost_of_capital(p["rf"], p["erp"], p["beta_u"], p["debt"], equity_mkt,
                         p["tax_marginal"], p["spread"], p["crp"], debt_ratio=dr)
    cT = cost_of_capital(p["rf"], p["erp"], 1.0, p["debt"], equity_mkt,
                         p["tax_marginal"], p["mature_spread"], p["crp"], debt_ratio=dr)
    if cT["wacc"] <= g_term:
        raise ValueError(f"Mature WACC {cT['wacc']:.2%} <= terminal growth {g_term:.2%}.")

    rows, rev, ic, nol, df, pv_sum = [], p["revenue"], p["invested_capital"], p["nol"], 1.0, 0.0
    for t in range(1, n + 1):
        if t <= p["years_high"]:
            g, margin, tax = p["growth"], p["margin"], p["tax_eff"]
            wacc = c0["wacc"]
        else:
            s = (t - p["years_high"]) / p["years_fade"]
            g = p["growth"] + (g_term - p["growth"]) * s
            margin = p["margin"] + (p["margin_target"] - p["margin"]) * s
            tax = p["tax_eff"] + (p["tax_marginal"] - p["tax_eff"]) * s
            wacc = c0["wacc"] + (cT["wacc"] - c0["wacc"]) * s     # convergence
        rev_new = rev * (1 + g)
        ebit = rev_new * margin
        taxable = max(ebit - nol, 0.0)
        nol = max(nol - ebit, 0.0)
        ebit_at = ebit - taxable * tax
        reinvest = (rev_new - rev) / p["sales_to_capital"]
        fcff = ebit_at - reinvest
        df *= (1 + wacc)                                          # compounded, year-specific
        pv = fcff / df
        pv_sum += pv
        roic_t = ebit_at / ic if ic > 0 else float("nan")
        rows.append(dict(year=t, growth=g, revenue=rev_new, margin=margin, ebit=ebit,
                         ebit_at=ebit_at, reinvest=reinvest, fcff=fcff, pv=pv,
                         wacc=wacc, invested_capital=ic, roic=roic_t))
        rev, ic = rev_new, ic + reinvest

    last = rows[-1]
    ebit_at_T = last["ebit_at"] * (1 + g_term)
    roic_derived = ebit_at_T / ic if ic > 0 else float("nan")
    roic_T = roic_derived if p["derive_terminal_roic"] else p["terminal_roic"]
    if roic_T <= 0:
        raise ValueError("Terminal ROIC must be positive.")
    rir = g_term / roic_T
    if rir >= 1:
        raise ValueError(f"Terminal reinvestment rate {rir:.0%} >= 100%: growth "
                         f"{g_term:.2%} exceeds what ROIC {roic_T:.1%} can fund.")
    tv = ebit_at_T * (1 - rir) / (cT["wacc"] - g_term)
    pv_tv = tv / df
    firm = pv_sum + pv_tv
    if firm <= 0:
        raise ValueError("Firm value is not positive: the assumptions do not describe "
                         "a going concern.")
    if pv_tv / firm > 1.0:
        raise ValueError(
            f"Terminal value is {pv_tv / firm:.0%} of firm value, i.e. the explicit "
            "forecast contributes negative present value overall. Every year you "
            "claim to forecast destroys value and the entire answer is the residual "
            "after year 10. Discard this run rather than reporting it.")
    eq = (firm + p["cash"] + p["non_op_assets"]
          - p["debt"] - p["minority_interest"] - p["options_val"])
    vps = eq / p["shares"]

    # THE MOST DECISION-RELEVANT NUMBER IN THE MODEL. The return on each marginal dollar
    # of capital is fixed by an identity and does not depend on growth; derived terminal
    # ROIC just converges to it. Above the mature WACC, growth creates value without
    # limit and the DCF is an extrapolation of the growth input. Measured spread across
    # the five cases: +4.3pp (ORCL) to +158pp (APP). See FINDINGS.md section 4.
    marg_roic = p["margin_target"] * (1 - p["tax_marginal"]) * p["sales_to_capital"]
    return dict(rows=rows, c0=c0, cT=cT, g_term=g_term, pv_explicit=pv_sum,
                marginal_roic=marg_roic, marginal_spread=marg_roic - cT["wacc"],
                terminal_value=tv, pv_terminal=pv_tv, terminal_share=pv_tv / firm,
                terminal_reinvest_rate=rir, terminal_roic_used=roic_T,
                terminal_roic_derived=roic_derived, firm_value=firm,
                equity_value=eq, value_per_share=vps,
                exit_ev_ebit=tv / (ebit_at_T / (1 - p["tax_marginal"]))
                if p["tax_marginal"] < 1 else float("nan"))


def solve(p, key, lo, hi, target, grid=33):
    """
    Reverse DCF: what value of one input makes the model agree with the price?

    Returns (solution, status) where status is 'ok', 'none' (no crossing in range),
    'multiple' (the answer is ambiguous) or 'infeasible' (too little of the range
    even values).

    Blind bisection assumes monotonicity. Value rises with growth only while marginal
    ROIC exceeds WACC and INVERTS below it (ORCL at s/cap 0.25). So: scan the range,
    count crossings, then bisect only inside a bracket that contains one.
    """
    xs = [lo + (hi - lo) * i / (grid - 1) for i in range(grid)]
    ys = []
    for x in xs:
        try:
            ys.append(value(dict(p, **{key: x}))["value_per_share"] - target)
        except (ValueError, ZeroDivisionError):
            ys.append(None)
    pts = [(x, y) for x, y in zip(xs, ys) if y is not None]
    feasible = (pts[0][0], pts[-1][0]) if pts else (None, None)
    if len(pts) < 2:
        return None, "infeasible", feasible
    brackets = [(pts[i][0], pts[i + 1][0])
                for i in range(len(pts) - 1)
                if (pts[i][1] <= 0 < pts[i + 1][1]) or (pts[i][1] > 0 >= pts[i + 1][1])]
    if not brackets:
        return None, "none", feasible
    a, b = brackets[0]
    for _ in range(60):
        mid = (a + b) / 2
        try:
            v = value(dict(p, **{key: mid}))["value_per_share"]
        except (ValueError, ZeroDivisionError):
            return None, "infeasible", feasible
        lo_val = value(dict(p, **{key: a}))["value_per_share"]
        if (v < target) == (lo_val < target):
            a = mid
        else:
            b = mid
    return mid, ("multiple" if len(brackets) > 1 else "ok"), feasible


def _perturb(p, k, kind, b):
    q = dict(p)
    q[k] = p[k] * b if kind == "mult" else p[k] + b / 100
    if k == "terminal_growth":
        q[k] = max(min(q[k], q["rf"]), 0.0)
    if k == "rf":
        q[k] = max(q[k], 0.0005)
    return q


def value_converged(p, iters=40, tol=1e-7):
    """
    Weight cost of capital on the model's OWN equity value, iterating to a fixed point.
    Removes the price from the valuation. Damodaran's remedy. Falls back to the
    price-weighted answer if it does not settle.
    """
    v = value(p)
    eq = v["equity_value"]
    for _ in range(iters):
        if eq <= 0:
            return v, False
        dr = p["debt"] / (p["debt"] + eq)
        w = value(dict(p, debt_ratio=dr))
        if abs(w["equity_value"] - eq) / max(abs(eq), 1e-9) < tol:
            return w, True
        eq, v = w["equity_value"], w
    return v, False


def spread_range(p, draws=4000, seed=7):
    """
    Sample every stressed input independently and uniformly inside its band.

    The all-extremes corner is a corner, not an interval: it pushes beta, ERP, spread
    and rf the same way, triple-counting one risk, and a 13.6x band decides nothing.
    Independent draws are not the truth either, since these inputs are correlated, but
    P10/P50/P90 is usable. The corner is still printed beside it.
    """
    import random
    rng = random.Random(seed)
    out = []
    for _ in range(draws):
        q = dict(p)
        for k, (kind, pess, opt) in STRESS.items():
            b = pess + (opt - pess) * rng.random()
            q = _perturb(q, k, kind, b)
        try:
            out.append(value(q)["value_per_share"])
        except (ValueError, ZeroDivisionError):
            continue
    if len(out) < draws * 0.5:
        return None
    out.sort()
    pick = lambda f: out[min(len(out) - 1, int(f * len(out)))]
    return dict(p10=pick(0.10), p25=pick(0.25), p50=pick(0.50), p75=pick(0.75),
                p90=pick(0.90), n=len(out), discarded=draws - len(out))


def tornado(p, base):
    out = []
    for k, (kind, pess, opt) in STRESS.items():
        vals = []
        for b in (pess, opt):
            try:
                vals.append(value(_perturb(p, k, kind, b))["value_per_share"])
            except (ValueError, ZeroDivisionError):
                vals.append(float("nan"))
        ok = [x for x in vals if x == x]          # drop NaN: that extreme is unvaluable
        if not ok:
            out.append((k, float("nan"), float("nan"), float("nan")))
            continue
        # A stress the model refuses to value is not a zero-impact input. Rank it at
        # the top and say so, rather than silently reporting a half-width swing.
        partial = len(ok) < len(vals)
        out.append((k, min(ok), max(ok), float("inf") if partial else max(ok) - min(ok)))
    return sorted(out, key=lambda r: (0 if r[3] != r[3] else -r[3]))


def joint(p, direction):
    q = dict(p)
    for k, (kind, pess, opt) in STRESS.items():
        q = _perturb(q, k, kind, pess if direction == "low" else opt)
    try:
        return value(q)["value_per_share"]
    except (ValueError, ZeroDivisionError):
        # An all-extremes corner can land outside what the model will value at all
        # (typically terminal value exceeding firm value). That is a fact about the
        # corner, not an error to propagate, and it is one more reason the corner is
        # not the honest range.
        return None


def main():
    a = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    # JUDGMENT inputs. Always supplied by the analyst; --from-cache never fills these.
    for x in ["--margin-target", "--growth", "--sales-to-capital"]:
        a.add_argument(x, type=float, required=True)
    # OBSERVABLE, no sensible default: required unless --from-cache supplies them.
    for x in REQUIRED_OBSERVABLE:
        a.add_argument(x, type=float, default=None)
    # OBSERVABLE with a standard default. Sentinel None so an explicit flag, the
    # cache, and the default can be told apart; the default is applied last.
    for x, _ in OBSERVABLE_DEFAULTS:
        a.add_argument(x, type=float, default=None)
    # JUDGMENT with a default. Never populated from cache.
    for x, d in [("--erp", 4.5), ("--crp", 0.0), ("--beta-u", 1.0), ("--spread", 2.0),
                 ("--mature-spread", 1.0), ("--tax-marginal", 25.0),
                 ("--terminal-roic", 12.0), ("--terminal-growth", 2.5), ("--mos", 25.0)]:
        a.add_argument(x, type=float, default=d)
    a.add_argument("--from-cache", metavar="TICKER", default=None,
                   help="populate observable inputs from data/<TICKER>/<FY>.json")
    a.add_argument("--fiscal-year", type=int, default=None,
                   help="fiscal year to read (default: latest cached for that ticker)")
    a.add_argument("--data-dir", default="data")
    a.add_argument("--rd", type=str, default=None,
                   help="R&D spend, current year first, comma separated: '800,770,723,556,531,470'")
    a.add_argument("--rd-life", type=int, default=5,
                   help="amortisable life in years (software ~3, tech hardware ~5, pharma ~10)")
    a.add_argument("--years-high", type=int, default=5)
    a.add_argument("--years-fade", type=int, default=5)
    a.add_argument("--derive-terminal-roic", action="store_true",
                   help="derive terminal ROIC from built-up invested capital instead of assuming it")
    a.add_argument("--debt-ratio", type=float, default=None,
                   help="weight the cost of capital on a TARGET debt ratio (0-1) instead "
                        "of market equity, removing the price from the valuation")
    a.add_argument("--converge-weights", action="store_true",
                   help="iterate the capital-structure weights onto the model's own "
                        "equity value instead of the market price")
    a.add_argument("--draws", type=int, default=4000,
                   help="independent draws for the P10/P90 range (0 to skip)")
    a.add_argument("--full", action="store_true",
                   help="append the year-by-year cash-flow build and cost-of-capital detail")
    a.add_argument("--json", action="store_true")
    a.add_argument("--brief", action="store_true",
                   help="decision-relevant lines only. Skips the cash-flow table and cost-of-capital detail.")
    a.add_argument("--sweep", type=str, default=None,
                   help="one input across several values in ONE process, one line each: "
                        "'rf=3.0,4.0,4.79' or 'margin_target=16,19,22'. Values in the same "
                        "units as the flags. Skips the draws.")
    a.add_argument("--scenarios", type=str, default=None,
                   help="run a grid in one process. Format 'growth:sales_to_capital,...' e.g. '8:0.58,12:1.0'")
    k = a.parse_args()

    # ---- cache resolution -------------------------------------------------------
    # Precedence is: explicit flag > cached record > built-in default. Anything typed
    # on the command line survives untouched; the cache only fills gaps.
    cache_note = None
    if k.from_cache and fundamentals is None:
        a.error("--from-cache needs fundamentals.py alongside this script.")
    if k.from_cache:
        fy = k.fiscal_year
        if fy is None:
            years = [y for t, y, _ in fundamentals.list_cached(k.data_dir)
                     if t == k.from_cache.upper()]
            if not years:
                a.error(f"no cached records for {k.from_cache.upper()} under {k.data_dir}/. "
                        f"Create one: python fundamentals.py init {k.from_cache.upper()} <FY>")
            fy = max(years)
        try:
            rec, _ = fundamentals.get(k.data_dir, k.from_cache, fy)
        except (LookupError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(2)
        problems = fundamentals.validate(rec)
        if problems:
            print(f"error: cached record {k.from_cache.upper()} FY{fy} is invalid:\n  - "
                  + "\n  - ".join(problems), file=sys.stderr)
            sys.exit(2)
        filled = fundamentals.apply_to_args(k, rec)
        cache_note = (f"{rec['ticker']} FY{rec['fiscal_year']} from {k.data_dir}/ "
                      f"[{rec.get('source', {}).get('provider', '?')}] "
                      f"{rec.get('currency', '?')} {rec.get('units', '?')} "
                      f"-> {', '.join(filled) if filled else 'nothing'}")
        if k.nol:
            print("[cache] warning: a non-zero NOL is in play. Terminal value is grown off "
                  "the final explicit year, so an NOL unexhausted by then is capitalised in "
                  "perpetuity. Review before trusting this number.", file=sys.stderr)

    for x, d in OBSERVABLE_DEFAULTS:                 # defaults applied last
        name = x[2:].replace("-", "_")
        if getattr(k, name) is None:
            setattr(k, name, d)
    missing = [x for x in REQUIRED_OBSERVABLE if getattr(k, x[2:].replace("-", "_")) is None]
    if missing:
        a.error("missing required observable inputs: " + ", ".join(missing)
                + ("" if k.from_cache else "  (or supply them with --from-cache TICKER)"))

    # Snapshot inputs as typed. The R&D block below mutates margin, margin_target,
    # invested_capital and sales_to_capital in place, so a snapshot taken afterwards
    # would record derived values as though they were the analyst's.
    as_typed = dict(vars(k))

    rdadj = None
    rd_shift = {"margin": 0.0, "margin_target": 0.0, "sales_to_capital": 1.0}
    if k.rd:
        rd = [float(x) for x in k.rd.split(",")]
        rdadj = capitalize_rd(rd, k.rd_life)
        ebit_stated = k.revenue * k.margin / 100
        ebit_adj = ebit_stated + rdadj["ebit_uplift"]
        ic_adj = k.invested_capital + rdadj["research_asset"]
        # sales-to-capital falls in proportion to the enlarged capital base, so the
        # ongoing R&D that drives growth is charged as reinvestment
        rd_shift["sales_to_capital"] = k.invested_capital / ic_adj
        k.sales_to_capital *= rd_shift["sales_to_capital"]
        rdadj.update(margin_stated=k.margin, margin_adj=ebit_adj / k.revenue * 100,
                     ic_stated=k.invested_capital, ic_adj=ic_adj,
                     stc_adj=k.sales_to_capital)
        # At maturity, R&D spend and R&D amortisation converge, so the EBIT uplift
        # decays toward a small residual. Holding the full uplift in the terminal
        # margin would overstate terminal value. Steady-state uplift with spend
        # growing at g over an N-year life is roughly rd * (1 - (1+g)^-((N+1)/2)).
        g_t = min(k.terminal_growth, k.rf) / 100
        steady = 1 - (1 + g_t) ** (-(k.rd_life + 1) / 2)
        term_uplift = (rd[0] / k.revenue) * steady * 100
        rd_shift["margin"] = rdadj["margin_adj"] - k.margin
        rd_shift["margin_target"] = term_uplift
        k.margin = rdadj["margin_adj"]
        k.margin_target += term_uplift
        rdadj.update(uplift_now=rdadj["margin_adj"] - rdadj["margin_stated"],
                     uplift_terminal=term_uplift, margin_target_adj=k.margin_target)
        k.invested_capital = ic_adj

    p = dict(revenue=k.revenue, margin=k.margin / 100, margin_target=k.margin_target / 100,
             growth=k.growth / 100, sales_to_capital=k.sales_to_capital, shares=k.shares,
             invested_capital=k.invested_capital, cash=k.cash, debt=k.debt, nol=k.nol,
             non_op_assets=k.non_op_assets, minority_interest=k.minority_interest,
             options_val=k.options_val, rf=k.rf / 100, erp=k.erp / 100, crp=k.crp / 100,
             beta_u=k.beta_u, spread=k.spread / 100, mature_spread=k.mature_spread / 100,
             tax_eff=k.tax_eff / 100, tax_marginal=k.tax_marginal / 100,
             terminal_roic=k.terminal_roic / 100, terminal_growth=k.terminal_growth / 100,
             years_high=k.years_high, years_fade=k.years_fade, price=k.price,
             derive_terminal_roic=k.derive_terminal_roic, debt_ratio=k.debt_ratio)
    try:
        if k.converge_weights:
            v, settled = value_converged(p)
            p = dict(p, debt_ratio=p["debt"] / (p["debt"] + v["equity_value"])
                     if settled and v["equity_value"] > 0 else p["debt_ratio"])
            if not settled:
                print("[!] capital-structure weights did not converge; falling back to "
                      "market-price weights.", file=sys.stderr)
        else:
            v = value(p)
    except (ValueError, ZeroDivisionError) as e:
        # These used to escape as raw tracebacks. A model that cannot value the inputs
        # should say so in one line, not dump a stack.
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)

    if k.json:
        out = {x: y for x, y in v.items() if x not in ("rows", "c0", "cT")}
        out["wacc_initial"], out["wacc_mature"] = v["c0"]["wacc"], v["cT"]["wacc"]
        # Audit trail: records what was decided, never reads it back.
        jud = ["growth", "margin_target", "sales_to_capital", "erp", "crp", "beta_u", "spread",
            "mature_spread", "tax_marginal", "terminal_roic", "terminal_growth",
            "mos", "rd_life", "years_high", "years_fade", "derive_terminal_roic"]
        obs = [x[2:].replace("-", "_") for x in REQUIRED_OBSERVABLE]
        obs += [x[2:].replace("-", "_") for x, _ in OBSERVABLE_DEFAULTS]
        out["inputs"] = {
            "observable": {f: as_typed.get(f) for f in obs},
            "judgment": {f: as_typed.get(f) for f in jud},
            "rd": as_typed.get("rd"),
            "provenance": cache_note or "command line",
        }
        print(json.dumps(out, indent=1))
        return

    c0, cT = v["c0"], v["cT"]

    PCT = {"margin", "margin_target", "growth", "rf", "erp", "crp", "spread",
           "mature_spread", "tax_eff", "tax_marginal", "terminal_roic", "terminal_growth"}

    if k.sweep:
        # Sweeping in one process instead of re-invoking: same numbers, one line each.
        # HANDBOOK requires two or three risk-free rates on every name, which was three
        # full reports.
        key, _, spec = k.sweep.partition("=")
        key = key.strip().replace("-", "_")
        if key not in p or not spec.strip():
            a.error("--sweep KEY=v1,v2,... : unknown or empty key "
                    f"'{key}'. Sweepable: " + ", ".join(sorted(PCT | {"sales_to_capital",
                    "beta_u", "years_high", "years_fade"})))
        # R&D capitalisation already mutated margin, margin_target and sales-to-capital,
        # so a swept value typed in CLI units gets the same shift a fresh run would give
        # it. Without this the sweep would silently drop the R&D adjustment.
        shift = {"margin": lambda x: x + rd_shift["margin"],
                 "margin_target": lambda x: x + rd_shift["margin_target"],
                 "sales_to_capital": lambda x: x * rd_shift["sales_to_capital"]}
        print(f"   {key:>16}{'value/sh':>11}{'buy-below':>11}{'price x':>9}"
              f"{'TV%':>6}{'m.spread':>10}")
        for raw in spec.split(","):
            x = float(raw)
            xa = shift[key](x) if (rdadj and key in shift) else x
            try:
                w = value(dict(p, **{key: xa / 100 if key in PCT else xa}))
                vs = w["value_per_share"]
                bb = f"{vs * (1 - k.mos / 100):>11,.2f}" if vs > 0 else f"{'none':>11}"
                px = f"{k.price / vs:>8.2f}x" if vs > 0 else f"{'n/a':>9}"
                print(f"   {x:>16.2f}{vs:>11,.2f}{bb}{px}"
                      f"{w['terminal_share']:>6.0%}{w['marginal_spread']:>+10.1%}")
            except (ValueError, ZeroDivisionError) as e:
                print(f"   {x:>16.2f}   unvaluable: {e}")
        return

    if k.scenarios:
        print(f"  {'growth':>8}{'s/cap':>8}{'value/sh':>11}{'ROIC(T)':>9}{'TV share':>10}")
        for spec in k.scenarios.split(","):
            g, sc = spec.split(":")
            q = dict(p, growth=float(g) / 100, sales_to_capital=float(sc))
            try:
                w = value(q)
                print(f"  {float(g):>7.0f}%{float(sc):>8.2f}{w['value_per_share']:>11.2f}"
                      f"{w['terminal_roic_used']:>8.1%}{w['terminal_share']:>10.0%}")
            except ValueError as e:
                print(f"  {float(g):>7.0f}%{float(sc):>8.2f}   unstable: {e}")
        return

    vps = v["value_per_share"]
    buy = vps * (1 - k.mos / 100) if vps > 0 else None
    tor = tornado(p, vps)
    rng = spread_range(p, k.draws) if k.draws else None

    if k.brief:
        print(f"  value/share {vps:,.2f}   price {k.price:,.2f}"
              + (f"   {k.price / vps:.2f}x   buy-below {buy:,.2f}"
                 if buy else "   NEGATIVE EQUITY, no buy-below"))
        if rng:
            print(f"  P10-P90 {rng['p10']:,.2f} to {rng['p90']:,.2f}   median {rng['p50']:,.2f}")
        print(f"  marginal ROIC {v['marginal_roic']:.1%} vs mature WACC {cT['wacc']:.2%}"
              f"   spread {v['marginal_spread']:+.1%}")
        print(f"  top driver: {tor[0][0]} ({tor[0][1]:,.2f} to {tor[0][2]:,.2f})")
        return

    # LAYOUT RULE (HANDBOOK.md): answer, then what moves it, then the detail. Sections
    # numbered so a follow-up can name one. Never print the point estimate alone.
    print("1. THE ANSWER")
    if buy:
        stance = ("price is BELOW the buy-below" if k.price <= buy
                  else "price is above the buy-below" if k.price <= vps
                  else "price is above the valuation itself")
        print(f"   value/share {vps:>12,.2f}      price {k.price:>10,.2f}   "
              f"{k.price / vps:.2f}x value")
        print(f"   buy-below   {buy:>12,.2f}      ({k.mos:.0f}% margin of safety)   {stance}")
    else:
        print(f"   value/share {vps:>12,.2f}      price {k.price:>10,.2f}")
        print("   NO BUY-BELOW PRICE. Equity value is negative: firm value does not cover")
        print("   debt plus the other bridge claims. A margin of safety applied to a")
        print("   negative number moves it UPWARD, which is less conservative, not more.")
    if rng:
        print(f"   range       {rng['p10']:>12,.2f} to {rng['p90']:,.2f}   "
              f"(P10-P90, median {rng['p50']:,.2f})")
        band = rng["p90"] / rng["p10"] if rng["p10"] > 0 else float("inf")
        print(f"               inputs drawn independently inside their bands; "
              f"{band:.1f}x wide")
    jl, jh = joint(p, "low"), joint(p, "high")
    fmt = lambda x: f"{x:,.2f}" if x is not None else "unvaluable"
    print(f"   corner      {fmt(jl):>12} to {fmt(jh)}   (every input extreme at once, "
          f"not a probability)")
    if cache_note:
        print(f"   source      {cache_note}")

    # ------------------------------------------------- 2. WHAT DECIDES THE ANSWER
    print("\n2. WHAT DECIDES IT  (ranked by how far each input moves value/share)")
    print(f"   {'#':>2} {'input':20}{'low':>11}{'high':>11}{'swing':>11}")
    finite = [r[3] for r in tor if r[3] == r[3] and r[3] != float("inf")]
    scale = max(finite) if finite else 1.0
    # Rows below 5% of the top swing are rolled into one line. Nothing is hidden: they
    # are named, numbered and bounded, so a follow-up can still ask for any of them.
    minor = []
    for i, (name, lo, hi, sw) in enumerate(tor, 1):
        if sw != sw or sw == float("inf"):
            print(f"   {i:>2} {name:20}{'BREAKS':>33}  one extreme is unvaluable")
            continue
        if i > 3 and sw < 0.05 * scale:
            minor.append((i, name, sw))
            continue
        print(f"   {i:>2} {name:20}{lo:>11,.2f}{hi:>11,.2f}{sw:>11,.2f}")
    if minor:
        print(f"   {minor[0][0]}-{minor[-1][0]} " + ", ".join(n for _, n, _ in minor)
              + f": each under {max(s for _, _, s in minor):,.2f}")

    # ------------------------------------------------------ 3. GROWTH ECONOMICS
    print("\n3. IS GROWTH WORTH ANYTHING HERE?")
    mr, sp = v["marginal_roic"], v["marginal_spread"]
    print(f"   return on each new dollar of capital {mr:>8.1%}"
          f"   = margin {p['margin_target']:.1%} x (1-t) x s/cap {p['sales_to_capital']:.2f}")
    print(f"   mature WACC                          {cT['wacc']:>8.2%}")
    print(f"   spread                               {sp:>+8.1%}")
    if sp > 0.03:
        print("   [!] Growth creates value without limit: the reinvestment charge never "
              "binds,\n       so growth is an extrapolation lever and row 1 is the valuation.")
    elif sp > 0:
        print("   Growth adds value, but thinly. The answer is not a growth bet.")
    else:
        print("   Growth DESTROYS value here: each new dollar earns less than it costs.")
        print("   Faster growth lowers the valuation. Check that this is intended.")
    print(f"   terminal ROIC {v['terminal_roic_used']:.1%} "
          f"{'[derived from built-up capital]' if k.derive_terminal_roic else '[assumed]'}"
          f"   converges toward the marginal return above")

    # ---------------------------------------------------- 4. WHAT THE PRICE NEEDS
    print("\n4. WHAT THE PRICE ALREADY REQUIRES  (reverse DCF)")
    for key, lo, hi, lab, fmt in [
            ("growth", 0.0, 0.60, "revenue growth, high-growth years", "pct"),
            ("margin_target", 0.01, 0.80, "target operating margin", "pct"),
            ("sales_to_capital", 0.1, 12.0, "sales-to-capital", "num")]:
        s, status, feas = solve(p, key, lo, hi, k.price)
        show = lambda x: (f"{x:.0%}" if fmt == "pct" else f"{x:.2f}") if x is not None else "?"
        if status == "none":
            clipped = feas[1] is not None and feas[1] < hi * 0.99
            txt = "  none"
            note = (f"  (unreachable; model only values up to {show(feas[1])})" if clipped
                    else f"  (no value in {show(lo)}-{show(hi)} reaches the price)")
        elif status == "infeasible":
            txt, note = "  n/a", "  (model does not value across this range)"
        else:
            txt = f"{s:.1%}" if fmt == "pct" else f"{s:.2f}"
            note = "  [!] MULTIPLE SOLUTIONS, value is not monotonic in this input" \
                   if status == "multiple" else ""
        print(f"   {lab:38}{txt:>8}{note}")
    print(f"   currently assumed: growth {p['growth']:.1%}, margin {p['margin_target']:.1%}, "
          f"s/cap {p['sales_to_capital']:.2f}")

    # ------------------------------------------------------------- 5. WHAT BREAKS IT
    print("\n5. FLAGS")
    flags = []
    if v["terminal_share"] > 0.75:
        flags.append(f"terminal value {v['terminal_share']:.0%} of firm value: most of this "
                     "is an assumption about year 11 onward")
    gap = abs(v["terminal_roic_derived"] - v["terminal_roic_used"])
    if gap > 0.05:
        flags.append(f"terminal ROIC assumed {v['terminal_roic_used']:.1%} vs "
                     f"{v['terminal_roic_derived']:.1%} implied by the capital build: the "
                     "explicit and terminal periods disagree")
    if rdadj and not rdadj.get("amort_complete", True):
        flags.append(f"R&D amortisation from {rdadj['amort_years_used']} of "
                     f"{rdadj['amort_years_needed']} years: EBIT uplift overstated, value "
                     f"biased UP. Supply {k.rd_life + 1} years")
    if k.reported_fcf:
        y1 = v["rows"][0]["fcff"]
        if abs(y1 - k.reported_fcf) / max(abs(k.reported_fcf), 1) > 0.25:
            flags.append(f"year-1 FCFF {y1:,.0f} vs reported FCF {k.reported_fcf:,.0f}: "
                         "reinvestment does not match observed cash flow")
    if p["tax_eff"] < 0.15 and p["tax_eff"] < p["tax_marginal"] - 0.05:
        flags.append(f"effective tax {p['tax_eff']:.0%} held flat {k.years_high} years then "
                     f"fades to {p['tax_marginal']:.0%}: rates that low usually come from "
                     "discrete items")
    if k.debt_ratio is None and not k.converge_weights:
        flags.append("cost of capital weighted on MARKET PRICE: value moves with the quote, "
                     "wrong direction. --converge-weights removes it")
    if abs(p["sales_to_capital"] - p["revenue"] / max(p["invested_capital"], 1e-9)) < 0.05:
        flags.append(f"s/cap {p['sales_to_capital']:.2f} = revenue/invested capital, the "
                     "AVERAGE book ratio; Damodaran's input is MARGINAL, so on buyback-heavy "
                     "names capital efficiency is overstated")
    if k.owner_earnings:
        y = k.owner_earnings / k.price
        print(f"   owner-earnings yield {y:.2%} vs risk-free {p['rf']:.2%} "
              f"({'above' if y > p['rf'] else 'BELOW'} the bond)")
    for f in flags:
        lines = textwrap.wrap(f, 86) or [""]
        print(f"   [!] {lines[0]}")
        for r in lines[1:]:
            print(f"       {r}")
    if not flags:
        print("   no structural flags")

    # ------------------------------------------------------------ 6. DETAIL (opt-in)
    if not k.full:
        print("\n   --full for the cash-flow build, cost of capital, equity bridge.")
        return

    print("\n6. DETAIL")
    if rdadj:
        tag = {"complete": "", "amortisation_short": " - AMORTISATION SHORT",
               "insufficient_data": " - INSUFFICIENT DATA"}[rdadj["status"]]
        print(f"   R&D capitalised ({k.rd_life}-year life, {rdadj['years_supplied']} "
              f"years supplied{tag})")
        print(f"     research asset {rdadj['research_asset']:,.0f}   amortisation "
              f"{rdadj['amortisation']:,.0f}   EBIT uplift {rdadj['ebit_uplift']:+,.0f}")
        print(f"     margin {rdadj['margin_stated']:.1f}% -> {rdadj['margin_adj']:.1f}%"
              f"   capital {rdadj['ic_stated']:,.0f} -> {rdadj['ic_adj']:,.0f}"
              f"   s/cap -> {rdadj['stc_adj']:.2f}")
        print(f"     uplift fades +{rdadj['uplift_now']:.1f}pp today -> "
              f"+{rdadj['uplift_terminal']:.1f}pp at maturity")
    print(f"   cost of capital: initial beta {c0['beta_levered']:.2f}  Ke "
          f"{c0['cost_of_equity']:.2%}  Kd(at) {c0['cost_of_debt']:.2%}  "
          f"E/D {c0['w_equity']:.0%}/{c0['w_debt']:.0%}  WACC {c0['wacc']:.2%}")
    print(f"                    mature  beta 1.00  spread {k.mature_spread:.2f}%"
          f"                       WACC {cT['wacc']:.2%}")
    if k.converge_weights:
        print("                    weights converged on intrinsic equity, not price")
    print(f"\n   {'yr':>3}{'g':>7}{'revenue':>11}{'margin':>8}{'EBIT(1-t)':>11}"
          f"{'reinvest':>10}{'FCFF':>10}{'WACC':>7}{'ROIC':>7}")
    for r in v["rows"]:
        print(f"   {r['year']:>3}{r['growth']:>6.1%}{r['revenue']:>11,.0f}{r['margin']:>8.1%}"
              f"{r['ebit_at']:>11,.0f}{r['reinvest']:>10,.0f}{r['fcff']:>10,.0f}"
              f"{r['wacc']:>7.2%}{r['roic']:>7.1%}")
    print(f"\n   terminal growth {v['g_term']:.2%} (capped at risk-free {p['rf']:.2%})   "
          f"reinvestment rate {v['terminal_reinvest_rate']:.1%}   "
          f"exit EV/EBIT {v['exit_ev_ebit']:.1f}x")
    print(f"   PV explicit {v['pv_explicit']:>14,.0f}")
    print(f"   PV terminal {v['pv_terminal']:>14,.0f}   ({v['terminal_share']:.0%} of firm)")
    print(f"   firm value  {v['firm_value']:>14,.0f}")
    print(f"   + cash {k.cash:,.0f}  + non-op {k.non_op_assets:,.0f}  - debt {k.debt:,.0f}"
          f"  - minorities {k.minority_interest:,.0f}  - options {k.options_val:,.0f}")
    print(f"   equity      {v['equity_value']:>14,.0f}")


if __name__ == "__main__":
    main()
