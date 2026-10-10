"""Sector CTA research orchestration; source-inspired, with explicit timing contracts.

Source S18 supplies the LightGBM/sector prototype. Multiple-model rank ensembling,
propagated timing, and a separate execution-return loader are public extensions.
No original market data, model weights or Notebook outputs are distributed.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.metadata
from pathlib import Path
import platform
import re
from typing import Callable, Mapping

import numpy as np
import pandas as pd

from .cta_sector import prepare_sector_dataset, augment_fit_samples
from .cta_portfolio import ensemble_predictions, build_sector_weights, research_backtest
from .factor_model_training import make_training_config, train_factor_model
from .factor_model_testing import evaluate_factor_model
from .factor_runtime import frame_fingerprint
from .pipeline import ResearchDataset, _purge, _sample, validate_config, write_json


def _fingerprint(ds):
    return {"x": frame_fingerprint(ds.x), "y": frame_fingerprint(ds.y.to_frame()),
            "timing": frame_fingerprint(pd.concat([ds.feature_available_at.rename("feature_available_at"),
                                                    ds.label_end.rename("label_end")], axis=1))}


def _span(ds, interval, name):
    ds.validate()
    times = pd.DatetimeIndex(ds.x.index.get_level_values("Datetime"))
    start, end = map(pd.Timestamp, interval)
    if len(times) == 0 or (times < start).any() or (times >= end).any():
        raise ValueError(f"{name} contains rows outside its declared half-open span")


def _metadata_timing(ds, metadata):
    available = pd.Series(metadata.metadata_available_at.to_numpy(), index=metadata.Contract)
    known_at = ds.x.index.get_level_values("Contract").map(available)
    if pd.isna(known_at).any() or (known_at > ds.x.index.get_level_values("Datetime")).any():
        raise ValueError("contract metadata must be known at every historical sample timestamp")


def run_sector_strategy(train_valid: ResearchDataset, test_loader: Callable[[], ResearchDataset],
                        execution_returns_loader: Callable[[], pd.DataFrame],
                        config: Mapping, output: Path, *, synthetic: bool = False) -> dict:
    """Freeze all sector models before Test, build weights before loading execution returns.

    The raw y must be an uncentered 30-minute forward simple return. Exact input
    provenance/price definition remains the caller's responsibility. Default
    selection uses Valid raw-return IC, following S18, but never copies its old
    manually entered iteration counts. Missing model forecasts (e.g. sequence
    warmup) require agreement of all configured models before entering the book.
    """
    cfg = validate_config(deepcopy(config))
    if not set(cfg["models"]) <= {"xgboost", "lightgbm", "transformer", "itransformer"}:
        raise ValueError("sector strategy supports the four explicitly documented model backends")
    if cfg.get("raw_target_definition") != "uncentered_forward_simple_return_30m":
        raise ValueError("declare the uncentered 30-minute raw target; do not pass pre-centered Tag")
    metadata = pd.DataFrame(cfg["contract_metadata"])
    required = {"Contract", "sector", "product", "role", "metadata_available_at"}
    if not required.issubset(metadata.columns) or metadata.Contract.duplicated().any() or metadata[list(required)].isna().any().any():
        raise ValueError("contract_metadata requires unique Contract/sector/product/role and metadata_available_at")
    metadata["metadata_available_at"] = pd.to_datetime(metadata.metadata_available_at, errors="raise")
    if metadata.metadata_available_at.isna().any() or metadata.metadata_available_at.dt.tz is not None:
        raise ValueError("metadata_available_at must be finite timezone-naive timestamps")
    if not set(metadata.role) <= {"main", "secondary"}:
        raise ValueError("contract metadata role must be main or secondary")
    sectors = sorted(metadata.sector.unique())
    if not sectors or any(not isinstance(s, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", s) for s in sectors):
        raise ValueError("sector IDs must be safe lowercase identifiers")
    mapping = dict(zip(metadata.Contract, metadata.sector))
    weights = cfg["model_weights"]
    allocations = cfg["sector_allocations"]
    if set(weights) != set(cfg["models"]) or any(not np.isfinite(v) or v <= 0 for v in weights.values()) or not np.isclose(sum(weights.values()), 1):
        raise ValueError("fixed positive model weights must match the model list and sum to one")
    if set(allocations) != set(sectors) or any(not np.isfinite(v) or v <= 0 for v in allocations.values()) or not np.isclose(sum(allocations.values()), 1):
        raise ValueError("fixed positive sector allocations must match metadata and sum to one")
    selection_rule = cfg.get("selection_rule", "validation_raw_ic")
    if selection_rule not in {"validation_raw_ic", "validation_rmse"}:
        raise ValueError("selection_rule must be chosen before Test")
    _span(train_valid, [cfg["intervals"]["fit"][0], cfg["intervals"]["valid"][1]], "Train/Valid")
    _metadata_timing(train_valid, metadata)
    output = Path(output)
    if output.exists():
        raise FileExistsError("choose a new run directory for provenance")
    output.mkdir(parents=True)
    write_json(output / "config.json", cfg)
    # Propagate contemporaneous peer dependencies before sampling/purging.
    prepared = prepare_sector_dataset(train_valid, mapping, target_mode=cfg.get("target_mode", "sector_median_excess"))
    used, split_audit = _purge(_sample(prepared, cfg), cfg, ("fit", "valid"))
    finals, selection, augmentation = {}, {}, {}
    for sector in sectors:
        mask = used.x.index.get_level_values("Contract").map(mapping) == sector
        base = used.subset(used.x.index[mask])
        augmented, audit = augment_fit_samples(
            base, mapping, fit_start=cfg["intervals"]["fit"][0], fit_end=cfg["intervals"]["fit"][1],
            n_synthetic_per_sector=int(cfg.get("augmentation", {}).get("n_synthetic_per_sector", 0)),
            alpha=float(cfg.get("augmentation", {}).get("alpha", 1.0)), seed=int(cfg["seed"]))
        augmentation[sector] = audit
        raw_ic = train_valid.y.reindex(augmented.x.index).rename("raw_return_30m")
        for method in cfg["models"]:
            model_cfg = make_training_config(method, random_seed=int(cfg["seed"]))
            model_cfg["live_plot"]["enabled"] = False
            model_cfg["params"].update(cfg.get("model_params", {}).get(method, {}))
            common = dict(x=augmented.x, y=augmented.y, target=augmented.y.name, method=method,
                          fit_start=cfg["intervals"]["fit"][0], fit_end=cfg["intervals"]["fit"][1],
                          valid_start=cfg["intervals"]["valid"][0], valid_end=cfg["intervals"]["valid"][1],
                          config=model_cfg, ic_y=raw_ic, feature_available_at=augmented.feature_available_at,
                          label_end=augmented.label_end)
            selected = train_factor_model(training_stage="selection", training_iterations=int(cfg["iterations"][method]), **common)
            count = selected.best_validation_ic_iteration if selection_rule == "validation_raw_ic" else selected.best_iteration
            if count is None:
                raise ValueError(f"{sector}/{method} has no defined validation selection criterion")
            key = f"{sector}/{method}"
            finals[key] = train_factor_model(training_stage="final", training_iterations=int(count),
                                             output_dir=output / "models" / sector / method, **common)
            selection[key] = {"iterations": int(count), "selection_rule": selection_rule, "audit": selected.audit}
    write_json(output / "selection.json", selection)
    raw_test = test_loader()  # All sectors and model configurations are frozen above.
    _span(raw_test, cfg["intervals"]["test"], "Test")
    _metadata_timing(raw_test, metadata)
    test_prepared = prepare_sector_dataset(raw_test, mapping, target_mode=cfg.get("target_mode", "sector_median_excess"))
    test, test_audit = _purge(_sample(test_prepared, cfg), cfg, ("test",))
    signals, metrics = [], []
    for sector in sectors:
        mask = test.x.index.get_level_values("Contract").map(mapping) == sector
        ds = test.subset(test.x.index[mask])
        for method in cfg["models"]:
            result = evaluate_factor_model(finals[f"{sector}/{method}"], ds.x, ds.y, target=ds.y.name,
                                            ic_y=raw_test.y.loc[ds.x.index], label_end=ds.label_end,
                                            feature_available_at=ds.feature_available_at,
                                            output_dir=output / "evaluation" / sector / method,
                                            rolling_window=24, block_size=48)
            # Never use actual or pairwise_finite to define portfolio eligibility.
            frame = result.predictions[["prediction"]].reset_index()
            frame["sector"], frame["model"] = sector, method
            frame["signal_available_at"] = frame.Datetime
            signals.append(frame)
            metrics.append({"sector": sector, "model": method, **result.overall_metrics.to_dict()})
    all_signals = pd.concat(signals, ignore_index=True)
    keys = ["Date", "Contract", "Datetime", "sector"]
    all_signals["_prediction_available"] = np.isfinite(all_signals.prediction)
    eligible = all_signals.groupby(keys, sort=False)["_prediction_available"].transform("all")
    usable = all_signals.loc[eligible].drop(columns="_prediction_available")
    if usable.empty:
        raise ValueError("no samples have forecasts from every frozen model")
    ensemble = ensemble_predictions(usable, model_weights=weights)
    positions = build_sector_weights(ensemble, metadata, sector_allocations=allocations,
                                     long_quantile=cfg.get("long_quantile", .8),
                                     short_quantile=cfg.get("short_quantile", .2),
                                     contract_set=cfg.get("contract_set", "both"))
    # Returns enter only after the book is frozen; missing future values cannot select instruments.
    execution_returns = execution_returns_loader().copy()
    for column in ("Date", "Datetime"):
        execution_returns[column] = pd.to_datetime(execution_returns[column], errors="raise")
    # Sequence warmup may omit whole bars. Restrict returns by the already-fixed
    # signal bar universe, never restrict the portfolio using realized returns.
    execution_subset = execution_returns.merge(positions[["Date", "Datetime"]].drop_duplicates(),
                                               on=["Date", "Datetime"], how="inner", validate="many_to_one")
    execution_subset = execution_subset.sort_values(["Datetime", "Date", "Contract"])
    bt = research_backtest(positions, execution_subset,
                          commission_bps=cfg.get("commission_bps", .2), slippage_bps=cfg.get("slippage_bps", 1.0))
    usable.to_csv(output / "model_signals.csv", index=False)
    ensemble.to_csv(output / "ensemble_signals.csv", index=False)
    positions.to_csv(output / "target_weights.csv", index=False)
    pd.DataFrame(metrics).to_csv(output / "model_metrics.csv", index=False)
    bt.curves.to_csv(output / "research_curve.csv", index=False)
    write_json(output / "research_metrics.json", bt.metrics)
    write_json(output / "backtest_audit.json", bt.audit)
    versions = {name: importlib.metadata.version(name) for name in ("numpy", "pandas", "scikit-learn")}
    for method, package in (("xgboost", "xgboost"), ("lightgbm", "lightgbm"), ("transformer", "torch"), ("itransformer", "torch")):
        if method in cfg["models"]:
            versions[package] = importlib.metadata.version(package)
    manifest = {"status": "complete", "synthetic": synthetic, "source_id": "S18", "target_mode": cfg.get("target_mode", "sector_median_excess"),
                "model_weights_frozen_before_test": weights, "sector_allocations": allocations,
                "all_sector_models_frozen_before_test_loader": True,
                "weights_built_before_execution_returns_loader": True,
                "caller_prior_test_access": "not_observable", "selection": selection,
                "excluded_model_rows_without_all_predictions": int((~eligible).sum()),
                "prediction_eligibility_uses_future_returns": False,
                "contract_metadata_timing_checked_on_all_inputs": True,
                "contract_metadata_scope": "fixed_mapping_over_declared_run",
                "ensemble": ensemble.attrs.get("ensemble_audit", {}),
                "portfolio": positions.attrs.get("portfolio_audit", {}),
                "augmentation": augmentation, "split_audit": split_audit, "test_audit": test_audit,
                "input_fingerprints": {"raw_train_valid": _fingerprint(train_valid), "raw_test": _fingerprint(raw_test),
                                       "used_train_valid": _fingerprint(used), "used_test": _fingerprint(test),
                                       "execution_returns": frame_fingerprint(execution_returns)},
                "research_backtest": bt.audit, "full_execution_engine": False,
                "signal_latency_assumption": "zero_compute_and_transport_latency_requires_external_validation",
                "versions": versions,
                "runtime": {"python": platform.python_version(), "system": platform.system(),
                            "machine": platform.machine()},
                "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob("*.py")},
                "artifacts": {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in sorted(output.rglob("*")) if p.is_file()}}
    write_json(output / "manifest.json", manifest)
    return manifest
