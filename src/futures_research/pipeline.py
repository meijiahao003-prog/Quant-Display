"""Auditable two-stage research orchestration shared by stock/futures feature panels.

Test data is obtained through a deferred loader only after all models are frozen.
This module evaluates predictions; it does not execute trades or calculate P&L.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
from typing import Callable, Mapping

import joblib
import numpy as np
import pandas as pd

from .factor_compression import compress_factor_group, transform_factor_group, save_pca_state
from .factor_grouping import extract_factor_groups
from .factor_model_testing import evaluate_factor_model
from .factor_model_training import make_training_config, train_factor_model
from .factor_processing import process_factor_data, transform_zscore, validate_processing_inputs
from .factor_runtime import frame_fingerprint
from .factor_sampling import purged_time_split, sample_factor_data, validate_point_in_time


def json_value(value):
    if isinstance(value, Mapping):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (pd.Timestamp, Path)):
        return str(value)
    return value


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(json_value(payload), ensure_ascii=False, indent=2,
                               sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class ResearchDataset:
    x: pd.DataFrame
    y: pd.Series
    feature_available_at: pd.Series
    label_end: pd.Series

    def validate(self) -> None:
        validate_processing_inputs(self.x, self.y)
        validate_point_in_time(self.x.index, feature_available_at=self.feature_available_at,
                               label_end=self.label_end)
        if not np.isfinite(self.y.to_numpy(dtype=float)).all():
            raise ValueError("labels must be finite; explicitly quarantine unavailable targets")

    def subset(self, index: pd.Index) -> "ResearchDataset":
        return ResearchDataset(self.x.loc[index], self.y.loc[index],
                               self.feature_available_at.loc[index], self.label_end.loc[index])

    @classmethod
    def from_frame(cls, frame: pd.DataFrame, *, features: list[str], target: str):
        reserved = {"Date", "Contract", "Datetime", "feature_available_at", "label_end", target}
        if not features or len(features) != len(set(features)) or reserved.intersection(features):
            raise ValueError("features must be unique and exclude keys, targets and timing metadata")
        required = reserved | set(features)
        if frame.columns.has_duplicates or not required.issubset(frame.columns):
            raise ValueError("input has duplicate columns or is missing required dataset fields")
        data = frame.copy()
        if data["Contract"].isna().any():
            raise ValueError("Contract/instrument ID must be supplied")
        for col in ["Date", "Datetime", "feature_available_at", "label_end"]:
            data[col] = pd.to_datetime(data[col], errors="raise")
            if data[col].isna().any() or data[col].dt.tz is not None:
                raise ValueError("use finite, timezone-naive timestamps in the declared exchange timezone")
        if not data["Date"].eq(data["Date"].dt.normalize()).all():
            raise ValueError("Date must be the normalized exchange trading date")
        data["Contract"] = data["Contract"].astype(str)
        data = data.set_index(["Date", "Contract", "Datetime"]).sort_index()
        result = cls(data[features], data[target], data["feature_available_at"], data["label_end"])
        result.validate()
        return result


def load_dataset(path: Path, *, features: list[str], target: str) -> ResearchDataset:
    if path.suffix == ".csv":
        frame = pd.read_csv(path)
    elif path.suffix == ".parquet":
        frame = pd.read_parquet(path)
        if isinstance(frame.index, pd.MultiIndex):
            frame = frame.reset_index()
    else:
        raise ValueError("only explicit CSV or Parquet datasets are accepted")
    return ResearchDataset.from_frame(frame, features=features, target=target)


def validate_config(config: Mapping) -> dict:
    cfg = dict(config)
    for key in ("seed", "timezone", "intervals", "models", "groups"):
        if key not in cfg:
            raise ValueError(f"missing configuration field: {key}")
    from zoneinfo import ZoneInfo
    ZoneInfo(cfg["timezone"])
    intervals = cfg["intervals"]
    if set(intervals) != {"fit", "valid", "test"}:
        raise ValueError("intervals must contain fit/valid/test only")
    previous_end = None
    for name in ("fit", "valid", "test"):
        start, end = map(pd.Timestamp, intervals[name])
        if pd.isna(start) or pd.isna(end) or start.tz is not None or end.tz is not None or start >= end:
            raise ValueError("intervals must be finite, timezone-naive and non-empty")
        if previous_end is not None and start < previous_end:
            raise ValueError("time intervals overlap or are out of order")
        previous_end = end
    allowed = {"xgboost", "lightgbm", "catboost", "transformer", "itransformer"}
    if not cfg["models"] or len(set(cfg["models"])) != len(cfg["models"]) or not set(cfg["models"]) <= allowed:
        raise ValueError("models must be distinct supported method names")
    return cfg


def _sample(ds: ResearchDataset, cfg: Mapping) -> ResearchDataset:
    ds.validate()
    if cfg.get("sampling_times"):
        sample = sample_factor_data(ds.x, ds.y, cfg["sampling_times"],
                                    strict_complete_grid=bool(cfg.get("strict_complete_grid", False)))
        return ds.subset(sample.x.index)
    return ds


def _purge(ds: ResearchDataset, cfg: Mapping, names: tuple[str, ...]):
    intervals = {name: (pd.Timestamp(cfg["intervals"][name][0]),
                        pd.Timestamp(cfg["intervals"][name][1]) - pd.Timedelta("1ns"))
                 for name in ("fit", "valid", "test")}
    split = purged_time_split(ds.x.index, intervals=intervals, label_end=ds.label_end,
                              feature_available_at=ds.feature_available_at,
                              embargo=cfg.get("embargo", "0s"))
    keep = np.logical_or.reduce([split.masks[n] for n in names])
    return ds.subset(ds.x.index[keep]), split.audit


def _prepare(ds: ResearchDataset, cfg: Mapping):
    fit_start, fit_end = map(pd.Timestamp, cfg["intervals"]["fit"])
    processed = process_factor_data(ds.x, ds.y, fit_start=fit_start,
                                    fit_end=fit_end - pd.Timedelta("1ns"))
    grouped = extract_factor_groups(processed.x, {"groups": cfg["groups"], "unassigned_policy": "error"})
    blocks, states, audits = [], {}, {}
    for name, values in grouped.groups.items():
        n_components = cfg.get("pca_components", {}).get(name)
        if n_components is None:
            blocks.append(values)
            states[name] = None
        else:
            compressed = compress_factor_group(values, {
                "method": "pca", "execute": True, "group_name": name,
                "fit_start": fit_start, "fit_end": fit_end - pd.Timedelta("1ns"),
                "component_rule": {"mode": "fixed", "value": n_components},
            })
            blocks.append(compressed.x)
            states[name] = compressed.state
            audits[name] = compressed.audit
    # DataFrame attrs may contain array-valued diagnostics; do not compare them during concat.
    for block in blocks:
        block.attrs = {}
    x = pd.concat(blocks, axis=1)
    if x.columns.has_duplicates:
        raise ValueError("compressed and passthrough feature names collide")
    x.attrs.update(processed.x.attrs)
    if any(s is not None for s in states.values()):
        x.attrs.update(compression_method="pca", compression_fit_end=str(fit_end - pd.Timedelta("1ns")))
    return x, processed.state, states, {"zscore": processed.audit, "grouping": grouped.audit, "pca": audits}


def _transform(ds: ResearchDataset, cfg: Mapping, zstate, pca_states) -> pd.DataFrame:
    x = transform_zscore(ds.x, zstate)
    grouped = extract_factor_groups(x, {"groups": cfg["groups"], "unassigned_policy": "error"})
    blocks = []
    for name, values in grouped.groups.items():
        state = pca_states[name]
        block = transform_factor_group(values, state) if state is not None else values.copy()
        block.attrs = {}
        blocks.append(block)
    result = pd.concat(blocks, axis=1)
    result.attrs.update(x.attrs)
    if any(s is not None for s in pca_states.values()):
        result.attrs.update(compression_method="pca", compression_fit_end=str(zstate.fit_end))
    return result


def run_research(train_valid: ResearchDataset, test_loader: Callable[[], ResearchDataset],
                 config: Mapping, output: Path, *, synthetic: bool = False) -> dict:
    cfg = validate_config(config)
    if output.exists():
        raise FileExistsError("run directory already exists; choose a new directory for provenance")
    output.mkdir(parents=True)
    write_json(output / "config.json", cfg)
    dt = train_valid.x.index.get_level_values("Datetime")
    if (dt < pd.Timestamp(cfg["intervals"]["fit"][0])).any() or (dt >= pd.Timestamp(cfg["intervals"]["valid"][1])).any():
        raise ValueError("training input must contain only the declared Fit/Valid span")
    ds = _sample(train_valid, cfg)
    ds, split_audit = _purge(ds, cfg, ("fit", "valid"))
    x, zstate, pca_states, processing_audit = _prepare(ds, cfg)
    joblib.dump(zstate, output / "zscore_state.joblib")
    for state in pca_states.values():
        if state is not None:
            save_pca_state(state, output / "pca_states")
    zstate.as_frame().to_csv(output / "zscore_fit_state.csv")
    final_models, selections = {}, {}
    for method in cfg["models"]:
        model_config = make_training_config(method, random_seed=int(cfg["seed"]))
        model_config["live_plot"]["enabled"] = False
        model_config["params"].update(cfg.get("model_params", {}).get(method, {}))
        common = dict(x=x, y=ds.y, target=ds.y.name, method=method,
                      fit_start=cfg["intervals"]["fit"][0], fit_end=cfg["intervals"]["fit"][1],
                      valid_start=cfg["intervals"]["valid"][0], valid_end=cfg["intervals"]["valid"][1],
                      config=model_config, label_end=ds.label_end, feature_available_at=ds.feature_available_at)
        selected = train_factor_model(training_stage="selection",
                                      training_iterations=int(cfg.get("iterations", {}).get(method, 8)), **common)
        final = train_factor_model(training_stage="final", training_iterations=int(selected.best_iteration),
                                   output_dir=output / "models", **common)
        final_models[method] = final
        selections[method] = {"iterations": int(selected.best_iteration), "audit": selected.audit}
    write_json(output / "selection.json", selections)
    # Deferred until all preprocessing and model choices above have been frozen.
    raw_test = test_loader()
    test_times = raw_test.x.index.get_level_values("Datetime")
    t0, t1 = map(pd.Timestamp, cfg["intervals"]["test"])
    if (test_times < t0).any() or (test_times >= t1).any():
        raise ValueError("Test file contains observations outside its declared half-open interval")
    test = _sample(raw_test, cfg)
    test, test_split_audit = _purge(test, cfg, ("test",))
    xt = _transform(test, cfg, zstate, pca_states)
    metrics, signal_blocks = [], []
    for method, model in final_models.items():
        result = evaluate_factor_model(model, xt, test.y, target=test.y.name,
                                        label_end=test.label_end, feature_available_at=test.feature_available_at,
                                        output_dir=output / "evaluation", rolling_window=24, block_size=48)
        metrics.append({"model": method, **result.overall_metrics.to_dict()})
        signals = result.predictions[["prediction"]].reset_index()
        signals["signal_available_at"] = signals["Datetime"]
        signals["model"] = method
        signals["target_name"] = str(test.y.name)
        signals["synthetic"] = synthetic
        signal_blocks.append(signals)
    pd.DataFrame(metrics).to_csv(output / "model_comparison.csv", index=False)
    pd.concat(signal_blocks, ignore_index=True).to_csv(output / "signals.csv", index=False)
    packages = ["numpy", "pandas", "scikit-learn", "pyarrow", "xgboost", "lightgbm", "torch", "threadpoolctl"]
    versions = {}
    for name in packages:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    source = {}
    for package in (Path(__file__).parent, Path(__file__).parent.parent / "quant_research",
                    Path(__file__).parent.parent / "ashare_factor_lab"):
        for path in sorted(package.rglob("*.py")):
            source[str(path.relative_to(package.parent))] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "status": "complete", "synthetic": synthetic, "python": platform.python_version(),
        "versions": versions, "source_sha256": source,
        "input_fingerprints": {
            name: {"features": frame_fingerprint(value.x), "target": frame_fingerprint(value.y.to_frame()),
                   "timing": frame_fingerprint(pd.concat([value.feature_available_at, value.label_end], axis=1))}
            for name, value in (("raw_train_valid", train_valid), ("used_train_valid", ds),
                                ("raw_test", raw_test), ("used_test", test))},
        "split_audit": split_audit, "test_split_audit": test_split_audit,
        "processing_audit": processing_audit, "models": cfg["models"],
        "test_loader_called_after_all_models_frozen": True,
        "caller_prior_test_access": "not_observable",
        "pipeline_uses_test_to_choose_model": False, "backtest_implemented": False,
        "artifacts": {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted(output.rglob("*")) if p.is_file()},
    }
    write_json(output / "manifest.json", manifest)
    return manifest
