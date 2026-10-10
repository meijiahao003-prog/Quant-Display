"""Synthetic old-equity-factor -> new training pipeline integration example.

Run: python examples/run_factor_bridge_demo.py --output runs/equity-bridge
The generated data is an engineering fixture, not a financial market simulation.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path

import numpy as np
import pandas as pd

from ashare_factor_lab.data import InMemoryDataProvider
from ashare_factor_lab.factors import DollarVolumeStability
from futures_research.pipeline import ResearchDataset, run_research, write_json
from quant_research.factor_bridge import SnapshotDataProvider, build_factor_panel


SEED = 20261010
N_ASSETS = 12
N_DATES = 100
DATES = pd.bdate_range("2024-01-02 16:00", periods=N_DATES)
ASSET_IDS = tuple(f"SYNTH_EQUITY_{i:02d}" for i in range(N_ASSETS))
FEATURES = ("dollar_volume_stability_3", "dollar_volume_stability_8")


def synthetic_source(last_position: int) -> tuple[np.ndarray, np.ndarray]:
    """Generate a deterministic prefix, with no future rows exposed to providers.

    Drawing one date at a time keeps earlier values identical for every requested
    prefix length. The last row may be used to complete the preceding label.
    """
    if not 0 <= last_position < N_DATES:
        raise ValueError("synthetic source position is out of range")
    rng = np.random.default_rng(SEED)
    close = np.empty((last_position + 1, N_ASSETS), dtype=float)
    traded_value = np.empty_like(close)
    previous = np.linspace(20.0, 50.0, N_ASSETS)
    for position in range(last_position + 1):
        daily_return = rng.normal(0.0, 0.01, N_ASSETS)
        previous = previous * (1.0 + daily_return)
        close[position] = previous
        traded_value[position] = rng.lognormal(14.0, 0.6, N_ASSETS) * previous
    return close, traded_value


def make_dataset(first_position: int, last_position: int) -> tuple[ResearchDataset, dict]:
    """Compute explicit daily snapshots and next-trading-day close labels."""
    if first_position < 7 or last_position >= N_DATES - 1:
        raise ValueError("eight-day warmup and one future label day are required")
    close, traded_value = synthetic_source(last_position + 1)
    short, long = DollarVolumeStability(), DollarVolumeStability()
    short.lookback_days, long.lookback_days = 3, 8

    def snapshot_factory(decision: pd.Timestamp) -> SnapshotDataProvider:
        position = DATES.get_loc(decision)
        # Each provider receives only its own prefix, never the whole history.
        provider = InMemoryDataProvider(daily_fields={
            "traded_value": traded_value[:position + 1].copy(),
        })
        return SnapshotDataProvider(
            provider, as_of=decision, available_at=decision, asset_ids=ASSET_IDS,
            asset_class="equity", snapshot_id=f"synthetic-v1:{SEED}:{decision.isoformat()}",
        )

    panel = build_factor_panel(
        {FEATURES[0]: short, FEATURES[1]: long},
        decision_times=DATES[first_position:last_position + 1],
        provider_factory=snapshot_factory,
    )
    returns = close[first_position + 1:last_position + 2] / close[first_position:last_position + 1] - 1.0
    y = pd.Series(returns.reshape(-1), index=panel.x.index, name="next_day_close_return")
    label_end = pd.Series(
        np.repeat(DATES[first_position + 1:last_position + 2].to_numpy(), N_ASSETS),
        index=panel.x.index, name="label_end",
    )
    dataset = ResearchDataset(panel.x, y, panel.feature_available_at, label_end)
    dataset.validate()
    return dataset, dict(panel.audit)


def demo_config() -> dict:
    """Half-open intervals; boundary-crossing next-day labels are purged."""
    return {
        "seed": SEED, "timezone": "Asia/Shanghai",
        "intervals": {
            "fit": [DATES[7].isoformat(), DATES[60].isoformat()],
            "valid": [DATES[60].isoformat(), DATES[78].isoformat()],
            "test": [DATES[78].isoformat(), (DATES[99] + pd.Timedelta("1s")).isoformat()],
        },
        "models": ["xgboost"], "groups": {"equity_volume": list(FEATURES)},
        "pca_components": {}, "iterations": {"xgboost": 8},
        "model_params": {"xgboost": {"eta": 0.1, "max_depth": 2}},
        "embargo": "0s",
    }


def run_demo(output: Path) -> dict:
    train_valid, development_audit = make_dataset(7, 77)
    test_loader_calls = 0

    def test_loader() -> ResearchDataset:
        nonlocal test_loader_calls
        test_loader_calls += 1
        if test_loader_calls != 1:
            raise RuntimeError("the held-out synthetic test dataset may only be loaded once")
        # run_research invokes this only after all model choices are frozen.
        test, test_audit = make_dataset(78, 98)
        write_json(output / "factor_bridge_audit.json", {
            "synthetic": True, "asset_class": "equity", "development": development_audit,
            "test": test_audit, "test_bridge_built_after_all_models_frozen": True,
        })
        write_json(output / "bridge_demo_manifest.json", {
            "synthetic": True, "seed": SEED, "source_dates": N_DATES, "assets": N_ASSETS,
            "source_generator": "independent synthetic price innovations and traded-value draws",
            "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
            "target": "close(next trading day) / close(decision day) - 1",
            "label_end": "next trading day 16:00; every boundary-crossing label is purged",
            "feature_available_at": "decision day 16:00 (synthetic declared availability)",
            "data_provider": "one explicitly sliced InMemoryDataProvider prefix per decision",
            "asset_class": "equity", "performance_claim": "none; engineering verification only",
        })
        return test

    manifest = run_research(train_valid, test_loader, demo_config(), output, synthetic=True)
    if test_loader_calls != 1:
        raise RuntimeError("held-out test loader was not invoked exactly once")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("runs/equity-bridge"))
    arguments = parser.parse_args()
    manifest = run_demo(arguments.output)
    print(f"Synthetic equity-factor integration complete: {arguments.output}")
    print(f"Models: {', '.join(manifest['models'])}; this is not real strategy performance.")


if __name__ == "__main__":
    main()
