"""Deterministic invented samples; never use demo metrics as investment evidence."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .pipeline import ResearchDataset


def make_demo(seed: int = 7, models: list[str] | None = None):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=100)
    times = ["09:30", "10:00", "10:30", "13:30", "14:00", "14:30"]
    records = []
    for day in dates:
        for instrument in ("SYN_A", "SYN_B"):
            for clock in times:
                dt = pd.Timestamp(f"{day.date()} {clock}")
                f = rng.normal(size=4)
                records.append({"Date": day, "Contract": instrument, "Datetime": dt,
                                "feature_available_at": dt, "label_end": dt + pd.Timedelta("30min"),
                                **{f"F_{i+1:03d}": float(v) for i, v in enumerate(f)},
                                "future_return": 0.3 * f[0] - 0.15 * f[1] + rng.normal(scale=0.5)})
    frame = pd.DataFrame(records)
    start, vstart, tstart, end = dates[0], dates[60], dates[80], dates[-1] + pd.Timedelta("1d")
    config = {"seed": seed, "timezone": "Asia/Shanghai", "asset_class": "synthetic",
              "intervals": {"fit": [str(start), str(vstart)], "valid": [str(vstart), str(tstart)],
                            "test": [str(tstart), str(end)]},
              "models": models or ["xgboost"], "embargo": "0s", "sampling_times": times,
              "groups": {"core": ["F_001", "F_002"], "auxiliary": ["F_003", "F_004"]},
              "pca_components": {"core": 2, "auxiliary": 1},
              "iterations": {"xgboost": 8, "lightgbm": 8, "transformer": 2, "itransformer": 2},
              "model_params": {"xgboost": {"eta": 0.1}, "lightgbm": {"learning_rate": 0.1}}}
    features = [f"F_{i:03d}" for i in range(1, 5)]
    train = ResearchDataset.from_frame(frame.loc[frame.Datetime < tstart], features=features, target="future_return")
    def load_test():
        return ResearchDataset.from_frame(frame.loc[frame.Datetime >= tstart], features=features, target="future_return")
    return train, load_test, config
