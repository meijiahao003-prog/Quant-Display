import unittest

import numpy as np
import pandas as pd

from futures_research.factor_series_qc_v2 import (
    factor_qc_summary, run_factor_frame_qc, test_prefix_invariance,
)


class QualityControlTests(unittest.TestCase):
    def test_causal_rolling_passes_and_full_sample_normalization_fails(self):
        values = pd.Series(np.arange(20.0), index=pd.date_range("2024-01-01", periods=20))
        trailing = test_prefix_invariance(lambda x: x.rolling(3).mean(), [values], [5, 11, 18])
        global_fit = test_prefix_invariance(lambda x: (x - x.mean()) / x.std(), [values], [5, 11, 18])
        self.assertTrue(trailing["passed"].all())
        self.assertFalse(global_fit["passed"].any())

    def test_prefix_shape_must_not_broadcast_into_false_pass(self):
        def broken(values):
            return np.zeros(len(values)) if len(values) == 10 else np.zeros(1)
        with self.assertRaisesRegex(ValueError, "shape"):
            test_prefix_invariance(broken, [np.arange(10.0)], [4])
        with self.assertRaisesRegex(ValueError, "cut_positions"):
            test_prefix_invariance(lambda x: x, [np.arange(10.0)], [])

    def test_diagnostic_copies_inputs_before_mutating_factor_call(self):
        def mutating(values):
            values += 1
            return values
        source = np.arange(10.0)
        report = test_prefix_invariance(mutating, [source], [3, 7])
        np.testing.assert_array_equal(source, np.arange(10.0))
        self.assertTrue(report["passed"].all())

    def test_empty_support_does_not_fallback_to_unsupported_statistics(self):
        summary = factor_qc_summary(np.arange(10.0), support_values=np.zeros(10))
        self.assertEqual(summary["n_analyzed"], 0)
        self.assertTrue(np.isnan(summary["mean"]))

    def test_frame_qc_rejects_misaligned_base(self):
        values = pd.DataFrame({"a": np.arange(5.0)})
        manifest = pd.DataFrame({"factor": ["a"]})
        base = pd.DataFrame({"session_segment_id": [0] * 5}, index=values.index[::-1])
        with self.assertRaisesRegex(ValueError, "equal indices"):
            run_factor_frame_qc(values, manifest, base=base)

    def test_duplicate_detection_requires_matching_missingness(self):
        values = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [1.0, np.nan, np.nan]})
        report = run_factor_frame_qc(values, pd.DataFrame({"factor": ["a", "b"]}))
        self.assertTrue(report["duplicates"].empty)


if __name__ == "__main__":
    unittest.main()
