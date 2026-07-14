"""Overnight residual shock combined with closing-hour order-flow pressure."""

from __future__ import annotations

import numpy as np

from ..data import DataProvider
from ..operators import (
    cross_sectional_residual,
    cs_sum,
    intraday_sum,
    purify,
    safe_divide,
    time_series_residual,
    ts_mean,
    ts_shift,
)
from .base import Factor, FactorMetadata


class OvernightTailFlowShock(Factor):
    """Five-day reversal signal from overnight shocks and late-session trading pressure."""

    metadata = FactorMetadata(
        name="overnight_tail_flow_shock",
        category="microstructure",
        direction="negative",
        description="Overnight residual shock × absolute closing-hour flow imbalance × volume spike.",
    )

    lookback_days = 5
    tail_bars = 60

    def compute(self, data: DataProvider) -> np.ndarray:
        close = data.daily("adj_close", 25)
        open_price = data.daily("adj_open", 25)
        market_cap = data.daily("market_cap", 25)

        previous_close = ts_shift(close, 1)
        overnight = safe_divide(open_price - previous_close, previous_close)[-self.lookback_days :]
        cap_window = market_cap[-self.lookback_days :]
        market_return = safe_divide(cs_sum(overnight * cap_window), cs_sum(cap_window))
        market_return = np.broadcast_to(market_return, overnight.shape)
        overnight_residual = time_series_residual(overnight, [market_return], intercept=True)

        buy = data.intraday("active_buy_volume_1m", self.lookback_days)
        sell = data.intraday("active_sell_volume_1m", self.lookback_days)
        volume = data.intraday("volume_1m", self.lookback_days)
        if buy.shape[1] < self.tail_bars:
            raise ValueError("The 1-minute dataset has fewer bars than the closing window")

        tail_buy = intraday_sum(buy, -self.tail_bars, None)
        tail_sell = intraday_sum(sell, -self.tail_bars, None)
        tail_volume = intraday_sum(volume, -self.tail_bars, None)
        daily_volume = intraday_sum(volume)

        imbalance = safe_divide(tail_buy - tail_sell, tail_buy + tail_sell)
        volume_spike = safe_divide(tail_volume, daily_volume / 4.0)
        daily_signal = np.abs(overnight_residual) * np.abs(imbalance) * volume_spike
        factor = -ts_mean(daily_signal)

        ret20 = safe_divide(close[-1:] - close[-21:-20], close[-21:-20])
        return purify(cross_sectional_residual(factor, [ret20], intercept=True))
