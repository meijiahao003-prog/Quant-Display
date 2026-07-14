"""Dollar-volume stability characteristic."""

from __future__ import annotations

from ...data import DataProvider
from ...operators import purify, safe_divide, ts_mean, ts_std
from ..base import Factor, FactorMetadata


class DollarVolumeStability(Factor):
    metadata = FactorMetadata(
        factor_id="F07",
        name="dollar_volume_stability",
        category="trading_activity",
        direction="negative",
        description="Negative coefficient of variation of daily traded value.",
    )

    lookback_days = 126

    def compute(self, data: DataProvider):
        traded_value = data.daily("traded_value", self.lookback_days)
        coefficient_of_variation = safe_divide(ts_std(traded_value), ts_mean(traded_value))
        return purify(-coefficient_of_variation)
