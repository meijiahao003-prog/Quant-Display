"""Turnover stability with partial dollar-volume decorrelation."""

from __future__ import annotations

import numpy as np

from ...data import DataProvider
from ...operators import (
    cross_sectional_residual,
    cs_percentile_rank,
    purify,
    safe_divide,
    ts_mean,
    ts_std,
)
from ..base import Factor, FactorMetadata


class TurnoverStability(Factor):
    metadata = FactorMetadata(
        factor_id="F08",
        name="turnover_stability",
        category="trading_activity",
        direction="negative",
        description="Turnover stability partially decorrelated from traded-value stability.",
    )

    lookback_days = 75
    decorrelation_strength = 0.25

    @staticmethod
    def _rank_or_neutral(value: np.ndarray) -> np.ndarray:
        ranked = cs_percentile_rank(purify(value))
        return purify(np.where(np.isfinite(ranked), ranked, 0.5))

    def compute(self, data: DataProvider) -> np.ndarray:
        volume = data.daily("volume", self.lookback_days)
        float_shares = data.daily("float_shares", self.lookback_days)
        turnover = safe_divide(volume, float_shares)
        turnover_stability = -safe_divide(ts_std(turnover), ts_mean(turnover))

        traded_value = data.daily("traded_value", self.lookback_days)
        dollar_volume_stability = -safe_divide(ts_std(traded_value), ts_mean(traded_value))

        target_rank = self._rank_or_neutral(turnover_stability)
        proxy_rank = self._rank_or_neutral(dollar_volume_stability)
        residual = cross_sectional_residual(target_rank, [proxy_rank], intercept=True)
        residual_rank = self._rank_or_neutral(residual)
        adjusted = (
            (1.0 - self.decorrelation_strength) * target_rank
            + self.decorrelation_strength * residual_rank
        )
        return purify(adjusted)
