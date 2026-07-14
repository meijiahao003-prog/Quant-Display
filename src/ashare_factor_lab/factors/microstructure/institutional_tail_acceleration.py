"""Institutional flow persistence and intraday acceleration composite."""

from __future__ import annotations

import numpy as np

from ...data import DataProvider
from ...operators import (
    cross_sectional_residual,
    cs_zscore,
    intraday_sum,
    purify,
    safe_divide,
    ts_mean,
    ts_std,
    ts_sum,
)
from ..base import Factor, FactorMetadata


class InstitutionalTailAcceleration(Factor):
    metadata = FactorMetadata(
        factor_id="F04",
        name="institutional_tail_acceleration",
        category="microstructure",
        direction="positive",
        description="Institutional flow persistence blended with closing-versus-morning acceleration.",
    )

    lookback_days = 20
    morning_bars = 12
    closing_bars = 12
    base_weight = 0.65
    tail_weight = 0.35

    @staticmethod
    def _flow_absnet(buy: np.ndarray, sell: np.ndarray) -> np.ndarray:
        net = buy - sell
        return safe_divide(ts_sum(net), ts_sum(np.abs(net)))

    @staticmethod
    def _flow_value(buy: np.ndarray, sell: np.ndarray) -> np.ndarray:
        return safe_divide(ts_sum(buy - sell), ts_sum(buy + sell))

    def compute(self, data: DataProvider) -> np.ndarray:
        buy = data.intraday("institution_active_buy_value_5m", self.lookback_days)
        sell = data.intraday("institution_active_sell_value_5m", self.lookback_days)
        if buy.shape[1] < self.morning_bars + self.closing_bars:
            raise ValueError("The 5-minute dataset is shorter than the selected session windows")

        buy_daily = intraday_sum(buy)
        sell_daily = intraday_sum(sell)
        base_raw = self._flow_absnet(buy_daily, sell_daily)
        base_value = self._flow_value(buy_daily, sell_daily)

        morning_buy = intraday_sum(buy, 0, self.morning_bars)
        morning_sell = intraday_sum(sell, 0, self.morning_bars)
        closing_buy = intraday_sum(buy, -self.closing_bars, None)
        closing_sell = intraday_sum(sell, -self.closing_bars, None)
        morning_strength = self._flow_value(morning_buy, morning_sell)
        closing_strength = self._flow_value(closing_buy, closing_sell)
        tail_acceleration = closing_strength - morning_strength

        close = data.daily("adj_close", 21)
        ret20 = safe_divide(close[-1:] - close[:1], close[:1])
        size = np.log(data.daily("market_cap", 1))
        liquidity = np.log(ts_mean(data.daily("traded_value", 20)))
        volatility = ts_std(data.daily("daily_return", 20))

        controls = [ret20, size, liquidity, volatility]
        base_alpha = cross_sectional_residual(base_raw, controls, intercept=True)
        tail_alpha = -cross_sectional_residual(
            tail_acceleration, controls + [base_raw, base_value], intercept=True
        )
        factor = self.base_weight * cs_zscore(base_alpha) + self.tail_weight * np.nan_to_num(
            cs_zscore(tail_alpha), nan=0.0
        )
        return purify(factor)
