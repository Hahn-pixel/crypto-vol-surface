# Case studies: testing trading hypotheses

Two research projects that complement [Vol-Edge](../README.md). Together the three
projects tested **15 hypotheses and closed all 15**, each with a criterion fixed
before the run, with controls, and with a numeric distance to breakeven.

| project | market | hypotheses | key finding |
|---|---|---|---|
| [Vol-Edge](../README.md) | Deribit and Derive options, BTC/ETH | 2 | spread reversion is real (n_eff ~17), but fees of ~1.05 vp eat ~0.55 vp of effect |
| [Funding-Edge](funding-edge.md) | perpetuals on 5 exchanges, incl. equity perps | 9 | measured costs 3× the assumed ones; selection leakage measured with a placebo population |
| [Trend-Edge](trend-edge.md) | Binance spot, altcoins 2020–2026 | 4 | best in-sample window (t −3.38) gave t 0.40 out of sample |

Code is published for Vol-Edge only; the other two are documented as case studies.

## Common backtest errors found and measured here

- costs taken from a config instead of measured (understated 3–10×);
- assets selected on the same out-of-sample data the verdict is made on;
- survivorship and spliced tickers (a fake gain of 171,116×);
- look-ahead inside one's own infrastructure;
- a trailing stop that produces a positive mean on edge-free synthetic data;
- the best of N parameter variants reported as a finding.
