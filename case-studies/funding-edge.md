# Case study: funding-rate arbitrage in crypto perpetuals — nine hypotheses, nine closures

**Market:** perpetual futures on Binance, Bybit, Gate, WhiteBIT, Hyperliquid;
non-crypto perpetuals on WhiteBIT (equities, ETFs, metals, energy).
**Period:** August–September 2026. **Stack:** Python, pandas.
**Result:** no tradable edge for retail capital. Every closure comes with a numeric
distance to breakeven, not just a sign.

---

## The question

Funding rates on perpetuals have stayed positive for years. Hence a popular idea:
long spot, short the perp, and collect funding as "almost risk-free" income. Other
variants of the same idea: cross-exchange funding spreads, collecting funding over
weekends, perpetuals on equities that trade around the clock while the underlying
market is closed.

The project's question: **does anything remain after real execution costs and the
cost of capital?**

## What was done

**1. Costs were measured, not taken from the config.** 1,188 order-book snapshots
across 43 coins on three exchanges. Breakeven at $1k turned out to be **0.663%**
instead of the 0.224% assumed in the config — three times higher. At $20k: 3.868%.
Slippage in the config was understated by a factor of 4–10.

**2. A DP oracle as a ceiling.** Instead of tuning a trading rule, the project
computed the exact maximum profit under **any** policy (dynamic programming,
positions {−1, 0, +1}). If an oracle that knows the future earns zero, there is no
rule to look for. This closed the WhiteBIT ↔ Hyperliquid cross-exchange scheme: the
ceiling was zero under every position limit.

The oracle is paired with null models. Permuting the runs keeps the mean and removes
timing. In one scheme the oracle earned +7.71% and the permuted runs +7.79% — the
"profit" came from amplitude and bias, not from when to enter.

**3. Selection leakage was found and measured.** Coins for cost measurement had
first been selected by return on the same out-of-sample half on which the verdict
was then made. This falsely reopened two closed hypotheses. Everything was
re-measured with blind selection, plus a control population with deliberate leakage
(placebo):

```
                 blind selection   actual leakage   deliberate leakage
ceiling, B3          +0.106            +0.231            +0.554
ceiling, B4          +0.320            +0.458            +0.765
```

The price of peeking is measured. No closure reopened under blind selection.

**4. Verdicts with a margin, not a sign.** Each hypothesis reports `be*` — the cost
level at which the causal rule breaks even out of sample. For the cross-exchange
spread: `be*` of the rule **0.53–0.57%** against measured costs of **1.47–1.58%**.
The rule is non-positive even with free capital.

**5. Equity perpetuals: closed by sign.** While the stock exchange is closed, the
index is frozen and the perp keeps trading. Hypothesis: the deviation reverts after
the open. Across 5,965 closed intervals (93 symbols) the relationship turned out to
be **the opposite**: Spearman +0.0805, CI95 [+0.053, +0.106]. What drifted overnight
keeps moving the same way after the open. Trading against the drift loses money
before costs.

## Summary

| hypothesis | reason for closure |
|---|---|
| spot + perp on Binance (carry) | funding + base ≈ 10.95% p.a. does not cover costs and 6% cost of capital; blind OOS −0.033 |
| Bitget ↔ Gate | small exchanges are synchronized |
| large ↔ small exchange spread | `be*` 0.57% < costs 0.663% |
| spread against WhiteBIT | `be*` 0.53–0.57% vs `be` 1.47–1.58%, measured per coin |
| cross-sectional neutrality on WhiteBIT | per-leg cost above breakeven in every session |
| static short perp | the signal is real but smaller than costs + capital |
| weekend funding collection | requires funding ≈5.2× the base rate every weekend |
| WhiteBIT ↔ Hyperliquid | DP ceiling = 0 |
| perp drift reversion on equities | sign opposite to the hypothesis |

## Lessons

- **Measure costs before statistics, at the trading frequency of the strategy.**
- **A ceiling and tradability are different questions.** A spread can be large and
  still unreachable without knowing the future: in one scheme the oracle's `be*` was
  4.9% and the rule's 0.54%.
- **Population selection must be blind to out-of-sample data — including selection
  for cost measurement.** The price of leakage can be measured with a control
  population.
- **A lower bound on costs can close a hypothesis but cannot open one.**
- **Every new metric first runs on a synthetic series with a known answer.** This is
  how a wrong definition of `be*` was caught: if the rule's parameter is re-chosen
  for every cost level, the function becomes non-monotonic (17 violations out of 40)
  and bisection returns an arbitrary number.
- **Verdict order: sign → gross result → ceiling → `be*`.** Stopping at any step
  closes the subject.

## What this does NOT mean

- Nothing about large capital or market makers with lower fees.
- Depth on Hyperliquid and on non-crypto WhiteBIT segments was not measured; it can
  only worsen verdicts that are already CLOSED.
- Non-crypto perpetuals are less than 4 months old; conclusions about them are
  limited by the age of the population.
