"""Invented data only: sector models -> frozen rank ensemble -> research portfolio."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from futures_research.cta_strategy import run_sector_strategy
from futures_research.pipeline import ResearchDataset


def make_sector_demo(seed=42, models=None):
    rng = np.random.default_rng(seed)
    models = models or ["lightgbm"]
    dates = pd.bdate_range("2030-01-02", periods=60)
    times = ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30"]
    sectors = ["black", "metals", "energy_chem", "agri"]
    metadata = [{"Contract": f"DEMO_{s}_{p}_{role}", "sector": s, "product": f"DEMO_{s}_{p}", "role": role,
                 "metadata_available_at": "2030-01-01 00:00:00"}
                for s in sectors for p in range(3) for role in ("main", "secondary")]
    rows, returns = [], []
    for day in dates:
        for clock in times:
            dt = pd.Timestamp(f"{day.date()} {clock}")
            for item in metadata:
                x = rng.normal(size=4)
                raw = .002 * x[0] - .001 * x[1] + rng.normal(scale=.001)
                rows.append({"Date": day, "Contract": item["Contract"], "Datetime": dt,
                             "feature_available_at": dt, "label_end": dt + pd.Timedelta("30min"),
                             **{f"factor_{j}": float(v) for j, v in enumerate(x)}, "return_30m": raw})
                returns.append({"Date": day, "Contract": item["Contract"], "Datetime": dt,
                                "return_start": dt + pd.Timedelta("1min"),
                                "return_end": dt + pd.Timedelta("31min"),
                                "return": .0015 * x[0] - .0005 * x[1] + rng.normal(scale=.001)})
    frame = pd.DataFrame(rows)
    execution = pd.DataFrame(returns)
    features = [f"factor_{j}" for j in range(4)]
    vstart, tstart, end = dates[36], dates[48], dates[-1] + pd.Timedelta("1d")
    config = {"seed": seed, "timezone": "Asia/Shanghai", "models": models,
              "groups": {"all": features}, "sampling_times": times, "embargo": "0s",
              "intervals": {"fit": [str(dates[0]), str(vstart)], "valid": [str(vstart), str(tstart)], "test": [str(tstart), str(end)]},
              "raw_target_definition": "uncentered_forward_simple_return_30m", "target_mode": "sector_median_excess",
              "contract_metadata": metadata, "model_weights": {m: 1 / len(models) for m in models},
              "sector_allocations": {s: 1 / len(sectors) for s in sectors}, "contract_set": "both",
              "selection_rule": "validation_raw_ic", "long_quantile": .8, "short_quantile": .2,
              "commission_bps": .2, "slippage_bps": 1.0,
              "augmentation": {"n_synthetic_per_sector": 2, "alpha": 1.0},
              "iterations": {"xgboost": 8, "lightgbm": 8, "transformer": 2, "itransformer": 2},
              "model_params": {"xgboost": {"eta": .1}, "lightgbm": {"learning_rate": .1, "min_data_in_leaf": 5},
                               "transformer": {"d_model": 8, "n_heads": 2, "encoder_layers": 1},
                               "itransformer": {"d_model": 8, "n_heads": 2, "encoder_layers": 1}}}
    train = ResearchDataset.from_frame(frame.loc[frame.Datetime < tstart], features=features, target="return_30m")
    def test_loader():
        return ResearchDataset.from_frame(frame.loc[frame.Datetime >= tstart], features=features, target="return_30m")
    def returns_loader():
        return execution.loc[execution.Datetime >= tstart].copy()
    return train, test_loader, returns_loader, config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--models", default="lightgbm")
    parser.add_argument("--target-mode", choices=["sector_median_excess", "sector_zscore"], default="sector_median_excess")
    args = parser.parse_args()
    train, test, execution, config = make_sector_demo(models=args.models.split(","))
    config["target_mode"] = args.target_mode
    result = run_sector_strategy(train, test, execution, config, args.output, synthetic=True)
    print(json.dumps({"status": result["status"], "synthetic": True, "output": str(args.output),
                      "full_execution_engine": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
