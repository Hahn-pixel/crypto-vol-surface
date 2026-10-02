# The reversion is real, the money is not: how a statistically genuine effect turned out untradable

*Vol-Edge, hypothesis A. All numbers come from live measurements, July–September 2026.*

A month of work, three steps of statistics, both controls at every
step — and the effect is genuine. After that, a simple fee calculation
showed that at the retail fee tier it cannot be traded profitably. This
article is about how that happened and why the order of these two
checks should be reversed.

## The idea

BTC and ETH implied volatilities move together: both react to the same
crypto market. Subtract one from the other and the common factor
cancels; the remainder — the spread — may behave differently from
either leg. In particular, it may revert to its mean.

Subject: `spread = ETH.atm30 − BTC.atm30`, the difference between the
30-day ATM volatilities, in volatility points (vp). Six Deribit surface
snapshots a day, ~350 snapshots over 58 days.

## Step 1: does the spread carry more information than its legs?

The first question is not "does it revert" but "how many independent
observations does the series contain". Volatility is highly persistent:
the autocorrelation between neighbouring snapshots is 0.97–0.98. Hence
the effective sample size:

| series | r (lag 1) | n_eff |
|---|---|---|
| BTC.atm30 | 0.967 | 5.9 |
| ETH.atm30 | 0.977 | 4.1 |
| spread | 0.909 | **16.7** |

The spread does cancel the common factor — it carries three times more
independent information. But 351 snapshots give **seventeen**
independent observations, not 351. That number stays with the
hypothesis to the end as its weakest link.

The test was run not against the "theoretical" ratio of 1.0 but against
a null simulated for our sample size: on noise, the estimate
`n_eff(spread) / max(n_eff of legs)` is ~0.70–0.76 rather than 1.0,
because the maximum of two noisy quantities systematically inflates the
denominator.

## Step 2: mean reversion and the Dickey-Fuller trap

Model: `dx[t] = α + β·x[t−1]`, half-life from β. Here is the first
trap. OLS on a near-unit-root series biases β downwards — and "finds"
reversion where there is none. On a pure random walk of our length, the
criterion "the confidence interval for β excludes zero" produced **58%
false PASSes** and manufactured a half-life of ~4 days.

So the p-value was taken only from an empirical null: thousands of
random walks of the same length, the same estimator. Result on 350
snapshots:

- β = −0.091, p = 0.002 against a null whose median is −0.012;
- half-life 1.2 days, 95% CI [0.6; 3.7] days;
- negative control (random walk) — 3% false positives at a 10% threshold.

## Step 3: is it an intraday artifact?

A half-life of about a day is suspicious: it is exactly six steps of the
snapshot grid. Daily seasonality (Asian/US sessions) could mimic
reversion.

Two separate statistics with separate positive controls:

1. Does the strength of reversion depend on the time of day
   (permutation test across slots)? p = 0.24 — no.
2. β at lag 6 (exactly one day), where seasonality cancels.

The second statistic has a non-obvious property: a β computed within a
time-of-day slot cancels additive daily seasonality **exactly**. A
"random walk plus sine wave" control gives p = 1.0 on it. Good for the
measurement, fatal for the control: a sine-wave control would "pass"
every time. Hence the rule: before choosing a statistic, ask what
cancels out of it.

### A pause that turned out to be right

The first run of step 3 (159 snapshots, August) gave β₆ = −0.369 with
p = 0.116 — not a pass. The test's power at its own estimated horizon
was only ~0.47: "not seen" did not mean "not there".

The threshold was not moved. Instead, a power table was computed in
advance: at n = 318, 0.97 was expected. A month later, on 350
snapshots:

- β₆ = −0.363 (the point estimate barely changed);
- p = 0.0038, power 0.985;
- all six per-slot β₆ are negative; the legs alone show no reversion.

The effect is not seasonal and belongs to the spread itself.
Statistically, hypothesis A is a PASS.

An honest correction: lag 1 reverts faster than lag 6 implies. That is
how white measurement noise (SVI fit error) behaves — it shortens the
short horizon more. Part of the "reversion" is noise that cannot be
traded; for trading purposes a half-life of ~1.5 days is the more
honest estimate.

## Step 4: the napkin

Now — what does it cost to try?

Deribit charges 0.03% of the underlying notional per option. To compare
this with the effect, convert it into the same units. The vega of an
ATM option per 1 vp is ≈ φ(0)·√τ·0.01·F = 0.00399·√τ·F, so

```
fee_vp = 0.0003 / (0.00399·√τ) = 0.0752 / √τ
```

Price and volatility level cancel: the number depends only on the fee
rate and the tenor. At 30 days — **0.26 vp per leg, per side**.

A reversion trade is an entry and an exit, two legs each (ETH and BTC
are two different order books, so no combo discount applies): **four
transactions, 1.05 vp**.

The effect: the standard deviation of the spread is 1.11 vp. An entry
at a one-sigma deviation yields ~0.55 vp of movement over a ~1.5-day
half-life.

| entry | move over one half-life | full reversion |
|---|---|---|
| 1 σ (1.11 vp) | 0.55 vp | 1.11 vp |
| 2 σ | 1.11 vp | 2.22 vp |

One sigma does not even cover the fees. Two sigma break even before
the bid-ask spread, and with n_eff ~17 such deviations occur only a
handful of times in two months. The sigma itself is inflated by fit
noise.

Verdict: **the effect is real and untradable** at the retail fee tier.

## Takeaways

**Cost in the units of the effect — before the first statistical
step.** The formula above needs no snapshot at all: the fee rate, the
tenor and the rough scale of the effect were known on day one. Had this
calculation come first, the tradability decision would have taken an
hour, and the statistics would have answered a different question —
whether it is worth looking for cheaper market access.

**The number of transactions per cycle matters as much as the tenor.**
A reversion trade is four transactions; a static pair held to expiry is
two. This sorts hypotheses before any data. The project's next
hypothesis (Derive vs Deribit) was chosen on exactly this criterion.

**The statistics were not wasted.** They answered questions the napkin
cannot: the effect is not seasonal, belongs to the spread, and has a
half-life of about a day and a half. If the fee rate is substantially
lower (fees in vol points scale linearly), the answer to "is there
something to trade" is already in hand. But the order should be
reversed.

**Not proven:** independent replication — the re-measurement is
cumulative, the first 159 snapshots are part of the sample; seventeen
independent observations are too few for any confidence about the
future.

---

Code for all steps: `analytics/spread_neff.py`, `spread_ou.py`,
`spread_slot.py` in the
[crypto-vol-surface](https://github.com/Hahn-pixel/crypto-vol-surface)
repository.
