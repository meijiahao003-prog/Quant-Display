import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from futures_research.factor_compression import (
    compress_factor_group, transform_factor_group, save_pca_state, load_pca_state,
)
from futures_research.factor_grouping import extract_factor_groups


class CompressionTests(unittest.TestCase):
    def setUp(self):
        dates = pd.date_range("2024-01-01", periods=12, freq="D", name="Datetime")
        rng = np.random.default_rng(7)
        self.x = pd.DataFrame(rng.normal(size=(12, 3)), index=dates, columns=["a", "b", "c"])
        self.x.iloc[1, 0] = np.nan
        self.x.loc[dates[:7], "c"] = np.nan
        self.plan = {
            "fit_start": dates[0], "fit_end": dates[6], "svd_solver": "full",
            "group_name": "synthetic", "component_rule": {"mode": "fixed", "value": 1},
        }

    def test_future_perturbation_cannot_change_imputation_feature_selection_or_pca(self):
        original = compress_factor_group(self.x, self.plan)
        future_changed = self.x.copy()
        future_changed.iloc[7:] = [1e10, -1e10, 1e15]
        changed = compress_factor_group(future_changed, self.plan)
        self.assertEqual(original.state.fingerprint, changed.state.fingerprint)
        self.assertEqual(original.state.dropped_features, ("c",))
        np.testing.assert_array_equal(original.state.components, changed.state.components)
        np.testing.assert_array_equal(original.state.impute_values, changed.state.impute_values)
        pd.testing.assert_frame_equal(original.x.iloc[:7], changed.x.iloc[:7])
        fresh_test = transform_factor_group(self.x.iloc[7:], original.state)
        pd.testing.assert_frame_equal(fresh_test, original.x.iloc[7:])

    def test_pca_numeric_state_is_immutable(self):
        state = compress_factor_group(self.x, self.plan).state
        with self.assertRaises(ValueError):
            state.components[0, 0] = 123.0
        with self.assertRaises(ValueError):
            state.components.setflags(write=True)

    def test_grouping_preserves_values_and_rejects_overlap(self):
        result = extract_factor_groups(self.x, {"groups": {"first": ["a", "b"], "second": ["c"]}})
        pd.testing.assert_frame_equal(result.groups["first"], self.x[["a", "b"]])
        with self.assertRaisesRegex(ValueError, "互斥"):
            extract_factor_groups(self.x, {"groups": {"first": ["a"], "second": ["a", "b", "c"]}})

    def test_duplicate_keys_and_impossible_component_count_fail(self):
        with self.assertRaisesRegex(ValueError, "duplicate sample"):
            compress_factor_group(pd.concat([self.x, self.x.iloc[[0]]]), self.plan)
        with self.assertRaisesRegex(ValueError, "exceeds available"):
            compress_factor_group(self.x, {**self.plan, "component_rule": {"mode": "fixed", "value": 10}})

    def test_checkpoint_roundtrip_restores_same_transform_without_pickle(self):
        original = compress_factor_group(self.x, self.plan)
        with TemporaryDirectory() as temporary:
            paths = save_pca_state(original.state, temporary)
            restored = load_pca_state(paths["directory"])
            self.assertEqual(restored.manifest(), original.state.manifest())
            pd.testing.assert_frame_equal(transform_factor_group(self.x, restored), original.x)
            self.assertFalse(restored.components.flags.writeable)

    def test_checkpoint_rejects_missing_hash_and_tampered_files(self):
        original = compress_factor_group(self.x, self.plan)
        for filename in ("compression_manifest.json", "compression_state.npz"):
            with self.subTest(filename=filename), TemporaryDirectory() as temporary:
                paths = save_pca_state(original.state, temporary)
                path = Path(paths["directory"]) / filename
                path.write_bytes(path.read_bytes() + b"tampered")
                with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                    load_pca_state(paths["directory"])
        with TemporaryDirectory() as temporary:
            paths = save_pca_state(original.state, temporary)
            Path(paths["checksums"]).unlink()
            with self.assertRaisesRegex(ValueError, "missing.*checksums"):
                load_pca_state(paths["directory"])


if __name__ == "__main__":
    unittest.main()
