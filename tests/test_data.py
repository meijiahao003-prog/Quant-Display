import unittest

import numpy as np

from ashare_factor_lab.data import InMemoryDataProvider


class DataProviderTests(unittest.TestCase):
    def test_in_memory_provider_returns_requested_tail(self) -> None:
        provider = InMemoryDataProvider(daily_fields={"x": np.arange(20).reshape(5, 4)})
        result = provider.daily("x", 2)
        self.assertEqual(result.shape, (2, 4))
        np.testing.assert_array_equal(result, np.arange(20).reshape(5, 4)[-2:])

    def test_provider_rejects_excessive_lookback(self) -> None:
        provider = InMemoryDataProvider(daily_fields={"x": np.ones((3, 2))})
        with self.assertRaises(ValueError):
            provider.daily("x", 4)


if __name__ == "__main__":
    unittest.main()
