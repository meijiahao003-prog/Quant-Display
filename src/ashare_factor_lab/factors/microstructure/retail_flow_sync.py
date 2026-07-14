"""Retail flow and lagged-return synchronization factor."""

from __future__ import annotations

import numpy as np

from ...data import DataProvider
from ...operators import (
    intraday_sum,
    purify,
    safe_divide,
    ts_corr,
    ts_percentile_rank_full,
    ts_shift,
)
from ..base import Factor, FactorMetadata


class RetailFlowSynchronization(Factor):
    metadata = FactorMetadata(
        factor_id="F03",
        name="retail_flow_synchronization",
        category="microstructure",
        direction="negative",
        description="Time-series rank correlation between lagged returns and retail net-flow strength.",
    )

    lookback_days = 20

    def compute(self, data: DataProvider) -> np.ndarray:
        n_days = self.lookback_days + 1
        returns = data.daily("daily_return", n_days)
        buy = data.intraday("retail_active_buy_value_5m", n_days)
        sell = data.intraday("retail_active_sell_value_5m", n_days)
        buy_daily = intraday_sum(buy)
        sell_daily = intraday_sum(sell)
        net_strength = safe_divide(buy_daily - sell_daily, buy_daily + sell_daily)

        lagged_return_rank = ts_percentile_rank_full(ts_shift(returns, 1))
        flow_rank = ts_percentile_rank_full(net_strength)
        synchronization = ts_corr(lagged_return_rank, flow_rank)
        return purify(-np.nan_to_num(synchronization, nan=0.0))
