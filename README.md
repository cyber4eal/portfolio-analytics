# portfolio-analytics

Risk, Monte Carlo and diversification analytics for a portfolio. Ported off the
Google Sheets / Apps Script version, with the parts that only existed to work
around Sheets removed.

Nothing here fetches prices or knows about a broker. Every entry point takes a
returns matrix (dates x tickers) and a weight vector, and returns dataclasses
with an `as_dict()` for the API layer to serialise.

## Modules

| Module | Answers |
| --- | --- |
| `risk` | How volatile is this, what is its beta, what do I lose on a bad day, and which position is actually costing me the risk? |
| `montecarlo` | Where could this end up over 1-10 years, as a fan chart and as horizon box plots? |
| `suggest` | Which holdings are secretly one position, which are inefficient per unit of weight, and what would adding X do? |

## Usage

```python
import pandas as pd
from portfolio_analytics import compute_risk, simulate, redundant_pairs

report = compute_risk(returns, weights, total_value=15_400, benchmark=spy)
print(report.vol_annual, report.var_95_1d)
for position in report.positions[:3]:
    print(position.ticker, f"{position.pct_of_var:.0%} of portfolio risk")

paths = simulate(15_400, report.sharpe * report.vol_annual, report.vol_annual, seed=1)
fan = [band.as_dict() for band in paths.fan]

for pair in redundant_pairs(returns, weights, threshold=0.85):
    print(pair.a, pair.b, round(pair.correlation, 2))
```

## Design notes worth knowing

- **Covariance is EWMA-weighted** (`lambda=0.94`, the RiskMetrics default, about
  an 11-day half-life). An equal-weighted three-year window treats a print from
  2023 as being as informative as yesterday, which is wrong when volatility
  clusters.
- **Both parametric and historical VaR are returned.** They disagree when the
  tails are fat, and that disagreement is the interesting number: if historical
  VaR is much worse than parametric, normality is lying to you.
- **Component VaR sums to total VaR**, so "this position is 55% of my risk" is a
  real statement rather than a ranking by size.
- **Dates are aligned, never array positions.** Holdings on different exchanges
  close on different days. `align_returns` keeps only the dates where every
  series printed; without it a single Xetra holiday turns vol, beta, VaR and
  every position number into `nan`, which then serialises as a bare `NaN` that
  `JSON.parse` rejects.
- **The Monte Carlo median carries the variance drag.** It grows at
  `mu - sigma^2/2`, not at `mu`. At 25% volatility that is over three points a
  year, so a flat-looking median line is usually correct rather than a bug.
- **The fan and the box plots come off one simulation.** The spreadsheet ran it
  twice and the box plot could disagree with the fan at the same horizon.
- **`evaluate_additions` measures before and after on the same dates** - the
  dates the candidate actually traded. Otherwise a fund that only listed in 2023
  looks like a volatility cure purely because 2022 is missing from its half of
  the comparison. Candidates with under 60 overlapping dates are skipped rather
  than reported on thin evidence.

Everything in `suggest` is descriptive, not advice. Correlations measured in
calm markets understate what happens in a crash: the pairs flagged as
diversifying are exactly the ones most likely to converge when it matters.

## Tests

```bash
python3 -m pytest
```

40 tests, no network, no fixtures on disk - the returns matrices are synthetic
with deliberately planted structure, so the assertions are about relationships
(component VaR adds up, a clone gets flagged, higher vol raises P(loss)) rather
than magic numbers.
