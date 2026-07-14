import unittest

import numpy as np

from ashare_factor_lab.data import SyntheticDataProvider
from ashare_factor_lab.factors import ALL_FACTORS


class FactorSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.synthetic_data = SyntheticDataProvider(seed=17, n_assets=80)

    def test_all_factors_return_finite_cross_sections(self) -> None:
        for factor_type in ALL_FACTORS:
            with self.subTest(factor=factor_type.__name__):
                signal = factor_type().compute(self.synthetic_data)
                self.assertEqual(signal.shape, (1, 80))
                self.assertGreaterEqual(np.mean(np.isfinite(signal)), 0.90)


if __name__ == "__main__":
    unittest.main()
