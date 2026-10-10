import unittest

import numpy as np
import pandas as pd

from futures_research.factor_sampling import (
    purged_time_split, sample_factor_data, validate_point_in_time,
)


class SamplingTests(unittest.TestCase):
    def setUp(self):
        self.times = pd.date_range("2024-01-01 09:00", periods=8, freq="h")
        self.index = pd.MultiIndex.from_arrays(
            [self.times.normalize(), ["SYNTH"] * 8, self.times],
            names=["Date", "Contract", "Datetime"],
        )
        self.x = pd.DataFrame({"factor": np.arange(8.0)}, index=self.index)
        self.y = pd.Series(np.arange(8.0) + 10, index=self.index)
        self.available = pd.Series(self.times, index=self.index)
        self.label_end = pd.Series(self.times + pd.Timedelta("1h"), index=self.index)

    def test_exact_sampling_preserves_feature_label_alignment(self):
        result = sample_factor_data(self.x, self.y, ["10:00", "13:00"])
        pd.testing.assert_frame_equal(result.x, self.x.iloc[[1, 4]])
        pd.testing.assert_series_equal(result.y, self.y.iloc[[1, 4]])

    def test_late_features_and_misaligned_metadata_fail_closed(self):
        late = self.available.copy()
        late.iloc[0] += pd.Timedelta("1s")
        with self.assertRaisesRegex(ValueError, "unavailable"):
            validate_point_in_time(self.index, feature_available_at=late, label_end=self.label_end)
        with self.assertRaisesRegex(ValueError, "exactly"):
            validate_point_in_time(self.index, feature_available_at=self.available.iloc[::-1], label_end=self.label_end)
        missing = self.label_end.copy()
        missing.iloc[0] = pd.NaT
        with self.assertRaisesRegex(ValueError, "NaT"):
            validate_point_in_time(self.index, feature_available_at=self.available, label_end=missing)

    def test_split_purges_horizon_and_embargo_crossing_rows(self):
        split = purged_time_split(
            self.index, feature_available_at=self.available, label_end=self.label_end,
            intervals={"train": (self.times[0], self.times[3]), "test": (self.times[4], self.times[7])},
            embargo="1h",
        )
        # 11:00's label ends 12:00; +1h touches test's 13:00 start -> purge.
        np.testing.assert_array_equal(np.flatnonzero(split.masks["train"]), [0, 1])
        np.testing.assert_array_equal(np.flatnonzero(split.masks["test"]), [4, 5, 6])
        self.assertEqual(split.audit["splits"]["train"]["purged_rows"], 2)

    def test_overlapping_intervals_and_negative_horizons_fail(self):
        with self.assertRaisesRegex(ValueError, "non-overlapping"):
            purged_time_split(
                self.index, feature_available_at=self.available, label_end=self.label_end,
                intervals={"train": (self.times[0], self.times[4]), "test": (self.times[4], self.times[7])},
            )
        backward = self.label_end.copy()
        backward.iloc[0] = self.times[0] - pd.Timedelta("1s")
        with self.assertRaisesRegex(ValueError, "precede"):
            validate_point_in_time(self.index, feature_available_at=self.available, label_end=backward)

    def test_duplicate_wallclock_cannot_mask_a_missing_grid_time(self):
        times = pd.DatetimeIndex(["2024-01-01 09:30", "2024-01-02 09:30", "2024-01-03 10:00"])
        index = pd.MultiIndex.from_arrays(
            [["trade_day_1", "trade_day_1", "trade_day_2"], ["SYNTH"] * 3, times],
            names=["Date", "Contract", "Datetime"],
        )
        x = pd.DataFrame({"factor": [1.0, 2.0, 3.0]}, index=index)
        y = pd.Series([0.0, 1.0, 2.0], index=index)
        result = sample_factor_data(x, y, ["09:30", "10:00"])
        self.assertEqual(result.audit["complete_group_count"], 0)
        self.assertEqual(result.audit["duplicate_time_group_count"], 1)
        with self.assertRaisesRegex(ValueError, "strict_complete_grid"):
            sample_factor_data(x, y, ["09:30", "10:00"], strict_complete_grid=True)


if __name__ == "__main__":
    unittest.main()
