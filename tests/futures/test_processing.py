import unittest

import numpy as np
import pandas as pd

from futures_research.factor_processing import fit_zscore_state, process_factor_data, transform_zscore


class ProcessingTests(unittest.TestCase):
    def setUp(self):
        times = pd.date_range("2024-01-01", periods=8, freq="D")
        index = pd.MultiIndex.from_arrays(
            [times.normalize(), ["SYNTH"] * len(times), times],
            names=["Date", "Contract", "Datetime"],
        )
        self.x = pd.DataFrame({"trend": np.arange(8.0), "constant": np.ones(8)}, index=index)
        self.y = pd.Series(np.arange(8.0), index=index)
        self.bounds = {"fit_start": times[0], "fit_end": times[3]}

    def test_future_values_do_not_change_fit_statistics_or_training_transform(self):
        original = process_factor_data(self.x, self.y, **self.bounds)
        changed = self.x.copy()
        changed.iloc[4:] = [1e9, -1e9]
        repeated = process_factor_data(changed, self.y, **self.bounds)
        self.assertEqual(original.state.fingerprint, repeated.state.fingerprint)
        pd.testing.assert_frame_equal(original.x.iloc[:4], repeated.x.iloc[:4])
        self.assertEqual(original.state.mean["trend"], 1.5)
        self.assertTrue(original.x["constant"].isna().all())
        self.assertIs(original.y, self.y)

    def test_direct_fit_rejects_duplicate_keys_and_nat(self):
        duplicate = pd.concat([self.x, self.x.iloc[[0]]])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            fit_zscore_state(duplicate, **self.bounds)
        with self.assertRaisesRegex(ValueError, "finite timestamps"):
            fit_zscore_state(self.x, fit_start=pd.NaT, fit_end=self.bounds["fit_end"])

    def test_mutated_fit_statistics_are_rejected(self):
        state = fit_zscore_state(self.x, **self.bounds)
        state.mean.iloc[0] = 12345.0
        with self.assertRaisesRegex(ValueError, "mutated"):
            transform_zscore(self.x, state)

    def test_misaligned_labels_are_not_silently_repaired(self):
        with self.assertRaisesRegex(ValueError, "values/order differ"):
            process_factor_data(self.x, self.y.iloc[::-1], **self.bounds)

    def test_center_before_float32_cast_preserves_small_changes_at_large_levels(self):
        large = self.x.copy()
        large["trend"] += 1e12
        result = process_factor_data(large, self.y, **self.bounds)
        expected = (np.arange(8.0) - 1.5) / np.std(np.arange(4.0))
        np.testing.assert_allclose(result.x["trend"], expected, rtol=1e-6)


if __name__ == "__main__":
    unittest.main()
