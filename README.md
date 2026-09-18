# Buffett verdict + hybrid valuation

Damodaran mechanics, Buffett discipline. Two layers, both reported: the model answers
*what is this worth*, the rubric answers *should I own it*.

## Start a session

```bash
git clone --depth 1 https://github.com/bdwv5rjxzm-code/Value-.git v && cd v
python3 -m pytest test_regression.py test_rubric.py -q
```

Thirty-five tests, about a second. They pin five valuation baselines and the verdict
matrix; a moved number is a finding, not something to update.

## Run an assessment

Read `HANDBOOK.md` — it is the whole operational method: the EDGAR recipe, the gates, the
1–5 anchors, the verdict matrix and the report layout. Read `RATIONALE.md` only when
changing the model or the rubric.

```bash
python3 fundamentals.py show BABA 2026

python3 hybrid_valuation.py --from-cache BABA --price 111.64 \
  --growth 8 --margin-target 12.0 --sales-to-capital 1.5 --rd-life 3 \
  --beta-u 1.20 --crp 0.6 --spread 1.25 --mature-spread 1.0 \
  --derive-terminal-roic --mos 25

python3 score.py --understand 2 --moat 3 --financials 3 \
  --management 3 --value-per-share 88.27 --price 111.64 --p90 106.31
```

## Layout

```
hybrid_valuation.py    the model
score.py               price score, caps, gates, floors, verdict matrix
fundamentals.py        point-in-time observables cache (data/<TICKER>/<FY>.json)
test_regression.py     pinned valuation baselines and guardrails
test_rubric.py         matrix and bands pinned against the assessment register
HANDBOOK.md            operational reference — read every session
RATIONALE.md           why it is shaped this way — read only when changing it
```

## Why this is a repo and not project knowledge

The analysis container starts empty and cannot reach sec.gov, but GitHub is reachable.
Project knowledge can only get code onto disk by passing it through the model's context
and typing it back out, which measured ~32k tokens per session for these four files. A
clone costs about fifty and produces identical output.

**The repo is the source of truth for anything executable.** It is also read-only from a
session — there are no push credentials — so nothing generated during an assessment can be
saved here.

**Project knowledge holds everything that accumulates**: the assessment register, findings,
the sell discipline, and the cached fundamentals at `cache/<TICKER>-<FY>.json`, one small
record per company per fiscal year. A session copies the record it needs onto disk at
`data/<TICKER>/<FY>.json` and writes any new one back to project knowledge before
finishing.
