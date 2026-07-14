"""Run every factor on deterministic synthetic data."""

from __future__ import annotations

import numpy as np

from ashare_factor_lab.data import SyntheticDataProvider
from ashare_factor_lab.factors import ALL_FACTORS


def main() -> None:
    data = SyntheticDataProvider(seed=11, n_assets=120)
    print(f"{'factor':42} {'coverage':>10} {'mean':>11} {'std':>11}")
    print("-" * 88)
    for factor_type in ALL_FACTORS:
        factor = factor_type()
        signal = factor.compute(data)
        valid = signal[np.isfinite(signal)]
        coverage = valid.size / signal.size
        mean = float(np.mean(valid)) if valid.size else np.nan
        std = float(np.std(valid)) if valid.size else np.nan
        print(f"{factor.metadata.name:42} {coverage:10.2%} {mean:11.4f} {std:11.4f}")


if __name__ == "__main__":
    main()
