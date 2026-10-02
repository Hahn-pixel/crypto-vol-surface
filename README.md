# Vol-Edge

A research pipeline for the implied volatility surface of BTC and ETH
options on Deribit, with a cross-venue comparison against the on-chain
options exchange Derive. Python standard library only, no API keys.

The project tested three hypotheses about options-market inefficiency.
One turned out statistically real but not tradable; one was false; one
was never opened. The value of this repository is not a signal — it is
**how** those verdicts were reached and why they can be trusted.

> Inline comments are in Ukrainian. Every module has an English header;
> the methodology is documented below. Українська версія: [README_uk.md](README_uk.md).

---

## What the pipeline does

```
Deribit REST (public)
   │
   ▼
data/deribit_chain.py ── quote filters ── synthetic forward (put-call parity)
   │                                       │
   │                IV bid/mid/ask via an in-house Black-76 solver
   ▼
Per-expiry SVI fit (raw SVI, Zeliade) + Durrleman and calendar checks
   │
   ▼
JSON artifact: parameters, RR25/BF25 in premium-adjusted deltas, quotes
   │
   ├─► analytics/surface_report.py   constant tenors 7/30/90d, DVOL
   ├─► analytics/rv_rolling.py       realized volatility on a 5/30-min grid
   ├─► analytics/anomaly_detect.py   rank-based anomaly detection + debounce
   └─► analytics/defi_eval.py        Derive vs the Deribit surface
```

The pipeline ran unattended for 9 weeks (cron, six snapshots a day,
UTC schedule, flock, retention).

## Key technical decisions

- **Inverse options.** Deribit quotes premium in coin. The
  premium-adjusted delta, `(K/F)·N(d2)`, is non-monotonic — the delta
  solver takes the OTM branch to the right of the peak.
- **Forward** is synthetic from put-call parity (median of three pairs
  around ATM), not the future and not the index.
- **The model is valid only where there is data.** Outside the quoted
  log-moneyness range (+10% padding) the code refuses explicitly with a
  counter instead of extrapolating. This single rule removed the
  Durrleman/calendar violations and the exploding RR25 values.
- **Fit degradation ladder:** FULL_SVI → FIXED_S → PARABOLA → FLAT,
  every step counted.
- **RV on a 30-minute grid.** Daily close-to-close systematically
  understated RV: an IV30/RV30 ratio of "1.32" turned out to be a
  sampling artifact; the actual value is ~1.01–1.07.

## Methodology

Every hypothesis went through the same protocol:

1. **Pre-registration.** PASS/FAIL thresholds are written down before
   the first look at the data and are not changed afterwards — even
   when the result is unwelcome.
2. **Empirical null, not theoretical.** Under the null, an estimator
   rarely returns zero. Example: OLS on a near-unit-root series "finds"
   mean reversion with a ~4-day half-life in a pure random walk; the
   criterion "the confidence interval excludes zero" gave 58% false
   PASSes. Verdicts are therefore judged only against a null
   distribution simulated for our sample size and structure.
3. **Two controls in every run.** A negative control (no effect — the
   instrument must stay silent) and a positive control (synthetic data
   with a known effect — the instrument must detect it). A negative
   control alone is not enough: it cannot tell "no effect" from "blind
   instrument".
4. **Effective sample size.** Series autocorrelation is 0.90–0.99:
   350 snapshots carry ~17 independent observations. Confidence
   intervals use a block bootstrap with block length derived from the
   measured n_eff.
5. **Cost in the units of the effect — before any statistics.** Fees
   converted to vol points: `fee_vp ≈ f / (0.00399·√τ)`; at a 0.03% fee
   this is `0.0752/√τ` per leg (0.26 vp at 30 days). If the expected
   effect at a realistic entry does not exceed the round-trip cost, no
   statistics are needed.
6. **The verdict is a pure function with tests** for branch priority.
   The logic deciding PASS/FAIL cannot change unnoticed.

### Error classes documented in the project

The full log has 11 classes. The most useful:

- **An estimator under the null does not return zero** (see item 2).
- **Direction-blind rules produce verdicts opposite to the data** —
  decision branches are written by sign.
- **A statistic is silent about what cancels out of it.** A beta
  computed within a time-of-day slot cancels additive seasonality
  exactly — so a control built from a sine wave would "pass" every
  time.
- **Success and failure print the same text.** Four times in one
  session broken code returned exit=0 with no zero anywhere. The fix:
  zero output on non-zero input raises an exception rather than being
  reported as a result; logs print the outcome of an operation (the
  absolute path), not the intent.
- **A statistical PASS is not tradability** (hypothesis A below).

## Verdicts

| # | hypothesis | verdict |
|---|---|---|
| A | mean reversion of the ETH−BTC ATM30 cross spread in vol points | statistics **PASS**; **not tradable** |
| DEFI | Derive option prices vs the Deribit surface, static pair | **FAIL** (both thresholds) |
| B | Binance Dual Investment retail product vs the market | not opened |

