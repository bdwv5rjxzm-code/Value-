"""
Regression baselines for hybrid_valuation.py.

These pin known outputs. If a refactor moves them, that is a finding to
investigate, not a rounding difference to update. Two real bugs were found by
noticing a value move: a missing terminal-growth guardrail, and an R&D uplift
held flat into the terminal margin (a 21% error).
"""
import subprocess, json, sys, os, pytest

# Layout-agnostic: the model may sit beside this file (flat repo) or one level up
# (tests/ subdirectory). Resolve rather than assume, so a re-upload that flattens the
# tree does not silently break the baselines.
_HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = next(c for c in (os.path.join(_HERE, "hybrid_valuation.py"),
                          os.path.join(os.path.dirname(_HERE), "hybrid_valuation.py"))
              if os.path.exists(c))
ROOT = os.path.dirname(SCRIPT)

COMMON = ["--rf", "4.79", "--erp", "4.5", "--terminal-growth", "2.5",
          "--mos", "25", "--derive-terminal-roic", "--json"]

CASES = {
    # name: (args, expected_value_per_share)
    "ciena_rd5": ([
        "--revenue", "6021", "--margin", "14.1", "--margin-target", "19.0",
        "--growth", "22", "--sales-to-capital", "1.7", "--shares", "141.8",
        "--invested-capital", "3497", "--cash", "2789", "--debt", "3231",
        "--tax-eff", "8", "--tax-marginal", "25", "--beta-u", "1.10",
        "--spread", "2.0", "--mature-spread", "1.0", "--price", "321.00",
        "--rd", "800,767,751,625,537", "--rd-life", "5"], 168.57),

    "adobe_rd3": ([
        "--revenue", "25198", "--margin", "36.7", "--margin-target", "35.0",
        "--growth", "8", "--sales-to-capital", "2.0", "--shares", "397.5",
        "--invested-capital", "12929", "--cash", "5551", "--debt", "7002",
        "--tax-eff", "20", "--tax-marginal", "25", "--beta-u", "1.10",
        "--spread", "0.75", "--mature-spread", "0.75", "--price", "263.03",
        "--rd", "4294,3944,3473,2987", "--rd-life", "3"], 322.15),

    "alphabet_rd5": ([
        "--revenue", "446053", "--margin", "33.1", "--margin-target", "30.0",
        "--growth", "12", "--sales-to-capital", "0.89", "--shares", "12230",
        "--invested-capital", "499000", "--cash", "240000", "--debt", "118000",
        "--tax-eff", "17", "--tax-marginal", "25", "--beta-u", "1.10",
        "--spread", "0.6", "--mature-spread", "0.6", "--price", "338.56",
        "--rd", "61087,49326,45427,39500,31562,27573", "--rd-life", "5"], 180.09),

    "applovin_nord": ([
        "--revenue", "6829", "--margin", "77.0", "--margin-target", "70.0",
        "--growth", "20", "--sales-to-capital", "4.0", "--shares", "339",
        "--invested-capital", "3619", "--cash", "3053", "--debt", "3515",
        "--tax-eff", "13", "--tax-marginal", "25", "--beta-u", "1.20",
        "--spread", "2.0", "--mature-spread", "1.25", "--price", "323.00"], 412.98),

    "oracle_nord": ([
        "--revenue", "67358", "--margin", "33.2", "--margin-target", "28.0",
        "--growth", "15", "--sales-to-capital", "0.9", "--shares", "2880",
        "--invested-capital", "175100", "--cash", "17690", "--debt", "153410",
        "--tax-eff", "13", "--tax-marginal", "25", "--beta-u", "1.10",
        "--spread", "1.75", "--mature-spread", "1.25", "--price", "158.78"], 71.87),
}


def run(args):
    r = subprocess.run([sys.executable, SCRIPT] + args + COMMON,
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr)
    return json.loads(r.stdout)


@pytest.mark.parametrize("name", CASES)
def test_regression_value_per_share(name):
    args, expected = CASES[name]
    got = run(args)["value_per_share"]
    assert abs(got - expected) < 0.01, f"{name}: expected {expected}, got {got:.2f}"


def test_terminal_value_share_is_sane():
    """Terminal value above ~100% of firm value means explicit FCFF is negative
    throughout and the output should not be trusted."""
    for name, (args, _) in CASES.items():
        assert run(args)["terminal_share"] < 1.0, f"{name}: terminal value exceeds firm value"


def test_derived_terminal_roic_is_used_when_flagged():
    args, _ = CASES["ciena_rd5"]
    out = run(args)
    assert abs(out["terminal_roic_used"] - out["terminal_roic_derived"]) < 1e-9


# --- guardrails: these must raise, not return a plausible-looking number ---

BASE = ["--rf", "4.79", "--erp", "4.5", "--terminal-growth", "2.5", "--mos", "25", "--json"]


def _expect_failure(args, fragment):
    r = subprocess.run([sys.executable, SCRIPT] + args, capture_output=True, text=True)
    assert r.returncode != 0, f"expected failure, got: {r.stdout[:200]}"
    assert fragment.lower() in r.stderr.lower(), f"unexpected error: {r.stderr[:300]}"


def test_zero_terminal_roic_raises():
    """Only reachable when terminal ROIC is ASSUMED. With --derive-terminal-roic
    the input is ignored, which is correct but means the guard is bypassed."""
    _expect_failure(list(CASES["oracle_nord"][0]) + BASE + ["--terminal-roic", "0"],
                    "terminal roic")


def test_wacc_below_terminal_growth_raises():
    """FINDING: the guard checks the MATURE WACC, which always uses beta 1.0.
    With terminal growth capped at the risk-free rate, the mature cost of equity
    is rf + ERP, so the guard is unreachable for any positive ERP. It only fires
    with ERP at zero and debt in the structure, where after-tax cost of debt
    drags WACC below rf. Worth knowing the guard is weaker than it looks."""
    _expect_failure(list(CASES["oracle_nord"][0]) + BASE +
                    ["--erp", "0", "--mature-spread", "0", "--terminal-growth", "4.79"],
                    "terminal growth")


def test_terminal_growth_is_capped_at_risk_free():
    """Requesting 9% terminal growth against a 4.79% risk-free must clamp, not comply."""
    r = subprocess.run([sys.executable, SCRIPT] + list(CASES["adobe_rd3"][0]) + BASE +
                       ["--terminal-growth", "9.0", "--derive-terminal-roic"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert abs(json.loads(r.stdout)["g_term"] - 0.0479) < 1e-9


def test_negative_growth_does_not_break_the_model():
    """A shrinking business must still value, not crash on negative reinvestment."""
    r = subprocess.run([sys.executable, SCRIPT] + list(CASES["adobe_rd3"][0]) + BASE +
                       ["--growth", "-5", "--derive-terminal-roic"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["value_per_share"] > 0
