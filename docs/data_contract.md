# Data contract

The package intentionally uses semantic public names instead of database-specific identifiers.
A custom adapter maps the user's data source into the following arrays.

## Daily fields

All daily fields have shape `time × assets`, oldest to newest.

| Name | Meaning | Timing requirement |
|---|---|---|
| `adj_open` | Split/dividend-adjusted open | Known after the open |
| `adj_close` | Split/dividend-adjusted close | Known after the close |
| `daily_return` | Close-to-close simple return | Known after the close |
| `market_cap` | Float or investable market capitalization | Point-in-time |
| `volume` | Shares traded | Known after the close |
| `float_shares` | Tradable shares outstanding | Point-in-time |
| `traded_value` | Daily traded monetary value | Known after the close |
| `early_vwap_30m` | VWAP during the first 30 minutes | Known after minute 30 |
| `morning_active_buy_ratio` | Morning active-buy volume / total volume | Known after the window |
| `morning_active_sell_ratio` | Morning active-sell volume / total volume | Known after the window |

## Intraday fields

Intraday arrays have shape `days × bars × assets`. Session alignment, auction handling, and
missing-bar policy belong in the adapter.

| Name | Frequency | Meaning |
|---|---:|---|
| `active_buy_volume_1m` | 1 minute | Buyer-initiated share volume |
| `active_sell_volume_1m` | 1 minute | Seller-initiated share volume |
| `volume_1m` | 1 minute | Total share volume |
| `retail_active_buy_value_5m` | 5 minutes | Small-trade buyer-initiated value |
| `retail_active_sell_value_5m` | 5 minutes | Small-trade seller-initiated value |
| `institution_active_buy_value_5m` | 5 minutes | Large-trade buyer-initiated value |
| `institution_active_sell_value_5m` | 5 minutes | Large-trade seller-initiated value |

The labels “retail” and “institution” are proxies based on a documented trade-size rule; they
must not be presented as observed investor identities.

## Fundamentals

Fundamental arrays have shape `periods × assets`. Each row must enter the research dataset on
the original public announcement date. Restatements require an explicit vintage policy.

The quality factor expects income-statement, cash-flow, and balance-sheet fields listed in
`CompositeQuality.compute`. It assumes quarterly observations ordered oldest to newest and uses
same-quarter year-over-year lags.
