# Rationale — why the method is shaped this way

**Read this only when changing the model or the rubric.** Running an assessment needs
`HANDBOOK.md` alone. This file exists so the reasoning is recorded once rather than
retold in four places, which is what it used to cost.

Do not silently reverse anything here.

---

## 1. Model design decisions

| Decision | Why |
|---|---|
| Terminal growth is an **input capped at the risk-free rate** | A ceiling, not a target. Hard-coding `g_term = rf` assumes every company grows at the maximum sustainable rate forever; on one case that line was worth 15%. |
| **Cost of capital converges**: beta → 1.0, spread → mature, year-specific discount factors compounded | A company in its terminal state is not the same risk as one growing 22%. |
| Margins and tax rates **hold through high growth, converge during the fade** | Damodaran's template. Trade-off: for companies whose margins are expanding now — or cyclically depressed now — holding them flat under- or overstates. BABA FY2026 is the sharp case: a 4.9% margin held five years makes explicit FCFF negative throughout. |
| Reinvestment = ΔRevenue / sales-to-capital, **invested capital tracked** so terminal ROIC can be derived | Removes the discontinuity between explicit and terminal periods. |
| **R&D capitalised**, EBIT uplift fading to a steady-state residual | If R&D is added back to earnings it must also be charged as reinvestment. Holding the full uplift flat overstated one case by 21%. |
| **Marginal ROIC is a headline number** | `margin_target × (1−t) × sales-to-capital`, independent of growth. Above the mature WACC, growth creates value without limit and the DCF is an extrapolation of the growth input. Measured: ORCL +4.3pp, CIEN +6.4, GOOGL +6.7, ADBE +24.2, APP +158 — in none does the reinvestment charge bind. |
| **Range is P10–P90 from independent draws**; the all-extremes corner is printed beside it and labelled a corner | CIEN: P10–P90 is 2.4x wide, the corner 13.6x. The corner pushes beta, ERP, spread and rf the same way, triple-counting one risk. |
| `STRESS` is a **superset of `JUDGMENT`**, adding `rf` and `mature_spread` | `rf` is a cached market observable so it cannot join `JUDGMENT` without breaking the cache contract, but it is the second-largest lever in the model. Leaving it out made the budget an understatement. |
| The price-weighted WACC default is **kept but flagged**, with two opt-outs | Market-equity weights make value a function of the price being judged, backwards: ORCL price $40 → value $81, $640 → $67. `--converge-weights` iterates onto intrinsic equity (ORCL returns 76.36 at every price); `--debt-ratio X` uses a target structure. |
| Reverse DCF **scans before it bisects** | Value is monotonic in growth only while marginal ROIC exceeds WACC. Returns `ok`, `multiple`, `none` (naming the feasible range) or `infeasible`. |
| Owner-earnings yield reported as an **independent check**, not an output | In backtesting this ratio alone ranked forward returns as well as the full DCF. |
| Margin of safety applied to the **output**, not buried in the discount rate | Keeps the two forms of conservatism separable. |
| **A negative equity value emits no buy-below** | A margin of safety on a negative number moves it upward. The negative figure is still printed. |
| R&D completeness reported as **two flags** | The asset reads `rd[0..life-1]` and is complete at `life`; amortisation reads `rd[1..life]` and needs `life+1`. Short amortisation overstates the uplift and biases value up. |
| A record states **what period it covers**, separately from its cache key | `fiscal_year` is a path label. `period_type` and `period_end` are the claim, so a TTM figure ending 2026 can sit under a 2025 key and say so. |
| **A cached fiscal year is never re-fetched** | A cached year is an assertion about the past. Superseding needs `--refresh`, which prints a field-by-field diff. |
| The cache stores **observables only**; the validator refuses judgment inputs | A file that could carry a growth assumption would quietly become one. |
| `price` and `rf` cached **as of fiscal year end** | That is the entry price a backtest needs. `--price` always overrides. |
| Percent fields in (0, 1) **rejected** unless `units_checked` | 0.141 for 14.1% sits inside every range check and yields a plausible valuation. |
| Implied price-to-sales outside **0.01–500** rejected | Catches a share count in absolute terms against revenue in millions. A heuristic, not a proof. |
| `--json` records the **inputs as typed** | A valuation you cannot reproduce is not evidence. Records judgment; never reads it back. |

