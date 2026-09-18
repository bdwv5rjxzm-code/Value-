# Handbook — the operational reference

Damodaran mechanics answer *what is this worth*. Buffett gates answer *should I own it*.
They stay separate layers and both get reported. Where they disagree, that is information.

**Read this file to run an assessment. Read `RATIONALE.md` only when changing the model
or the rubric** — it holds the why, the fork history, the measured findings and the known
limitations. Splitting them is deliberate: the rationale used to be duplicated across
four files and cost ~6k tokens to re-read on every stock that changed nothing.

Rubric version **1.0** (2026-09-09) · Report layout **2.0** (2026-09-10).
Every verdict records the rubric version beside the model inputs, or it is not reproducible.

---

## 0. Session bootstrap

```bash
git clone --depth 1 https://github.com/bdwv5rjxzm-code/Value-.git v && cd v
python3 -m pytest test_regression.py test_rubric.py -q     # ~1s, proves the clone is intact
```

Baselines pinned at rf 4.79%, ERP 4.5%, terminal growth 2.5%, MoS 25%,
`--derive-terminal-roic`: **CIEN 168.57 · ADBE 322.15 · GOOGL 180.09 · APP 412.98 ·
ORCL 71.87**. If a refactor moves one, that is a finding, not a number to update.

Never rebuild these files from project knowledge by hand. Reading 61,550 characters of
Python into context and typing it back out cost ~32k tokens per session; the clone costs
about fifty.

## 1. Identify and classify

Confirm the company behind the ticker, then classify:

- **Core** — profitable 10+ years, understandable, consumer/industrial/energy/rail.
- **Adjacent** — Buffett would rarely buy it (software, semis, capital-light growth,
  platforms) but the logic still applies. Assess normally and say so in the header.
- **Outside** — pre-profit, under ~7 years of history, binary biotech, SPACs,
  turnarounds. Short PASS write-up only. A scored 1.4/5 on a pre-revenue company is
  false precision.

## 2. Gather data

**Every figure carries an as-of date and a label: observed or assumed. Never fill a gap
with an estimate — write "not found" and score that dimension conservatively.**

Financials from **SEC EDGAR**, price and history from **IBKR**. A cached fiscal year is
an assertion about the past and is never re-fetched without `--refresh`.

### The EDGAR recipe — fixed, six calls

The container cannot reach sec.gov (proxy returns 403 on CONNECT), so this runs through
WebFetch. Do **not** improvise: `browse-edgar` is robots-disallowed, `submissions.json`
has parallel arrays that summarisers mangle, and guessing XBRL tag names produces 404s.
Four of twelve calls were wasted that way on BABA.

1. `data.sec.gov/api/xbrl/companyconcept/CIK##########/us-gaap/Revenues.json`
   → ask for full-fiscal-year entries only, digit for digit. **Check two or three years
   against what you already know** — that validates the whole extraction path before you
   trust anything else from it.
2. Same file, ask for the `accn` of the latest fiscal year → the accession number.
3. `www.sec.gov/Archives/edgar/data/<cik>/<accn-no-dashes>/FilingSummary.xml`
   → ask for the `HtmlFileName` of the balance sheet, income statement, cash flow and
   segment schedules.
4-6. Fetch those R-pages and ask for a **verbatim transcription**. They are plain HTML
   tables and transcribe reliably, unlike the XBRL frames.

Then check the statements tie (assets = liabilities + mezzanine + equity; the expense
lines sum to operating income; EPS = attributable income ÷ share count). If they tie, the
data is sound. If a figure appears in both an R-page and a `companyconcept` call, that is
a second independent confirmation — note it.

Cache the result immediately:

```bash
python3 fundamentals.py put TICKER FY --revenue … --period-end YYYY-MM-DD \
  --currency USD --units millions --provider "SEC EDGAR 20-F" --source-note "accn …"
python3 fundamentals.py list | validate | show TICKER FY | init TICKER FY
```

Cache holds **observables only**; the validator refuses judgment inputs. `price` and `rf`
are stored as of fiscal year end, because that is the entry price a backtest needs;
`--price` always overrides. A record without `period_end` warns on every load.

