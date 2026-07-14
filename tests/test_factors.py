import unittest

import numpy as np

from ashare_factor_lab.data import SyntheticDataProvider
from ashare_factor_lab.factors import ALL_FACTORS, FACTOR_GROUPS


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

    def test_factor_ids_and_categories_are_stable(self) -> None:
        factor_ids = [factor_type.metadata.factor_id for factor_type in ALL_FACTORS]
        expected_ids = [f"F{number:02d}" for number in range(1, len(ALL_FACTORS) + 1)]
        self.assertEqual(factor_ids, expected_ids)
        self.assertEqual(len(factor_ids), len(set(factor_ids)))

        grouped_factors = []
        for category, factor_types in FACTOR_GROUPS.items():
            for factor_type in factor_types:
                with self.subTest(category=category, factor=factor_type.__name__):
                    self.assertEqual(factor_type.metadata.category, category)
                    grouped_factors.append(factor_type)
        self.assertEqual(grouped_factors, list(ALL_FACTORS))


if __name__ == "__main__":
    unittest.main()