### Known limitations

- **The discount rate is largely a disguised rates forecast.** rf 4.79% → 3.0% adds
  38–66% (GOOGL +38%, CIEN +54%, ORCL +66%). Always run two or three rates.
- **CAPM under-penalises business unpredictability.** Ciena's eightfold earnings swings
  gave a levered beta of 1.16, because that volatility is diversifiable. A genuine
  philosophical conflict with Buffett, not a calibration error — and what G1 exists to
  catch instead.
- **Terminal value routinely runs 55–85% of firm value.** Flagged above 75%; above 100%
  the model raises rather than printing.
- **Sales-to-capital is often set to the average book ratio.** Damodaran's input is
  marginal; on buyback-heavy names book capital is understated and this overstates
  capital efficiency. Now flagged.
- **The model does not apply to financials.** Debt is raw material, not financing.
- Employee options are an input value, not Black-Scholes.
- No data provider is bundled; `manual` never fetches.
- **The container cannot reach sec.gov.** All EDGAR access goes through WebFetch, whose
  summariser mangles the parallel arrays in `submissions.json`. Hence the fixed recipe in
  HANDBOOK §2, and the rule to validate two or three known years before trusting any
  extraction.

## 2. What the fork changed, and why

Forked from `buffett-stock-assessment` 2026-09-09 and re-anchored to this project's model.

| Original | Here | Why |
|---|---|---|
| `scripts/valuation.py`, owner earnings, flat r=9%, MoS 30/40/50% | **Deleted.** Value comes from `hybrid_valuation.py`, MoS 25% | The original is Era 1. Mixing it with Era 2 numbers is the exact comparison ASSESSMENTS.md forbids. |
| Price bands measured as discount to IV at 30–50% MoS | **Re-anchored to 25% MoS**, so price ≤ buy-below ⇔ Price ≥ 4 | Under the old bands, buying at the buy-below price scored Price 3, which the matrix treats as not-yet-cheap. Incoherent. |
| Price score set by judgment | **Computed** by `score.py --value-per-share` | ADBE, TCOM and APP were recorded at Price 4/4/5; the bands give 3/3/3. The column had drifted. |
| No P90 cap | **Cap added** | A price above the optimistic decile cannot score above 1. |
| No financials guard | **G9 added, redirecting rather than failing** | Codifies the BRK.B substitution. |
| Dual class inside G6 | **Demoted to a warning** | See §3. |
| Aggregator pages, 10-year ratio tables | **SEC EDGAR + IBKR** | Already the project's practice. |
| Running comparison table across tickers | **Removed**, rank score deleted with it | Assessments are self-contained. |
| Own output template | **One layout, sections as in HANDBOOK §7** | One layout, not two. |
| Unversioned | **Version stamp + regression test** | An unversioned rubric makes past verdicts unreproducible. |

**One live verdict changed as a direct consequence: TCOM moves WATCH → PASS.** The
original scored its Price judgmentally at 4/5; the re-anchored bands compute 3/5 from the
same 0.86x ratio, and quality 3.0 at Price 3 is a PASS cell. ADBE and APP also lost a
Price point but neither verdict moved — ADBE stays WATCH, APP still fails the
understandability floor. ASSESSMENTS.md's TCOM row carries a footnote saying the old row
was correct under the version that produced it.

**The fork also closed the register's open question** about the verdict rule being applied
inconsistently across BEAN, CRL and LII. BEAN is a WATCH at 2.6x its buy-below while CRL
is a PASS at 2.45x and LII a PASS at 1.37x. The matrix reproduces all three exactly: BEAN
quality 4.0 / Price 1 lands in the 4.0–4.4 row where every cell is WATCH or better; CRL at
3.8 and LII at 3.6 land one row lower, where Price 1 and Price 2 are PASS. The rule was
never inconsistent — it was quality-weighted and undocumented.
`test_rubric.py::test_matrix_reproduces_register` pins this.