### Where cached records live — project knowledge, not the repo

**The repo is read-only from a session: there are no push credentials.** A record written
to `data/` inside the container dies when the container is reclaimed. So the durable store
is project knowledge, one doc per company per fiscal year at `cache/<TICKER>-<FY>.json`.

At the start of an assessment, if a record exists for the ticker, copy it onto disk:

```bash
mkdir -p data/TICKER && cp <the project doc> data/TICKER/FY.json
```

After reconciling a new company against its filing, write the record **both** to `data/`
(so the run works) and to project knowledge via `project_write` with `local_path` (so the
next session has it). `local_path` uploads straight from disk and costs no context.

Reading one cached record is ~400 tokens against ~6,000 to re-fetch it from EDGAR, so this
pays from the second look at any company onward — and every re-score under SELL.md is a
second look.

**Foreign issuers:** convert at the filing's own convenience rate and say so. It is a
unit conversion, not an FX forecast — but flag that the rate is as of the balance-sheet
date, not today.

## 3. Apply the gates

Any failed gate is a **PASS** regardless of scores. Still complete the scorecard — the
reasoning is the output — but make the gate the headline. Pass `--gate "reason"` to
`score.py` for each.

| # | Gate |
|---|---|
| G1 | **Outside circle of competence** — cannot explain how it durably makes money and what could kill it. |
| G2 | **No profitable track record** — under ~7 years of positive earnings, or pre-profit. |
| G3 | **Turnaround** — earnings collapsed from peak *and the thesis depends on recovery*. NKE failed here at an attractive price. |
| G4 | **Solvency risk** — going-concern language, covenant breach, maturities not covered by cash plus two years of FCF, or interest coverage under 2x outside regulated sectors. |
| G5 | **Serial dilution** — share count up over 3% a year for 5+ years without matching per-share value growth. |
| G6 | **Accounting or governance concerns** — restatements, auditor resignation, regulator investigation into reporting, chronic adjusted-versus-GAAP gaps, related-party transactions. |
| G7 | **Binary outcome** — value hinges on one event: a drug approval, a lawsuit, one contract, or a customer above 30% of revenue. |
| G8 | **Commodity business without a cost advantage** — undifferentiated, not demonstrably the low-cost producer. |
| G9 | **Financial institution** — bank, insurer, or any structure where debt is raw material rather than financing. **Does not force a PASS**; it forces a different valuation. Pass `--substitution`, not `--gate`. |

**Dual-class structures are a warning sign, not a gate** (Management, up to −1 point,
and must be named). They become a gate only where the structure has actually been used to
push through value-destroying allocation. A strict reading refused Meta at the 2022
trough before a sevenfold move — see RATIONALE.md §2.