### A: the reversion is real, the money is not

The spread `ETH.atm30 − BTC.atm30` cancels the common crypto factor and
mean-reverts with a half-life of ~1.2–1.5 days (p = 0.0038 against a
lag-matched null; power 0.985; the effect is not seasonal and belongs
to the spread itself, not to its legs).

But an "entry + exit, two legs" trade is four transactions, i.e.
~1.05 vp of fees per round trip. The expected reversion from a
one-standard-deviation move is ~0.55 vp. Arithmetic available on day
one closed what three steps of statistics had proven.

**Not proven:** independent replication (the re-measurement is
cumulative); n_eff ~17 is the weakest number in the case.

### DEFI: Derive quotes within the cost of arbitrage

23 synchronized snapshot pairs over 5 days, 8,538 exact matches
(expiry, strike, type) with no interpolation. Derive IV is computed on
Derive's own forward. Share of instruments where the IV difference
exceeds the round-trip fees of a static pair: **median 0.000 in every
tenor bucket**. Both controls passed.

**What the verdict does not cover:** expiries ≤ 3 days, where the
positive control showed the instrument cannot see even a 3 vp effect;
market-making strategies; other market regimes (5 days is one regime).

## A control at work: the USD convention bug

The negative control of the DEFI evaluator runs Deribit quotes through
the Derive code path — against themselves. The result must be zero.

The first run produced **84 "tradable" instruments** and a 0.115 vp
offset. Cause: in `deribit_chain.py` the premium of an inverse option
was converted to USD via the **spot** (`coin · index`), while IV was
solved on the **forward**. For Black-76 the correct value is `coin · F`.
The offset reaches −2.4 vp on long expiries, where the basis is
largest. After the fix in the evaluator: 0 tradable, offset 0.0.

The convention in `deribit_chain.py` itself was deliberately left
unchanged: fixing it breaks the continuity of the historical series and
requires a convention-version field in the artifact. It is documented
as an open issue.

## Modules

| module | purpose | self-tests |
|---|---|---|
| core/black76_greeks.py | Black-76, IV solver, Greeks (USD / coin / PA) | 872 |
| core/svi_fit.py | raw SVI (Zeliade), Durrleman, calendar | 30 |
| core/svi_jw.py | raw ↔ jump-wings | 15 |
| core/env_flags.py | explicit environment-flag handling | 13 |
| data/deribit_chain.py | chain snapshot → SVI per expiry | 58 |
| analytics/surface_report.py | constant tenors, RR/BF, DVOL | 18 |
| analytics/smile_report.py | HTML smile report | 20 |
| analytics/rv_intraday.py, rv_rolling.py | 5-min RV, HAR-RV, rolling window | 19 / 27 |
| analytics/anomaly_detect.py | rank-based detection + debounce | 43 |
| analytics/anomaly_calibrate.py, dvol_drift.py, tenor_nodes.py | calibration, diagnostics | 141 / 126 / 68 |
| analytics/spread_neff.py, spread_ou.py, spread_slot.py | hypothesis A, steps 1–3 | 68 / 84 / 114 |
| analytics/defi_flow.py, defi_grid.py, defi_collect.py | Derive: flow, grid, collection | 67 / 44 / 109 |
| analytics/defi_fee_probe.py | Derive fee schedule measured from the API | — |
| analytics/defi_eval.py | DEFI evaluator + verdict | 36 |
| deploy/*.sh | cron with UTC schedule, flock, retention | — |

## Running

Only Python 3 (stdlib) is required. Each script runs on double-click:
offline self-tests first, then the live section; the window stays open
until Enter is pressed.

```bash
# self-tests only, no network
VOLEDGE_OFFLINE=1 python3 data/deribit_chain.py

# live BTC snapshot without the second order-book depth pass
VOLEDGE_CURRENCIES=BTC VOLEDGE_CHAIN_DEPTH=0 python3 data/deribit_chain.py
```

The artifact is written to `artifacts/chain/`. Every `VOLEDGE_*` flag
is printed with its value; an unknown flag (e.g. a typo) produces
`[ENV-WARN]` rather than a silent default.

The cron wrappers in `deploy/` need the executable bit after cloning
(it is not preserved by the web upload):

```bash
chmod +x deploy/*.sh
```

## Limitations

- Public endpoints only; surface history exists only from the start of
  collection (July 2026).
- Fees were measured at the retail tier; for accounts with lower rates
  verdict A must be recomputed (fees in vol points scale linearly).
- This is a research tool, not a trading system and not investment
  advice.

## Author

Dmytro Govor ([profile link]). Available for fixed-price asynchronous projects:
volatility surfaces, Greeks, derivatives data pipelines, backtest
audits. Written communication only.