## 3. Why the gates are absolute, and the one that was demoted

- **G1** is empirically the weakest gate here: over 2021–26 it cost nine points and
  matched SPY. Kept on grounds of cost, not proof.
- **G2** — the method needs evidence. Great young companies exist; this style cannot
  identify them and does not pretend to.
- **G3** — turnarounds seldom turn; the analysis becomes a bet on management execution.
  The test is whether *the thesis depends on recovery*, not merely whether earnings fell.
  A profitable franchise choosing to spend is not a turnaround.
- **G4** — leverage turns a temporary problem into a permanent loss.
- **G5** — the stock can rise while the per-share claim shrinks.
- **G6** — numbers that cannot be trusted cannot be analysed.
- **G7** — a coin flip with a story attached.
- **G8** — profits go to the customer in every price war.
- **G9** — FCFF has no meaning where debt is raw material, and reported earnings swing on
  mark-to-market.

**Dual class.** The original made a dual-class structure used to override shareholders on
capital allocation part of G6. A strict reading refused Meta at the November 2022 trough
near $88.09, before a sevenfold move. That is the method's most dangerous documented
failure and it is not hypothetical. Dual class now costs Management up to one point and
must be named; it disqualifies only where the structure has actually been used to push
through value-destroying allocation.

## 4. Why these weights

Moat carries the most because it is what makes a ten-year forecast possible. Financials at
30% are the proof the moat exists but cannot substitute for understanding its source.
Management at 20%: a great business survives mediocre management, but capital allocation
compounds. Understandability at 15% plus a floor, because in practice it is binary and
works better as a disqualifier than as a graded weight. Price stays out of Quality because
blending would let a cheap weak business outscore a fairly priced great one.

## 5. Regression baselines

`tests/test_regression.py` pins CIEN 168.57 · ADBE 322.15 · GOOGL 180.09 · APP 412.98 ·
ORCL 71.87. `buffett-verdict/tests/test_rubric.py` pins the matrix and Price bands against
the register rather than against model output. Run both after touching either file; they
are pinned against different things and a change to one should not silently move the other.

**The CIEN baseline is known wrong and pinned deliberately:** 5 years of R&D against a
5-year life leaves amortisation 14.9% short and the EBIT uplift 55% too high, biasing
value up. It needs Ciena's FY2020 R&D from the 10-K. The model warns; the data has not
been fixed. Two real bugs were found by noticing a value move — a missing terminal-growth
guardrail, and an R&D uplift held flat into the terminal margin, a 21% error — which is
why a moved baseline is investigated rather than updated.

`test_regression.py` also documents two guards that are weaker than they look:
`test_wacc_below_terminal_growth_raises` only fires with ERP at zero, because the mature
cost of equity is rf + ERP and terminal growth is capped at rf; and
`test_zero_terminal_roic_raises` is bypassed entirely by `--derive-terminal-roic`.

## 6. Token economics of a session

Measured on the BABA assessment, 18 September 2026.

The container starts empty, and project knowledge can only reach disk *through* context:
`project_read` returns a 40KB file inline, so the four scripts cost ~16k tokens to read
and ~16k to type back out — **~32k before any analysis began**. Stripping comments does
not help; rationale is 19% of the model file and is the part worth keeping.

GitHub is on the container's proxy allowlist (sec.gov is not), so `git clone` replaces
that ~32k with about fifty tokens and changes no output. Everything else was second-order:
the four reference docs repeated each other (~3k), four of twelve EDGAR calls were wasted
on robots-disallowed pages, mangled `submissions.json` reads and guessed XBRL tag names
(~3k), and `--full` printed a ten-year table the report never uses (~1.3k).

The rule that follows: **the repo is the source of truth for anything executable; project
knowledge holds prose only.**
