"""Mixed-horizon beta adjusted idiosyncratic skewness."""

from __future__ import annotations

import numpy as np

from ..data import DataProvider
from ..operators import cs_mean, purify, safe_divide, ts_corr, ts_shift, ts_skew, ts_std
from .base import Factor, FactorMetadata


class MixedHorizonIdiosyncraticSkewness(Factor):
    """Low residual-skewness signal using long-correlation/short-volatility beta inputs."""

    metadata = FactorMetadata(
        name="mixed_horizon_idiosyncratic_skewness",
        category="risk",
        direction="negative",
        description="Negative 21-day CAPM residual skewness with a mixed-horizon beta estimate.",
    )

    correlation_days = 750
    volatility_days = 252
    skewness_days = 21

    @staticmethod
    def _log_returns(close: np.ndarray) -> np.ndarray:
        return purify(np.log(safe_divide(close, ts_shift(close, 1))))

    @staticmethod
    def _market_return(stock_return: np.ndarray) -> np.ndarray:
        return np.broadcast_to(cs_mean(stock_return), stock_return.shape)

    def _beta(self, data: DataProvider) -> np.ndarray:
        correlation_return = self._log_returns(
            data.daily("adj_close", self.correlation_days + 1)
        )
        market_correlation_return = self._market_return(correlation_return)
        correlation = ts_corr(correlation_return, market_correlation_return)

        volatility_return = self._log_returns(
            data.daily("adj_close", self.volatility_days + 1)
        )
        market_volatility_return = self._market_return(volatility_return)
        beta = correlation * safe_divide(ts_std(volatility_return), ts_std(market_volatility_return))
        return purify(np.where(np.isfinite(beta), beta, 1.0))

    def compute(self, data: DataProvider) -> np.ndarray:
        beta = self._beta(data)
        stock_return = self._log_returns(data.daily("adj_close", self.skewness_days + 1))
        market_return = self._market_return(stock_return)
        residual = purify(stock_return - beta * market_return)[-self.skewness_days :]
        skewness = ts_skew(residual)
        return purify(-np.nan_to_num(skewness, nan=0.0))
