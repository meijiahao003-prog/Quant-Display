import unittest

import numpy as np

from ashare_factor_lab.operators import (
    cross_sectional_residual,
    cs_percentile_rank,
    intraday_sum,
    safe_divide,
    time_series_residual,
    ts_corr,
)


class OperatorTests(unittest.TestCase):
    def test_safe_divide_handles_zero_and_infinity(self) -> None:
        result = safe_divide(
            np.array([[1.0, 2.0, np.inf]]), np.array([[1.0, 0.0, 2.0]])
        )
        self.assertEqual(result[0, 0], 1.0)
        self.assertTrue(np.isnan(result[0, 1]))
        self.assertTrue(np.isnan(result[0, 2]))

    def test_cross_sectional_rank_uses_average_ties(self) -> None:
        ranked = cs_percentile_rank(np.array([[10.0, 20.0, 20.0, 40.0, np.nan]]))
        np.testing.assert_allclose(ranked[0, :4], [0.0, 0.5, 0.5, 1.0])
        self.assertTrue(np.isnan(ranked[0, 4]))

    def test_intraday_sum_enforces_bar_coverage(self) -> None:
        bars = np.ones((2, 10, 2))
        bars[0, :3, 0] = np.nan
        bars[1, :1, 1] = np.nan
        result = intraday_sum(bars, min_valid_ratio=0.8)
        self.assertTrue(np.isnan(result[0, 0]))
        self.assertEqual(result[1, 1], 9.0)

    def test_cross_sectional_residual_removes_linear_exposure(self) -> None:
        exposure = np.arange(30.0).reshape(1, -1)
        target = 2.0 + 3.0 * exposure + np.sin(exposure)
        residual = cross_sectional_residual(target, [exposure])
        self.assertLess(abs(np.corrcoef(residual[0], exposure[0])[0, 1]), 1e-12)

    def test_time_series_residual_removes_per_asset_exposure(self) -> None:
        exposure = np.arange(30.0).reshape(-1, 1)
        exposure = np.repeat(exposure, 3, axis=1)
        target = 1.0 + exposure * np.array([[1.0, 2.0, 3.0]]) + np.sin(exposure)
        residual = time_series_residual(target, [exposure])
        correlation = ts_corr(residual, exposure)
        self.assertLess(np.nanmax(np.abs(correlation)), 1e-12)


if __name__ == "__main__":
    unittest.main()
