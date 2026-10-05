# Case study: momentum in Binance altcoins — four hypotheses, no edge

**Market:** Binance spot, USDT pairs, 1h/4h OHLCV.
**Data:** 2020–2026, including delisted coins.
**Stack:** Python, pandas. **Result:** no simple momentum edge — neither
directional, nor cross-sectional, nor market-neutral.

---

## The question

Can you make money by entering strong altcoin moves: into a trend, after a pump,
into momentum leaders, or against them?

## Data: what had to be fixed before any test

**No survivorship bias.** Full Binance history including delisted coins: 664
symbols with data, 171 of them delisted (25.8%), ~12.9M rows for 2023–2026. A
separate 2020–2023 set (422 symbols, 85 delisted) was reserved as out-of-sample and
not opened until the criterion was fixed.

**Spliced tickers.** One ticker can carry two different coins. LUNAUSDT: old LUNA
until May 2022, then LUNA 2.0 — the splice creates a fake gain of **171,116×**.
There are 7 such symbols in 2020–2023 and 3 in 2023–2026. Any momentum signal turns
them into "leaders". The cleaning rule (gap > 3 days **and** price jump > 3×) was
fixed **before** looking at the data and applied in the shared loader. Jumps without
a trading gap (the LUNA crash, real squeezes) are left untouched.

**Look-ahead in our own infrastructure.** Liquidity on the rebalance date included
volume of the rebalance day itself. Membership was moved to the previous day.

**Point-in-time universe:** top 60 by 30-day median volume, $2M floor.

## Method

- the success criterion (signal, horizon, t threshold, years) is fixed **before**
  the run;
- before every run: a synthetic series with a planted edge (must be found) and a
  null one (must not);
- clustered block bootstrap: altcoins correlate at 0.7–0.9, so a naive t on 460k
  observations gives t = 15 out of nothing;
- the control is random entries with **the same** exit rules; two controls at once:
  same symbol at another time, and same time on other coins;
- cuts chosen after looking at the data are tagged `[EXPLORATORY]` and give no
  verdict.

## Hypotheses

**#1. Entering a strong trend — FAIL.** 4,187 trades, 8 exit rules. Best t against
the random control: 1.25. A continuation effect exists but lasts ~1 day; by the
time the signal fires, the trend has already happened. Trailing stops lose money on
both the signal and the control.

**#2. Entering after a pump — FAIL.** 1,982 events, 240 symbols. 75% of pumps are
outside the liquid universe. Edge against the controls t = 2.40 and 2.88 with a 2.5
threshold; median trade −1.23%; in 2026 the edge is negative against both controls.
A borderline FAIL is a FAIL.

**#3. Cross-sectional momentum — FAIL.** 181 weeks. Edge −0.39%/week, t = −1.21;
worse every year: 2023 −0.00%, 2024 −0.07%, 2025 −0.41%, 2026 −1.21%. The signal sits
at the 10th percentile of 200 random portfolios.

**H4. Market-neutral short of leaders — FAIL out of sample.** An exploratory cut
of #3 found a 56-day window with t = −3.38. That was the best of four windows on the
same data, so it was tested **only** on the unopened 2020–2022 set, with a
specification fixed before the data was downloaded. Result: +0.25%/week,
**t = 0.40**. Neighbouring windows change sign smoothly (48d −0.06%, 64d +0.43%) —
a sign of noise.

## Lessons

- **The best of N variants on the same data is overfitting;** divide t by roughly
  √N. Test only on data you have not seen.
- **A control with the same exits is mandatory:** a trailing stop by itself creates
  a positive mean on trending synthetic data.
- **Splitting into "survivors" and "dropouts" by future membership is a tautology.**
  The right measure of survivorship is comparing aggregates on datasets with and
  without delistings. Here it was almost zero: the liquidity filter drops a coin
  before it gets delisted.
- **Run the non-overlapping test over all phases of the calendar grid,** judge by
  the median. A single phase is a lottery.
- **The most recent year matters most:** an effect that vanished in the last year is
  not alive.

## What this does NOT mean

- Nothing about other data (order book, on-chain, funding) or about perpetuals as a
  separate class.
- Nothing about capital larger than retail — although capacity was not binding here.
- In H4 the perp price was replaced by spot and funding was ignored (accepted in
  advance).