**Not gates:** high debt at a bank (that is G9's job); capital intensity at a regulated
railroad or utility; a dual-class structure not used against shareholders; earnings
volatility that CAPM does not penalise (that is what G1 is for).

### Warning signs — lower a score, and must be named in the reasoning

**Moat** — gross margin declining 3+ consecutive years · growth bought with rising sales
and marketing as a share of revenue · share defended by price cuts · moat resting on one
patent, licence or contract with a visible expiry · customer concentration 10–30% ·
margin expansion coinciding with antitrust action (a regulator found the mechanism and
removed it, so the margin evidence is unreliable).

**Financials** — ROE high mainly through leverage, check ROIC · FCF persistently below
net income · goodwill above 50% of equity · receivables or inventory growing faster than
revenue · pension or lease obligations large against market cap · cash taxes far below
book taxes for years.

**Management** — buybacks concentrated at share-price highs (most frequently decisive
item here) · acquisition write-downs within three years of the deal · pay linked to
revenue, EBITDA or TSR rather than per-share value · guidance missed 3+ times in five
years · insider selling clusters · three CEOs in ten years · letters that never name a
mistake · dual-class structure.

**Understandability** — business model materially changed in the last five years ·
segments repeatedly reclassified · profits depending on a tax or regulatory arrangement
that one decision could change.

**Price and analyst bias** — base-case growth above 12% for ten years demands
justification, above 15% is a flag on the analyst · a valuation that only works below the
current risk-free rate · marginal ROIC more than ~3pp above the mature WACC · terminal
value above 75% of firm value · sales-to-capital set to the average book ratio where the
input is marginal · R&D amortisation short of `life + 1` years · "this time is different"
reasoning about historical margins · the position is held and the analysis is drifting
toward reassurance.

## 4. Score five dimensions

Whole numbers 1–5. **Every score cites a specific number, trend or management action** —
"strong moat" is not a reason. **When torn between two scores, take the lower one.**

**Understandability (15%)** — not "is it simple" but "is the earnings stream
forecastable".
5 explainable to a teenager, demand structural, ten years out a reasonable extrapolation ·
4 clear model, one or two variables to watch · 3 understandable but earnings depend on
cycles, product refreshes or technology shifts hard to forecast beyond ~5 years ·
2 needs specialist knowledge, product could be obsolete within a decade, erratic earnings
(**floor → PASS**) · 1 cannot explain how it durably makes money, or the industry cannot
honestly be judged (**that is G1**).
Mechanical proxy: a loss year in the last five, or a peak-to-trough earnings swing above 3.0x.

**Moat (35%)** — evidence, not adjectives. First test is pricing power; second is whether
returns on capital stayed high while competitors attacked.
5 demonstrated pricing power, gross margin stable or rising over ten years, top-2 share,
two or more moat sources, ROIC above cost of capital every year · 4 one clear moat source
with evidence, margins stable within a few points across the cycle · 3 contested —
margins drift, share defended by spending, or the moat depends on one factor outside the
company's control · 2 competes mainly on price, low switching costs (**floor → PASS**) ·
1 no advantage, losing share, disruption visible in the numbers.
Common error: confusing size with moat. Check gross margin behaviour, not revenue.

**Financial track record (30%)** — ten years, not one. Consistency beats peaks.
Thresholds: ROIC ≥ 12% (cleaner than ROE ≥ 15%, which leverage manufactures) · net margin
stable or rising, above industry · FCF conversion ≥ 80%, positive every year · long-term
debt repayable from under four years of net income, interest coverage > 6x · capex /
operating cash flow < 30% unless regulated or contracted · share count flat or falling ·
no loss years · over ten years, market value added ≥ earnings retained.
5 essentially all thresholds for ten years · 4 most, one soft spot · 3 profitable
throughout but volatile margins, or ROE carried by leverage, or FCF lagging earnings, or
one loss year with a clear one-off cause · 2 two or more thresholds failed *persistently*
(**floor → PASS**) · 1 losses in multiple years, debt rising faster than earnings, or
serial dilution (**gate**).

**Management & capital allocation (20%)** — judge actions over years, not interviews.
5 long-tenured and owner-minded, buybacks timed well, acquisitions accretive, candid
letters, meaningful insider stake · 4 sensible allocation, no major errors · 3 adequate;
some questionable deals or buybacks at high prices, heavy use of adjusted metrics ·
2 value-destroying acquisitions, persistent dilution, guidance misses, misaligned pay ·
1 accounting or governance concerns, or promotion over performance (**gate**).

## 5. Value the business

```bash
python3 hybrid_valuation.py --from-cache TICKER --price P \
  --growth G --margin-target M --sales-to-capital S \
  --rd-life N --derive-terminal-roic --mos 25
```

Filled from cache: revenue, margin, shares, cash, debt, invested_capital, tax_eff, nol,
price, rf, rd, and the bridge items. **Still yours: `growth`, `margin_target`,
`sales_to_capital`, `rd_life`** — the cache refuses to hold judgment.

Non-negotiables:

- **Two or three risk-free rates, always.** rf is worth +38% to +66%. Use
  `--sweep rf=3.0,4.0,4.79`. For EUR-denominated names the benchmark is the Bund, not
  the Treasury.
- **Never quote the point estimate without the P10–P90 range beside it.**
- **Report the marginal-ROIC spread.** Above ~3pp the reinvestment charge never binds and
  the valuation is an extrapolation of the growth input. Say so.
- **Growth above 12% for ten years needs justification; above 15% is a flag on the analyst.**
- Check the R&D completeness flag. `life` years against a `life`-year life leaves
  amortisation short and biases value **up**. Supply `life + 1` years.
- If the model refuses to value an input (terminal value exceeding firm value), that
  refusal **is** a finding — report it rather than working around it.
- A bank or insurer: stop. G9 fires, the model does not apply. Value on price-to-book
  against its own history plus the buyback signal, label it a documented substitution.

**Output discipline.** The default run already omits the year-by-year build; add `--full`
only when asked for it, since the v2.0 report never prints that table. Use `--brief`
inside sweeps, `--draws 0` when only the point estimate is needed, `--json` only when the
audit trail is actually wanted.

## 6. Compute the verdict

```bash
python3 score.py --understand N --moat N --financials N \
  --management N --value-per-share V --price P [--p90 X] [--oe-yield Y --rf R] \
  [--gate "reason"] [--substitution "…"]
```

The script derives the Price score from price ÷ value/share, then applies caps → gates →
floors → matrix. **Use its output.** If you disagree with the verdict, a score was wrong —
but a score changes only if the cited fact behind it changes.

**Price bands** (against base value/share, anchored so price at the buy-below price
scores 4): ≤ 0.60 → 5 · 0.60–0.75 → 4 · 0.75–1.00 → 3 · 1.00–1.30 → 2 · > 1.30 → 1.

**Caps, applied after the band:** cap at 2 if owner-earnings yield is below the risk-free
rate and base growth is under 8% (being paid less than a bond for more risk) · cap at 1 if
price exceeds the P90 (the point estimate cannot carry a price the optimistic decile does
not reach). Use the risk-free rate matching the currency of the flows.

**Quality** = 0.15·Understandability + 0.35·Moat + 0.30·Financials + 0.20·Management.
Ties round down. ≥ 4.5 exceptional · 4.0–4.4 wonderful · 3.5–3.9 good · 3.0–3.4 fair ·
< 3.0 weak.

**Matrix** — gates first, then floors, then:

| Quality | Price 5 | Price 4 | Price 3 | Price 2 | Price 1 |
|---|---|---|---|---|---|
| ≥ 4.5 | BUY | BUY | BUY if discount ≥ 15%, else WATCH | WATCH | WATCH |
| 4.0–4.4 | BUY | BUY | WATCH | WATCH | WATCH |
| 3.5–3.9 | BUY\* | WATCH | WATCH | PASS | PASS |
| 3.0–3.4 | WATCH | WATCH | PASS | PASS | PASS |
| < 3.0 | PASS | PASS | PASS | PASS | PASS |

\* The cigar-butt buy: a fair business at a wonderful price. Only with a discount ≥ 50%
and a clean balance sheet.

**WATCH always carries a buy-below price. BUY always carries the two or three things that
would break the thesis.**

### Sector adjustments — state when used

**Banks and insurers** — G9. Tangible book or price-to-book against the company's own
history; for insurers judge underwriting first (combined ratio under 100% across a cycle,
reserve development, cost of float); read the buyback signal where policy is to
repurchase only below intrinsic value. BRK.B is the worked precedent.
**REITs** — FFO/AFFO instead of EPS, AFFO payout under 90%, debt/EBITDA under 6x.
Adjacent. Value on AFFO yield against the risk-free rate plus growth.
**Utilities, railroads, pipelines** — accept debt/EBITDA to ~4x where returns are
regulated. Score Financials on ROIC against the allowed return. Moat usually 4, rarely 5,
because regulators cap pricing power.
**Commodity producers** — Moat ≤ 2 unless demonstrably the low-cost producer, evidenced
by position on the cost curve. Mid-cycle earnings, not peak. Net debt/EBITDA under 1.5x.
**Software and platforms** — Adjacent. Score on retention and gross margin. SBC is a real
cost. R&D life 3 years software, 5 years technology hardware. These usually fail on
Price, not on quality.

## 7. Write the assessment — report layout 2.0

**Three sections, always. Same length regardless of verdict. No shortening for a PASS, no
lengthening for a BUY.** The full model output stays available on request; it is not the
default report.

**1. Verdict** — BUY / WATCH / PASS, with rubric version and as-of date. One line stating
why: name the gate or floor if one fired, otherwise the quality/price cell.

**2. Price vs. value** — price today with as-of date · value per share (base case) ·
buy-below at 25% MoS · ratio as "x value".

**3. The five scores** — fixed order: Understandability, Moat, Financials, Management,
Price. One line each: score /5 plus the specific fact behind it. Ends with the quality
average and band.

Standing rules: no comparison to any other stock inside a report, each stands alone · Era
1 and Era 2 numbers never mixed · owner-earnings yield stated against the risk-free rate
even where the DCF disagrees · every number labelled observed or assumed in the underlying
work, though the labels are not printed · if a gate or floor fired and the valuation was
skipped, say so in Section 1 rather than printing a value/share in Section 2 that was
never used · record the rubric version and model inputs so the verdict can be reproduced.

### Standards of honesty

Predictability over story — ten years of margins are evidence, a narrative is not ·
**say "I don't know"**: outside the circle, score Understandability low and say why, that
is the method working · distinguish the Buffett verdict from the general one ("PASS under
this style; the business itself may be fine" is often the honest sentence) · show the
range, never a single number · do not flatter a held position, if it scores 2.4 say 2.4 ·
record wrong calls in FINDINGS.md.

---

## Reference data

**IBKR contract IDs** (a name search costs ~600 tokens, a bare ticker up to 7,000):

```
GOOGL 208813719   META  107113386   AMD   4391        AVGO 313130367
ADBE  265768      ORCL  272800      CIEN  41045553    APP  481863646
TCOM  390332321   LII   6608113     BRK.B 72063691    BABA 166090175
```

**Rates, as of 4 September 2026:** US 10-year 4.79% · Bund 3.34% · ERP 4.5% assumed
(Damodaran's implied was 4.23% at the start of 2026; raised for the mid-2026 oil shock and
inflation) · China CRP 0.6%.

**Conventions:** margin of safety 25% · terminal growth 2.5% capped at rf · R&D life
3 years software, 5 years technology hardware.

## For a German-resident holder

Not in the model, and it should be.

- **Hurdle rate.** Discount USD flows at a USD rate; for EUR names the benchmark is the
  Bund at 3.34%, not the Treasury at 4.79%. That alone moved Carel from an 84% premium to 53%.
- **Withholding.** US 15% with a W-8BEN, fully creditable, no leakage. Switzerland
  withholds 35%, only 15% credits, the rest reclaimed via EStV Formular 85 — that friction
  lands on Belimo, which pays CHF 10 a share. Italy 26% (treaty 15%), Sweden 30% (treaty 15%).
- **Abgeltungsteuer** 26.375% with Soli, ~28% with church tax. Sparer-Pauschbetrag
  €1,000 / €2,000. Share losses only offset share gains.
- **IBKR withholds nothing at source**; everything self-reported via Anlage KAP.
- Not tax advice. Verify with a Steuerberater.

## What to do next

1. Fix the CIEN R&D data (needs FY2020 R&D from the 10-K) and re-pin the baseline,
   recording why the old value was wrong.
2. Set `period_end` on the remaining seeded records (CIEN TTM to 1 Aug 2026, ADBE to
   31 May 2026, APP to 30 Jun 2026, ORCL FY2026 ending May 2026). GOOGL and BABA are done.
3. Implement an EDGAR `companyfacts` provider so §2 stops being manual.
4. Add a financials guard inside `hybrid_valuation.py`. G9 flags the case in the rubric,
   but nothing yet stops the model running on a bank if someone routes around it.
5. Reconcile the remaining seeded records against filings, as GOOGL and BABA now have been.

**Not on this list: more model refinement.** Every structural improvement moves the answer
less than a modest change to the growth assumption.
