# Factor cards

These cards document what each implementation measures, the sign convention, and the most
important falsification tests. Formulas describe the raw signal before optional winsorization,
neutralization, and cross-sectional ranking.

## Microstructure

### F01 — Overnight Tail-Flow Shock

**Results:** [View the empirical report](../results/factor_reports/f01_overnight_tail_flow_shock.md).

**Hypothesis.** A large market-adjusted overnight gap followed by unusually concentrated,
one-sided closing-hour trading may represent temporary pressure rather than durable information.

For stock `i` and day `t`:

```text
overnight(i,t) = open(i,t) / close(i,t-1) - 1
residual(i,t)  = residual from overnight(i,t) ~ market_overnight(t)
imbalance(i,t) = (tail_buy - tail_sell) / (tail_buy + tail_sell)
spike(i,t)     = tail_volume / (daily_volume / 4)
raw(i)         = -mean_t(|residual| × |imbalance| × spike)
```

The implementation removes the final cross-sectional exposure to 20-day momentum.

**Required checks.** The five-observation residual regression is intentionally short and can be
noisy. Compare against direct market subtraction, longer windows, signed imbalance, and separate
positive/negative gap legs.

### F02 — Open/Close Volume Concentration

**Results:** [View the empirical report](../results/factor_reports/f02_open_close_volume_concentration.md).

**Hypothesis.** Stocks whose activity is persistently concentrated at session boundaries may be
more exposed to crowded attention, liquidity demand, and temporary price pressure.

```text
open_share(t)  = first_15m_volume / daily_volume
close_share(t) = last_15m_volume / daily_volume
raw            = -[0.5 × mean(open_share) + 0.5 × mean(close_share)]
```

**Required checks.** Treat opening/closing auctions consistently, vary the 15-minute window, and
test the open and close components independently.

### F03 — Retail Flow Synchronization

**Results:** [View the empirical report](../results/factor_reports/f03_retail_flow_synchronization.md).

**Hypothesis.** Small-trade flow that closely follows lagged price changes is consistent with
extrapolative demand and may predict reversal.

```text
retail_strength(t) = (small_buy_value - small_sell_value)
                     / (small_buy_value + small_sell_value)
raw = -corr_time(rank(return(t-1)), rank(retail_strength(t)))
```

**Required checks.** “Retail” is a trade-size proxy, not investor identity. Vary the trade-size
threshold and require adequate five-minute coverage before daily aggregation.

### F04 — Institutional Tail Acceleration

**Results:** [View the empirical report](../results/factor_reports/f04_institutional_tail_acceleration.md).

**Hypothesis.** Persistent large-trade net buying contains information, while the change from
morning to closing-session pressure adds a timing dimension.

```text
base = sum_t(buy - sell) / sum_t(|buy - sell|)
tail = closing_flow_strength - morning_flow_strength
raw  = 0.65 × zscore(residualized_base)
     + 0.35 × zscore(-orthogonalized_tail)
```

The base component is residualized against momentum, size, liquidity, and volatility. The tail
component additionally controls for the two full-session flow measures.

**Required checks.** “Institutional” is also a trade-size proxy. Report results before and after
orthogonalization because aggressive residualization can remove economically meaningful signal.

### F05 — VWAP–Flow Divergence

**Results:** [View the empirical report](../results/factor_reports/f05_vwap_flow_divergence.md).

**Hypothesis.** The relationship between early-session price formation and directional order
flow can identify confirmation versus divergence regimes.

```text
raw = corr_time(early_30m_vwap, morning_buy_ratio)
      × sign(mean(morning_buy_ratio - morning_sell_ratio))
```

**Required checks.** Correlating a price level with a flow ratio can accidentally capture price
trend or scale. A stronger robustness specification replaces VWAP level with its deviation from
the open, previous close, or full-day VWAP.

## Risk

### F06 — Mixed-Horizon Idiosyncratic Skewness

**Results:** [View the empirical report](../results/factor_reports/f06_mixed_horizon_idiosyncratic_skewness.md).

**Hypothesis.** Investors may overpay for lottery-like positive residual skewness, producing a
negative relationship between idiosyncratic skewness and subsequent returns.

```text
beta(i)     = corr_750d(r_i, r_m) × std_252d(r_i) / std_252d(r_m)
residual(t) = r_i(t) - beta(i) × r_m(t)
raw(i)      = -skew_21d(residual)
```

The mixed-horizon beta is inspired by Frazzini–Pedersen estimation, but it is **not** a complete
replication of their BAB beta because this implementation does not apply beta shrinkage.

**Required checks.** Compare equal-weighted and investable-cap-weighted market returns, beta
shrinkage, weekly-return correlation, and minimum-history filters.

## Trading activity

### F07 — Dollar-Volume Stability

**Results:** [View the empirical report](../results/factor_reports/f07_dollar_volume_stability.md).

**Hypothesis.** Stable traded value reflects a more persistent liquidity regime, while highly
variable traded value may capture event-driven attention or speculative participation.

```text
raw = -std_126d(traded_value) / mean_126d(traded_value)
```

**Required checks.** Compare raw versus log traded value, remove market-wide activity shocks,
and inspect size and liquidity exposure.

### F08 — Turnover Stability

**Results:** [View the empirical report](../results/factor_reports/f08_turnover_stability.md).

**Hypothesis.** Stable share turnover contains information distinct from the stability of dollar
volume.

```text
turnover(t) = volume(t) / float_shares(t)
base        = -std_75d(turnover) / mean_75d(turnover)
proxy       = -std_75d(traded_value) / mean_75d(traded_value)
raw         = 0.75 × rank(base) + 0.25 × rank(residual(base ~ proxy))
```

**Required checks.** The original research class name referenced 126 days while its implemented
parameter was 75 days. This public version reports the actual 75-day horizon. Test both horizons
out of sample rather than selecting between them on full-sample IC.

## Fundamental

### F09 — Composite Quality

**Results:** [View the empirical report](../results/factor_reports/f09_composite_quality.md).

**Hypothesis.** Profitable, cash-generative, operationally efficient, conservatively financed
companies with improving growth should earn a quality premium.

Five equally weighted dimensions are built from cross-sectional percentile ranks:

1. Profitability: cash flow on assets, return on equity, and operating return on capital.
2. Growth: year-over-year acceleration in net income, operating profit, and revenue.
3. Earnings quality: cash accrual quality, cash-flow coverage of profit, and low receivables.
4. Operating efficiency: asset, inventory, and receivables turnover.
5. Safety: low leverage, interest coverage, and current ratio.

The prior implementation reserved a sixth corporate-governance dimension but had no governance
data. This public version omits the constant placeholder instead of presenting it as measured.

**Required checks.** Use original announcement dates, preserve statement vintages, distinguish
year-to-date from single-quarter flows, and compare results within industries.
