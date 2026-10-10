import unittest

import numpy as np
import pandas as pd

from ashare_factor_lab.data import InMemoryDataProvider
from ashare_factor_lab.factors.base import Factor
from ashare_factor_lab.factors import DollarVolumeStability
from quant_research.factor_bridge import SnapshotDataProvider, build_factor_panel


class TrailingMean(Factor):
    def compute(self, data):
        return data.daily("close", 3).mean(axis=0, keepdims=True)


class BadShape(Factor):
    def compute(self, data):
        return data.daily("close", 1).ravel()


class FactorBridgeTests(unittest.TestCase):
    def setUp(self):
        self.dates = pd.date_range("2024-01-01 16:00", periods=8, freq="D")
        self.values = np.arange(16.0).reshape(8, 2)

    def factory(self, values):
        def create(decision):
            prefix = values[self.dates <= decision]
            return SnapshotDataProvider(
                InMemoryDataProvider(daily_fields={"close": prefix}),
                as_of=decision, available_at=decision, asset_ids=["STOCK_A", "STOCK_B"],
                asset_class="equity", snapshot_id=f"synthetic:{decision.isoformat()}",
            )
        return create

    def test_dynamic_snapshots_preserve_prefix_when_future_data_changes(self):
        kwargs = {"factors": {"trailing_mean": TrailingMean()}, "decision_times": self.dates[2:6]}
        original = build_factor_panel(**kwargs, provider_factory=self.factory(self.values))
        mutated = self.values.copy()
        mutated[6:] = 1e12
        repeated = build_factor_panel(**kwargs, provider_factory=self.factory(mutated))
        pd.testing.assert_frame_equal(original.x, repeated.x)
        self.assertEqual(original.audit["fingerprint"], repeated.audit["fingerprint"])
        self.assertEqual(original.x.index.names, ["Date", "Contract", "Datetime"])
        self.assertEqual(original.x.attrs["asset_class"], "equity")
        self.assertEqual(original.x.iloc[0, 0], self.values[:3, 0].mean())
        self.assertFalse(original.audit["vendor_point_in_time_verified"])

    def test_late_availability_and_wrong_snapshot_date_fail(self):
        with self.assertRaisesRegex(ValueError, "unavailable"):
            SnapshotDataProvider(
                InMemoryDataProvider(), as_of=self.dates[0], available_at=self.dates[1],
                asset_ids=["A"], asset_class="equity", snapshot_id="invalid",
            )
        fixed = self.factory(self.values)(self.dates[2])
        with self.assertRaisesRegex(ValueError, "exactly match"):
            build_factor_panel({"mean": TrailingMean()}, decision_times=self.dates[3:4],
                               provider_factory=lambda decision: fixed)

    def test_shape_is_never_implicitly_broadcast_or_flattened(self):
        with self.assertRaisesRegex(ValueError, "expected exactly"):
            build_factor_panel({"bad": BadShape()}, decision_times=self.dates[2:3],
                               provider_factory=self.factory(self.values))
        def wrong_asset_count(decision):
            return SnapshotDataProvider(
                InMemoryDataProvider(daily_fields={"close": self.values[:3]}),
                as_of=decision, available_at=decision, asset_ids=["A"],
                asset_class="equity", snapshot_id="bad-columns",
            )
        with self.assertRaisesRegex(ValueError, "asset columns"):
            build_factor_panel({"mean": TrailingMean()}, decision_times=self.dates[2:3],
                               provider_factory=wrong_asset_count)

    def test_duplicate_snapshot_identifier_is_rejected(self):
        factory = self.factory(self.values)
        def repeated_id(decision):
            snapshot = factory(decision)
            snapshot._snapshot_id = "same-source-version"
            return snapshot
        with self.assertRaisesRegex(ValueError, "distinct snapshot_ids"):
            build_factor_panel({"mean": TrailingMean()}, decision_times=self.dates[2:4],
                               provider_factory=repeated_id)

    def test_existing_factor_implementation_runs_through_bridge(self):
        factor = DollarVolumeStability()
        factor.lookback_days = 3
        decision = self.dates[2]
        source = InMemoryDataProvider(daily_fields={"traded_value": self.values[:3] + 10})
        expected = factor.compute(source)[0]
        result = build_factor_panel(
            {"volume_stability": factor}, decision_times=[decision],
            provider_factory=lambda as_of: SnapshotDataProvider(
                source, as_of=as_of, available_at=as_of, asset_ids=["A", "B"],
                asset_class="equity", snapshot_id="synthetic-real-factor",
            ),
        )
        np.testing.assert_allclose(result.x["volume_stability"], expected)
        audit = result.audit["snapshots"][0]["factors"]["volume_stability"]
        self.assertEqual(audit["public_scalar_parameters"]["lookback_days"], 3)


if __name__ == "__main__":
    unittest.main()
