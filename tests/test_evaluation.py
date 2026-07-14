import unittest

import numpy as np

from ashare_factor_lab.evaluation import (
    forward_returns,
    information_coefficient,
    summarize_ic,
)


class EvaluationTests(unittest.TestCase):
    def test_forward_returns_are_aligned_to_signal_date(self) -> None:
        close = np.array([[10.0, 20.0], [11.0, 18.0], [12.1, 19.8]])
        result = forward_returns(close, horizon=1)
        np.testing.assert_allclose(result[0], [0.1, -0.1])
        np.testing.assert_allclose(result[1], [0.1, 0.1])
        self.assertTrue(np.isnan(result[-1]).all())

    def test_ic_and_summary(self) -> None:
        signal = np.tile(np.arange(30.0), (4, 1))
        returns = signal.copy()
        ic = information_coefficient(signal, returns, min_assets=20)
        np.testing.assert_allclose(ic, 1.0)
        summary = summarize_ic(ic)
        self.assertEqual(summary.mean_ic, 1.0)
        self.assertEqual(summary.observations, 4)


if __name__ == "__main__":
    unittest.main()
