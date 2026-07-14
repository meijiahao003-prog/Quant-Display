"""Opening and closing volume concentration factor."""

from __future__ import annotations

import numpy as np

from ...data import DataProvider
from ...operators import intraday_sum, purify, safe_divide, ts_mean
from ..base import Factor, FactorMetadata


class OpenCloseVolumeConcentration(Factor):
    metadata = FactorMetadata(
        factor_id="F02",
        name="open_close_volume_concentration",
        category="microstructure",
        direction="negative",
        description="Average share of volume traded in the first and last 15 minutes.",
    )

    lookback_days = 20
    window_bars = 15
    open_weight = 0.5

    def compute(self, data: DataProvider) -> np.ndarray:
        volume = data.intraday("volume_1m", self.lookback_days)
        if volume.shape[1] < 2 * self.window_bars:
            raise ValueError("The intraday dataset is shorter than the selected open/close windows")
        total = intraday_sum(volume)
        open_share = safe_divide(intraday_sum(volume, 0, self.window_bars), total)
        close_share = safe_divide(intraday_sum(volume, -self.window_bars, None), total)
        concentration = self.open_weight * ts_mean(open_share) + (1 - self.open_weight) * ts_mean(
            close_share
        )
        return purify(-concentration)
