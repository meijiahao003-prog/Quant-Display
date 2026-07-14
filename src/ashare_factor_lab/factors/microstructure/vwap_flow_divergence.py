"""Early-session VWAP and directional flow divergence."""

from __future__ import annotations

import numpy as np

from ...data import DataProvider
from ...operators import purify, ts_corr, ts_mean
from ..base import Factor, FactorMetadata


class VwapFlowDivergence(Factor):
    metadata = FactorMetadata(
        factor_id="F05",
        name="vwap_flow_divergence",
        category="microstructure",
        direction="signed",
        description="VWAP/buy-flow correlation signed by average morning buy-minus-sell pressure.",
    )

    lookback_days = 20

    def compute(self, data: DataProvider) -> np.ndarray:
        vwap = data.daily("early_vwap_30m", self.lookback_days)
        buy_ratio = data.daily("morning_active_buy_ratio", self.lookback_days)
        sell_ratio = data.daily("morning_active_sell_ratio", self.lookback_days)
        correlation = ts_corr(vwap, buy_ratio)
        direction = np.sign(ts_mean(buy_ratio - sell_ratio))
        return purify(correlation * direction)
