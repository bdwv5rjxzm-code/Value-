"""
Regression baselines for the rubric. Rubric v1.0.

These pin the verdict matrix and the Price bands against the register in
ASSESSMENTS.md. If an edit moves one, that is a finding to investigate and a
version bump, not a number to update.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

from score import price_score, verdict  # noqa: E402


def q(u, m, f, mg):
    return {"understand": u, "moat": m, "financials": f, "management": mg}


MATRIX_CASES = {
    #  name:   (dimensions,        quality, price score, verdict)
    "BEAN": (q(4, 4, 4, 4), 4.0, 1, "WATCH"),
    "CRL":  (q(4, 4, 4, 3), 3.8, 1, "PASS"),
    "LII":  (q(4, 3, 4, 4), 3.6, 2, "PASS"),
    "META": (q(4, 4, 4, 3), 3.8, 3, "WATCH"),
}


@pytest.mark.parametrize("name", MATRIX_CASES)
def test_matrix_reproduces_register(name):
    dims, expected_q, pscore, expected_v = MATRIX_CASES[name]
    got_q, got_v, _ = verdict(dims, pscore, [], None)
    assert got_q == expected_q, f"{name}: quality {got_q} != recorded {expected_q}"
    assert got_v == expected_v, f"{name}: verdict {got_v} != recorded {expected_v}"


PRICE_CASES = {
    #  name:   (price,   value/share, band score, recorded score)
    "ADBE":  (263.03, 322.15, 3, 4),
    "TCOM":  (41.03,   47.75, 3, 4),
    "APP":   (323.00, 412.98, 3, 5),
    "GOOGL": (338.56, 191.04, 1, 1),
    "CIEN":  (321.00, 168.57, 1, 1),
    "ORCL":  (158.78,  71.87, 1, 1),
}


@pytest.mark.parametrize("name", PRICE_CASES)
def test_price_band(name):
    price, vps, expected, _recorded = PRICE_CASES[name]
    got, ratio, _ = price_score(price, vps)
    assert got == expected, f"{name}: {ratio:.3f}x value scored {got}, expected {expected}"


def test_the_three_disagreements_are_still_three():
    """If a band edit silently reconciles these, that is a finding, not a fix."""
    disagree = [n for n, (p, v, band, rec) in PRICE_CASES.items() if band != rec]
    assert sorted(disagree) == ["ADBE", "APP", "TCOM"]


def test_tcom_verdict_flips_under_the_new_bands():
    dims = q(3, 3, 3, 3)
    assert verdict(dims, 4, [], None)[1] == "WATCH"      # as recorded
    assert verdict(dims, 3, [], None)[1] == "PASS"       # as computed


def test_buy_below_is_the_price_4_boundary():
    assert price_score(75.0, 100.0)[0] == 4
    assert price_score(75.01, 100.0)[0] == 3
    assert price_score(60.0, 100.0)[0] == 5


# --- floors and gates -------------------------------------------------------

def test_understandability_floor_passes_regardless_of_price():
    _, v, reasons = verdict(q(2, 3, 3, 4), 5, [], 0.78)
    assert v == "PASS" and "Understandability" in reasons[0]


def test_understandability_one_is_a_gate_not_a_floor():
    _, v, reasons = verdict(q(1, 5, 5, 5), 5, [], 0.5)
    assert v == "PASS" and reasons[0].startswith("Gate failed")


def test_turnaround_gate_beats_a_cheap_price():
    _, v, reasons = verdict(q(4, 4, 3, 3), 5, ["G3 turnaround: EBIT down from peak"], 0.5)
    assert v == "PASS" and "G3" in reasons[0]


def test_moat_floor():
    assert verdict(q(4, 2, 4, 4), 5, [], 0.5)[1] == "PASS"


# --- caps -------------------------------------------------------------------

def test_p90_cap():
    score, _, notes = price_score(120.0, 100.0, p90=110.0)
    assert score == 1 and any("P90" in n for n in notes)


def test_p90_cap_does_not_fire_inside_the_range():
    assert price_score(70.0, 100.0, p90=140.0)[0] == 4


def test_bond_yield_cap():
    score, _, notes = price_score(70.0, 100.0, oe_yield=3.1, rf=4.79, growth=4.0)
    assert score == 2 and any("risk-free" in n for n in notes)


def test_bond_yield_cap_does_not_fire_when_growing():
    assert price_score(70.0, 100.0, oe_yield=3.1, rf=4.79, growth=11.0)[0] == 4


def test_caps_only_move_down():
    assert price_score(200.0, 100.0, p90=400.0, oe_yield=9.0, rf=4.79)[0] == 1


def test_negative_equity_scores_one():
    score, ratio, notes = price_score(50.0, -12.0)
    assert score == 1 and ratio is None and notes


# --- the G9 substitution ----------------------------------------------------

def test_brk_b_substitution_path():
    got_q, v, _ = verdict(q(4, 4, 4, 4), 3, [], None)
    assert got_q == 4.0 and v == "WATCH"
