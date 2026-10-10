#_pearson_ic 的单核约束我保留了（没关）。理由：1-D 点积开多线程零收益，关掉既不能提速、又会重新引入瞬时叠加。节流 + 单核是互补的两道防线，各管一段。
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import importlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import random
import tempfile
import time
from typing import Any, Callable, Iterator, Mapping, MutableMapping, Optional, Sequence, Union

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype

from .factor_runtime import (
    temporal_operation, temporal_metadata_for, temporal_fit_cutoff, validate_preprocessing_boundary,
    CPU_THREADS,
    configure_torch_threads,
    enforce_model_thread_config,
    frame_fingerprint,
    index_fingerprint as _index_fingerprint,
    jsonable as _jsonable,
    limit_numeric_threads,
    runtime_thread_snapshot,
    series_fingerprint as _target_fingerprint,
    sha256_file as _sha256_file,
    sha256_json as _sha256_json,
)


__all__ = [
    "CPU_THREADS",
    "EXPECTED_INDEX_NAMES",
    "TrainingResult",
    "available_training_methods",
    "make_training_config",
    "register_training_method",
    "train_factor_model",
    "validate_training_inputs",
]

__version__ = "3.2.0"

EXPECTED_INDEX_NAMES = ("Date", "Contract", "Datetime")
Target = Union[pd.Series, pd.DataFrame]


@dataclass(frozen=True)
class _SplitData:
    name: str
    X: np.ndarray
    y: np.ndarray
    index: pd.MultiIndex
    configured_start: pd.Timestamp
    configured_end: pd.Timestamp
    ic_y: Optional[np.ndarray] = None


@dataclass
class _FitOutcome:
    model: Any
    history: pd.DataFrame
    best_iteration: int
    best_valid_loss: float
    best_valid_ic_iteration: Optional[int]
    best_valid_ic: float
    metadata: dict[str, Any]


@dataclass
class _FrozenOutcome:
    model: Any
    history: pd.DataFrame
    metadata: dict[str, Any]


@dataclass(frozen=True)
class TrainingResult:
    """Complete output of :func:`train_factor_model`.

    For ``training_stage="selection"``, ``model`` and ``selection_model`` are
    the Fit-only model restored/limited to the selected Validation iteration.

    For ``training_stage="final"``, ``model`` is the freshly fitted
    Fit+Validation model and ``selection_model`` is ``None`` because this call
    performs no Validation selection.
    """

    model: Any
    selection_model: Optional[Any]
    method: str
    target: str
    training_stage: str
    requested_iterations: int
    feature_names: tuple[str, ...]
    history: pd.DataFrame
    frozen_history: pd.DataFrame
    best_iteration: int
    best_validation_loss: Optional[float]
    best_validation_loss_iteration: Optional[int]
    best_validation_ic: Optional[float]
    best_validation_ic_iteration: Optional[int]
    best_params: Mapping[str, Any]
    refit_full: bool
    artifact_paths: Mapping[str, str]
    audit: Mapping[str, Any]

    @property
    def train_loss(self) -> pd.Series:
        if self.training_stage == "selection":
            return self.history.set_index("iteration")["train_rmse"].copy()
        return self.frozen_history.set_index("iteration")["full_train_rmse"].copy()

    @property
    def valid_loss(self) -> pd.Series:
        if self.training_stage == "selection":
            return self.history.set_index("iteration")["valid_rmse"].copy()
        return pd.Series(dtype=np.float64, name="valid_rmse")

    @property
    def valid_ic(self) -> pd.Series:
        if self.training_stage == "selection":
            return self.history.set_index("iteration")["valid_pearson_ic"].copy()
        return pd.Series(dtype=np.float64, name="valid_pearson_ic")

    def __iter__(self) -> Iterator[Any]:
        """Allow ``model, train_loss, valid_loss, best_params = result``."""
        yield self.model
        yield self.train_loss
        yield self.valid_loss
        yield self.best_params


def _safe_path_component(value: str) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in {"-", "_"} else "_"
        for character in value
    ).strip("_")
    return cleaned or "artifact"


def _save_final_artifacts(
    *,
    model: Any,
    method: str,
    target: str,
    feature_names: Sequence[str],
    config: Mapping[str, Any],
    training_iterations: int,
    history: pd.DataFrame,
    audit: Mapping[str, Any],
    run_id: str,
    output_dir: Union[str, os.PathLike[str]],
) -> tuple[dict[str, str], dict[str, str]]:
    """Atomically publish one native final model plus history and manifest."""
    output_root = Path(output_dir).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    temporary_directory = Path(
        tempfile.mkdtemp(prefix=".training_tmp_", dir=str(output_root))
    )
    model_filenames = {
        "xgboost": "model.json",
        "lightgbm": "model.txt",
        "catboost": "model.cbm",
        "itransformer": "model_state.pt",
        "transformer": "model_state.pt",
    }
    if method not in model_filenames:
        raise NotImplementedError(
            f"final artifact saving is not implemented for method={method!r}"
        )

    model_path = temporary_directory / model_filenames[method]
    history_path = temporary_directory / "full_train_history.csv"
    manifest_path = temporary_directory / "manifest.json"

    try:
        if method == "xgboost":
            model.save_model(str(model_path))
        elif method == "lightgbm":
            model.save_model(str(model_path), num_iteration=training_iterations)
        elif method == "catboost":
            model.save_model(str(model_path), format="cbm")
        else:
            torch = _require_dependency("torch", method)
            state_dict = {
                name: tensor.detach().cpu()
                for name, tensor in model.state_dict().items()
            }
            torch.save(
                {
                    "format_version": 1,
                    "method": method,
                    "target": target,
                    "training_iterations": int(training_iterations),
                    "feature_names": list(feature_names),
                    "params": deepcopy(dict(config["params"])),
                    "state_dict": state_dict,
                },
                model_path,
            )

        history.to_csv(history_path, index=False)
        hashes = {
            "model_sha256": _sha256_file(model_path),
            "history_sha256": _sha256_file(history_path),
        }
        manifest = {
            "format_version": 1,
            "run_id": run_id,
            "method": method,
            "target": target,
            "training_stage": "final",
            "training_iterations": int(training_iterations),
            "model_file": model_path.name,
            "history_file": history_path.name,
            "hashes": hashes,
            "feature_names": list(feature_names),
            "effective_config": deepcopy(dict(config)),
            "audit": deepcopy(dict(audit)),
        }
        manifest_path.write_text(
            json.dumps(_jsonable(manifest), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        hashes["manifest_sha256"] = _sha256_file(manifest_path)

        stem = (
            f"{_safe_path_component(method)}_"
            f"{_safe_path_component(target)}_{run_id}"
        )
        final_directory = output_root / stem
        suffix = 1
        while final_directory.exists():
            final_directory = output_root / f"{stem}_{suffix:02d}"
            suffix += 1
        os.replace(temporary_directory, final_directory)

        paths = {
            "directory": str(final_directory),
            "model": str(final_directory / model_path.name),
            "history": str(final_directory / history_path.name),
            "manifest": str(final_directory / manifest_path.name),
        }
        return paths, hashes
    except Exception:
        # Preserve a failed save for forensic inspection instead of deleting
        # files that may explain serialization failure.
        failed_path = temporary_directory.with_name(
            temporary_directory.name.replace(".training_tmp_", "failed_training_")
        )
        if temporary_directory.exists() and not failed_path.exists():
            os.replace(temporary_directory, failed_path)
        raise


def _require_dependency(module_name: str, method: str) -> Any:
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        raise ImportError(
            f"method={method!r} requires the optional package {module_name!r}. "
            f"Install it in the Jupyter kernel environment before training."
        ) from exc


def _package_version(package: str) -> Optional[str]:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def validate_training_inputs(
    x: pd.DataFrame,
    y: Target,
    target: Optional[str],
) -> tuple[pd.Series, pd.DatetimeIndex, tuple[str, ...]]:
    """Validate and select the label without sorting or silently aligning."""
    if not isinstance(x, pd.DataFrame):
        raise TypeError(f"x must be a pandas DataFrame, got {type(x)!r}")
    if not isinstance(y, (pd.Series, pd.DataFrame)):
        raise TypeError(f"y must be a pandas Series or DataFrame, got {type(y)!r}")

    for name, obj in (("x", x), ("y", y)):
        if not isinstance(obj.index, pd.MultiIndex):
            raise TypeError(f"{name}.index must be a pandas MultiIndex")
        if tuple(obj.index.names) != EXPECTED_INDEX_NAMES:
            raise ValueError(
                f"{name} index names must be {list(EXPECTED_INDEX_NAMES)}, "
                f"got {list(obj.index.names)}"
            )
        if not obj.index.is_unique:
            raise ValueError(f"{name} contains duplicate Date + Contract + Datetime keys")

    if not x.index.equals(y.index):
        raise ValueError(
            "x and y MultiIndex values/order differ. Training never intersects "
            "or reindexes them automatically because that can hide label leakage."
        )
    if len(x) == 0:
        raise ValueError("x/y cannot be empty")
    if not x.columns.is_unique:
        duplicates = x.columns[x.columns.duplicated()].tolist()
        raise ValueError(f"x contains duplicate factor columns: {duplicates[:20]}")
    if any(not isinstance(column, str) for column in x.columns):
        raise TypeError("all x factor column names must be strings")
    if len(set(x.columns)) != len(x.columns):
        raise ValueError("x factor names are not unique")

    non_numeric = [column for column in x.columns if not is_numeric_dtype(x[column])]
    if non_numeric:
        raise TypeError(f"x contains non-numeric factor columns: {non_numeric[:20]}")

    if isinstance(y, pd.DataFrame):
        if target is None:
            raise ValueError(
                "target must be specified because y is a DataFrame, for example "
                "target='ret_30m'"
            )
        if target not in y.columns:
            raise KeyError(
                f"target {target!r} is absent from y. Available columns include: "
                f"{list(y.columns[:20])}"
            )
        selected_y = y[target]
    else:
        if target is not None and y.name is not None and target != str(y.name):
            raise ValueError(
                f"target={target!r} does not match y.name={y.name!r}"
            )
        selected_y = y
        target = str(y.name) if y.name is not None else "target"

    if not is_numeric_dtype(selected_y):
        raise TypeError(f"selected target {target!r} must be numeric")

    datetimes = pd.DatetimeIndex(x.index.get_level_values("Datetime"))
    if datetimes.isna().any():
        raise ValueError("Datetime index contains NaT")
    if str(selected_y.name) in x.columns:
        raise ValueError("label leakage: target name appears among feature columns")
    return selected_y, datetimes, tuple(x.columns)


def _extract_ic_series(ic_y: Any, x: pd.DataFrame) -> pd.Series:
    """Extract an independent IC-evaluation label aligned exactly with x."""
    if isinstance(ic_y, pd.DataFrame):
        if ic_y.shape[1] != 1:
            raise ValueError("ic_y DataFrame must have exactly one column")
        series = ic_y.iloc[:, 0]
    elif isinstance(ic_y, pd.Series):
        series = ic_y
    else:
        raise TypeError("ic_y must be a pandas Series or single-column DataFrame")
    if not series.index.equals(x.index):
        raise ValueError(
            "ic_y index must exactly match x index (same sampling and row mask)"
        )
    if not is_numeric_dtype(series):
        raise TypeError("ic_y must be numeric")
    return series


def _normalise_bounds(
    fit_start: Any,
    fit_end: Any,
    valid_start: Any,
    valid_end: Any,
) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    values = tuple(pd.Timestamp(v) for v in (fit_start, fit_end, valid_start, valid_end))
    if any(pd.isna(v) for v in values):
        raise ValueError("Fit/Validation bounds cannot contain NaT")
    fs, fe, vs, ve = values
    if fs >= fe:
        raise ValueError(f"fit_start must be earlier than exclusive fit_end: {fs} >= {fe}")
    if vs >= ve:
        raise ValueError(
            f"valid_start must be earlier than exclusive valid_end: {vs} >= {ve}"
        )
    if fe > vs:
        raise ValueError(
            "half-open Fit and Validation intervals overlap: "
            f"fit_end={fe}, valid_start={vs}"
        )
    return fs, fe, vs, ve


def _make_split(
    name: str,
    mask: np.ndarray,
    x: pd.DataFrame,
    selected_y: pd.Series,
    ic_label: Optional[pd.Series],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> _SplitData:
    row_count = int(mask.sum())
    if row_count == 0:
        raise ValueError(f"{name} contains zero rows in configured interval {start} -> {end}")

    if bool(mask.all()):
        X = x.to_numpy(dtype=np.float32, na_value=np.nan, copy=True)
    else:
        # Allocate the final float32 matrix once.  Building ``x.loc[mask]``
        # first would keep a second, often float64, multi-gigabyte DataFrame
        # alive during conversion.
        positions = np.flatnonzero(mask)
        X = np.empty((row_count, x.shape[1]), dtype=np.float32)
        for left in range(0, x.shape[1], 64):
            right = min(left + 64, x.shape[1])
            block = x.iloc[:, left:right].to_numpy(
                dtype=np.float32,
                na_value=np.nan,
                copy=False,
            )
            X[:, left:right] = block[positions]
    for left in range(0, X.shape[1], 64):
        block = X[:, left : left + 64]
        block[~np.isfinite(block)] = np.nan
    source_y = selected_y.to_numpy(dtype=np.float64, na_value=np.nan, copy=False)
    y_values = source_y.copy() if bool(mask.all()) else source_y[mask]
    y_values[~np.isfinite(y_values)] = np.nan
    # 独立的 IC 评估标签：与训练 label 用同一个 mask 过滤，保证 prediction 与 ic 行对齐。
    # ic 自身的 NaN 不在此处理，交由 _pearson_ic 内部成对丢弃。
    ic_values: Optional[np.ndarray] = None
    if ic_label is not None:
        source_ic = ic_label.to_numpy(dtype=np.float64, na_value=np.nan, copy=False)
        ic_values = source_ic.copy() if bool(mask.all()) else source_ic[mask]
    return _SplitData(
        name=name,
        X=X,
        y=y_values,
        index=x.index[mask],
        configured_start=start,
        configured_end=end,
        ic_y=ic_values,
    )


def _missing_value_count(
    values: np.ndarray,
    row_mask: Optional[np.ndarray] = None,
    *,
    chunk_rows: int = 8_192,
) -> int:
    """Count NaNs with bounded temporary memory for a row subset."""
    total = 0
    for start in range(0, len(values), chunk_rows):
        block = values[start : start + chunk_rows]
        if row_mask is not None:
            block = block[row_mask[start : start + chunk_rows]]
        total += int(np.count_nonzero(np.isnan(block)))
    return total


def _split_audit_from_matrix(
    split: _SplitData,
    *,
    name: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    role: str,
    row_mask: Optional[np.ndarray] = None,
) -> dict[str, Any]:
    """Build one backward-compatible split record from a shared matrix."""
    if row_mask is None:
        index = split.index
        labels = split.y
        row_count = len(split.X)
    else:
        index = split.index[row_mask]
        labels = split.y[row_mask]
        row_count = int(np.count_nonzero(row_mask))
    if row_count == 0:
        raise RuntimeError(f"internal error: {name} audit contains zero rows")
    datetimes = pd.DatetimeIndex(index.get_level_values("Datetime"))
    return {
        "configured_start": start.isoformat(),
        "configured_end": end.isoformat(),
        "interval_semantics": "[start, end)",
        "actual_start": datetimes.min().isoformat(),
        "actual_end": datetimes.max().isoformat(),
        "source_rows": int(row_count),
        "finite_target_rows": int(np.isfinite(labels).sum()),
        "nonfinite_target_rows": int((~np.isfinite(labels)).sum()),
        "missing_factor_values": _missing_value_count(split.X, row_mask),
        "index_fingerprint": _index_fingerprint(index),
        "role_in_this_stage": role,
    }


def _finite_label_rows(split: _SplitData) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    valid = np.isfinite(split.y)
    if not valid.any():
        raise ValueError(f"{split.name} has no finite labels")
    ic = split.ic_y if split.ic_y is not None else split.y
    if valid.all():
        return split.X, split.y, ic, 0
    return split.X[valid], split.y[valid], ic[valid], int((~valid).sum())


def _combine_finite_parts(
    parts: Sequence[_SplitData],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """Combine training parts without copying the common one-part final path."""
    if not parts:
        raise ValueError("at least one training part is required")
    prepared = [_finite_label_rows(part) for part in parts]
    dropped = sum(item[3] for item in prepared)
    if len(prepared) == 1:
        return prepared[0][0], prepared[0][1], prepared[0][2], dropped
    return (
        np.concatenate([item[0] for item in prepared], axis=0),
        np.concatenate([item[1] for item in prepared], axis=0),
        np.concatenate([item[2] for item in prepared], axis=0),
        dropped,
    )


def _default_common(
    method: str,
    random_seed: int,
) -> dict[str, Any]:
    return {
        "method": method,
        "selection_metric": "rmse",
        "selection_data": "valid",
        "live_plot": {
            "enabled": True,
            "update_every": 25,
        },
        "validation_ic": {
            # Signed pooled Pearson correlation across all finite Valid rows.
            # The model checkpoint is still selected by minimum Valid RMSE.
            "method": "pearson",
        },
        "random_seed": random_seed,
    }


def make_training_config(method: str, random_seed: int = 42) -> dict[str, Any]:
    """Return an independent, commented-by-key configuration dictionary.

    Edit the returned dictionary in the Notebook; never mutate a shared global
    default. The iteration count is deliberately not stored here: pass it as
    ``training_iterations`` to :func:`train_factor_model`. It means boosting
    rounds for tree models and epochs for iTransformer/Transformer.
    """
    normalised = method.lower().strip().replace(" ", "")
    aliases = {"xgb": "xgboost", "lgb": "lightgbm", "lgbm": "lightgbm", "cat": "catboost"}
    normalised = aliases.get(normalised, normalised)

    if normalised == "xgboost":
        config = _default_common(normalised, random_seed)
        config["params"] = {
            "objective": "reg:squarederror",  # squared-error regression
            "eval_metric": "rmse",            # Train/Valid curve metric
            "tree_method": "hist",            # histogram tree builder
            "device": "cpu",                  # change explicitly for GPU
            "max_bin": 200,                    # histogram bins
            "eta": 0.001,                      # learning rate
            "max_depth": 2,                    # maximum tree depth
            "subsample": 1.0,                  # row sampling ratio
            "colsample_bytree": 1.0,           # factor sampling ratio
            "reg_alpha": 0.0,                  # L1 regularisation
            "reg_lambda": 1.0,                 # L2 regularisation
            "seed": random_seed,
            "nthread": CPU_THREADS,
        }
        return config

    if normalised == "lightgbm":
        config = _default_common(normalised, random_seed)
        config["params"] = {
            "objective": "regression",         # L2 regression objective
            "metric": "rmse",                 # Train/Valid curve metric
            "boosting_type": "gbdt",
            "learning_rate": 0.01,
            "num_leaves": 31,
            "max_depth": -1,                   # -1 means no explicit limit
            "min_data_in_leaf": 20,
            "feature_fraction": 1.0,
            "bagging_fraction": 1.0,
            "bagging_freq": 0,
            "lambda_l1": 0.0,
            "lambda_l2": 0.0,
            "verbosity": -1,
            "seed": random_seed,
            "num_threads": CPU_THREADS,
            "deterministic": True,
            "force_col_wise": True,
        }
        return config

    if normalised == "catboost":
        config = _default_common(normalised, random_seed)
        config["params"] = {
            "loss_function": "RMSE",           # optimisation loss
            "eval_metric": "RMSE",             # Train/Valid curve metric
            "learning_rate": 0.01,
            "depth": 6,
            "l2_leaf_reg": 3.0,
            "random_seed": random_seed,
            "thread_count": CPU_THREADS,
            "task_type": "CPU",
            "allow_writing_files": False,
            "verbose": False,
        }
        return config

    if normalised in {"itransformer", "transformer"}:
        config = _default_common(normalised, random_seed)
        config["params"] = {
            "window_size": 3,                   # sampled rows, not clock minutes
            "d_model": 8,
            "n_heads": 4,
            "encoder_layers": 2,
            "feedforward_dim": 32,
            "dropout": 0.1,
            "learning_rate": 1e-3,
            "weight_decay": 1e-4,
            "batch_size": 256,
            "device": "cpu",                   # auto/cpu/cuda/mps
            "thread_count": CPU_THREADS,        # PyTorch intra-op CPU threads
            "missing_value_strategy": "zero",  # zero is Fit-mean after Z-score
            "allow_cross_day": False,
            "allow_cross_session": True,
            "gradient_clip_norm": 1.0,
            "num_workers": 0,
        }
        return config

    raise KeyError(
        f"unknown method {method!r}; built-ins are xgboost, lightgbm, "
        "catboost, itransformer, transformer"
    )


def _canonical_method(method: str) -> str:
    normalised = method.lower().strip().replace(" ", "")
    return {
        "xgb": "xgboost",
        "lgb": "lightgbm",
        "lgbm": "lightgbm",
        "cat": "catboost",
    }.get(normalised, normalised)


def _canonical_training_stage(training_stage: Union[str, int]) -> str:
    """Return the two supported training stages under one canonical name."""
    aliases: dict[Union[str, int], str] = {
        1: "selection",
        2: "final",
        "1": "selection",
        "stage1": "selection",
        "stage_1": "selection",
        "first": "selection",
        "selection": "selection",
        "select": "selection",
        "2": "final",
        "stage2": "final",
        "stage_2": "final",
        "second": "final",
        "final": "final",
        "refit": "final",
    }
    key: Union[str, int]
    if isinstance(training_stage, str):
        key = training_stage.lower().strip().replace(" ", "")
    elif isinstance(training_stage, int) and not isinstance(training_stage, bool):
        key = training_stage
    else:
        raise TypeError("training_stage must be a string or integer 1/2")
    if key not in aliases:
        raise ValueError(
            "training_stage must be 'selection' (stage 1) or 'final' (stage 2)"
        )
    return aliases[key]


def _validate_method_config(config: Mapping[str, Any]) -> None:
    method = config["method"]
    params = config["params"]
    if method == "xgboost":
        if params.get("objective") != "reg:squarederror":
            raise ValueError("XGBoost objective must be reg:squarederror")
        if params.get("eval_metric") != "rmse":
            raise ValueError("XGBoost eval_metric must be rmse")
    elif method == "lightgbm":
        objective = str(params.get("objective", ""))
        if objective not in {"regression", "regression_l2", "binary"}:
            raise ValueError(
                "LightGBM objective must be regression/regression_l2/binary"
            )
        if objective == "binary":
            # 二分类：metric 用 logloss / auc / error，选轮统一取 binary_logloss
            if params.get("metric") not in {"binary_logloss", "auc", "binary_error"}:
                raise ValueError(
                    "LightGBM binary metric must be binary_logloss/auc/binary_error"
                )
        else:
            if params.get("metric") != "rmse":
                raise ValueError("LightGBM metric must be rmse")
    elif method == "catboost":
        if str(params.get("loss_function", "")).upper() != "RMSE":
            raise ValueError("CatBoost loss_function must be RMSE")
        if str(params.get("eval_metric", "")).upper() != "RMSE":
            raise ValueError("CatBoost eval_metric must be RMSE")
        reserved = {"iterations", "early_stopping_rounds"}.intersection(params)
        if reserved:
            raise ValueError(
                f"CatBoost params must not contain {sorted(reserved)}; use "
                "training_iterations instead"
            )
    elif method in {"itransformer", "transformer"}:
        display_name = "iTransformer" if method == "itransformer" else "Transformer"
        positive_integer_keys = (
            "window_size", "d_model", "n_heads", "encoder_layers",
            "feedforward_dim", "batch_size", "thread_count",
        )
        for key in positive_integer_keys:
            if not isinstance(params.get(key), int) or params[key] <= 0:
                raise ValueError(f"{display_name} {key} must be a positive integer")
        if params["d_model"] % params["n_heads"] != 0:
            raise ValueError(f"{display_name} d_model must be divisible by n_heads")
        if not isinstance(params.get("num_workers"), int) or params["num_workers"] < 0:
            raise ValueError(f"{display_name} num_workers must be a non-negative integer")
        if params.get("missing_value_strategy") not in {"zero", "raise"}:
            raise ValueError(
                f"{display_name} missing_value_strategy must be 'zero' or 'raise'"
            )


def _normalise_config(
    method: str,
    config: Optional[Mapping[str, Any]],
    training_stage: str,
    training_iterations: int,
) -> dict[str, Any]:
    requested = _canonical_method(method)
    if config is None:
        try:
            base = make_training_config(requested)
        except KeyError as exc:
            raise ValueError(
                f"custom method {requested!r} requires an explicit config mapping"
            ) from exc
    else:
        base = deepcopy(dict(config))
    configured = _canonical_method(str(base.get("method", requested)))
    if configured != requested:
        raise ValueError(f"method={requested!r} conflicts with config['method']={configured!r}")
    base["method"] = requested

    # Version 2 makes both decisions explicit function inputs. Reject old
    # configuration keys instead of allowing two conflicting sources of truth.
    legacy_keys = {"max_iterations", "refit_full"}.intersection(base)
    if legacy_keys:
        raise ValueError(
            f"config contains removed keys {sorted(legacy_keys)}. Pass "
            "training_stage and training_iterations directly to "
            "train_factor_model instead."
        )
    base["training_stage"] = training_stage
    base["max_iterations"] = training_iterations

    if base.get("selection_metric") != "rmse" or base.get("selection_data") != "valid":
        raise ValueError("selection must minimise Validation RMSE")
    if not isinstance(base.get("random_seed"), int):
        raise TypeError("random_seed must be an integer")
    if not isinstance(base.get("params"), Mapping):
        raise TypeError("config['params'] must be a mapping")
    base["params"] = enforce_model_thread_config(requested, base["params"])

    plot = base.get("live_plot")
    if not isinstance(plot, Mapping) or not isinstance(plot.get("enabled"), bool):
        raise TypeError("live_plot must contain boolean enabled")
    if not isinstance(plot.get("update_every"), int) or plot["update_every"] <= 0:
        raise ValueError("live_plot.update_every must be a positive integer")
    validation_ic = base.get("validation_ic")
    if not isinstance(validation_ic, Mapping):
        raise TypeError("validation_ic must be a mapping")
    if str(validation_ic.get("method", "")).lower() != "pearson":
        raise ValueError("validation_ic.method currently supports only 'pearson'")
    _validate_method_config(base)
    return base


class _LiveLossPlot:
    """Audited Jupyter chart and numeric output for stage-1 metrics."""

    def __init__(self, enabled: bool, update_every: int, title: str):
        self.enabled = enabled
        self.update_every = update_every
        self.title = title
        self.fig = None
        self.axes = None
        self.handle = None
        self.last_reported_iteration = 0

    def should_update(self, iteration: int, final: bool = False) -> bool:
        return final or iteration % self.update_every == 0

    def update(
        self,
        train_values: Sequence[float],
        valid_values: Sequence[float],
        valid_ic_values: Optional[Sequence[float]] = None,
        valid_label_ic_values: Optional[Sequence[float]] = None,
        final: bool = False,
    ) -> None:
        if not self.enabled or not train_values or not valid_values:
            return
        iteration = len(valid_values)
        if not self.should_update(iteration, final=final):
            return
        import matplotlib.pyplot as plt

        if valid_ic_values is None:
            ic = np.full(iteration, np.nan, dtype=np.float64)
        else:
            ic = np.asarray(valid_ic_values, dtype=np.float64)
            if len(ic) != iteration:
                raise RuntimeError("live Validation IC and RMSE lengths differ")

        # 可选的第二个 IC：prediction vs 训练标签（target）
        if valid_label_ic_values is None:
            label_ic = None
        else:
            label_ic = np.asarray(valid_label_ic_values, dtype=np.float64)
            if len(label_ic) != iteration:
                raise RuntimeError("live Label IC and RMSE lengths differ")

        if iteration != self.last_reported_iteration:
            message = (
                f"[{self.title}] iteration={iteration} "
                f"fit_rmse={float(train_values[-1]):.10g} "
                f"valid_rmse={float(valid_values[-1]):.10g}"
            )
            if np.isfinite(ic[-1]):
                message += f" valid_pearson_ic={float(ic[-1]):.10g}"
            print(message)
            self.last_reported_iteration = iteration

        if self.fig is None:
            self.fig, self.axes = plt.subplots(1, 3, figsize=(20, 5.1))
            try:
                from IPython.display import display
                self.handle = display(self.fig, display_id=True)
            except Exception:
                self.handle = None

        train = np.asarray(train_values, dtype=np.float64)
        valid = np.asarray(valid_values, dtype=np.float64)
        rounds = np.arange(1, len(valid) + 1)
        best_idx = int(np.nanargmin(valid))
        best_iteration = best_idx + 1
        ax_train, ax_valid, ax_ic = self.axes

        ax_train.clear()
        ax_train.plot(rounds, train, color="tab:blue", label="Train RMSE")
        ax_train.axvline(best_iteration, color="red", linestyle="--", alpha=0.8)
        ax_train.set(title=f"Train loss — iteration {iteration}", xlabel="Iteration", ylabel="RMSE")
        ax_train.grid(alpha=0.25)
        ax_train.legend()

        ax_valid.clear()
        ax_valid.plot(rounds, valid, color="tab:orange", label="Validation RMSE")
        ax_valid.axvline(best_iteration, color="red", linestyle="--", alpha=0.8)
        ax_valid.scatter(
            [best_iteration], [valid[best_idx]], color="red", s=40,
            label=f"Min={valid[best_idx]:.6g} @ {best_iteration}",
        )
        ax_valid.set(title="Validation loss (selection metric)", xlabel="Iteration", ylabel="RMSE")
        ax_valid.grid(alpha=0.25)
        ax_valid.legend()

        ax_ic.clear()
        ax_ic.plot(rounds, ic, color="tab:green", label="IC vs return")
        if label_ic is not None:
            ax_ic.plot(rounds, label_ic, color="tab:blue", label="IC vs label")
        finite_ic = np.flatnonzero(np.isfinite(ic))
        if finite_ic.size:
            best_ic_idx = int(finite_ic[np.argmax(ic[finite_ic])])
            best_ic_iteration = best_ic_idx + 1
            ax_ic.axvline(best_ic_iteration, color="purple", linestyle="--", alpha=0.8)
            ax_ic.scatter(
                [best_ic_iteration], [ic[best_ic_idx]], color="purple", s=40,
                label=f"Max={ic[best_ic_idx]:.6g} @ {best_ic_iteration}",
            )
        ax_ic.set(title="Validation IC (diagnostic)", xlabel="Iteration", ylabel="Pearson IC")
        ax_ic.grid(alpha=0.25)
        ax_ic.legend()
        self.fig.suptitle(self.title)
        self.fig.tight_layout()
        self.fig.canvas.draw()
        if self.handle is not None:
            self.handle.update(self.fig)
        elif final:
            plt.show()


class _LiveFullTrainPlot:
    """One Jupyter panel for stage-2 Fit+Validation training RMSE."""

    def __init__(self, enabled: bool, update_every: int, title: str):
        self.enabled = enabled
        self.update_every = update_every
        self.title = title
        self.fig = None
        self.ax = None
        self.handle = None
        self.last_reported_iteration = 0

    def should_update(self, iteration: int, final: bool = False) -> bool:
        return final or iteration % self.update_every == 0

    def update(self, values: Sequence[float], final: bool = False) -> None:
        if not self.enabled or not values:
            return
        iteration = len(values)
        if not self.should_update(iteration, final=final):
            return
        import matplotlib.pyplot as plt

        if iteration != self.last_reported_iteration:
            print(
                f"[{self.title}] iteration={iteration} "
                f"full_train_rmse={float(values[-1]):.10g}"
            )
            self.last_reported_iteration = iteration

        if self.fig is None:
            self.fig, self.ax = plt.subplots(1, 1, figsize=(8.2, 5.1))
            try:
                from IPython.display import display
                self.handle = display(self.fig, display_id=True)
            except Exception:
                self.handle = None

        rmse = np.asarray(values, dtype=np.float64)
        rounds = np.arange(1, len(rmse) + 1)
        self.ax.clear()
        self.ax.plot(rounds, rmse, color="tab:blue", label="Full-train RMSE")
        self.ax.set(
            title=f"Fit + Validation loss — iteration {iteration}",
            xlabel="Iteration",
            ylabel="RMSE",
        )
        self.ax.grid(alpha=0.25)
        self.ax.legend()
        self.fig.suptitle(self.title)
        self.fig.tight_layout()
        self.fig.canvas.draw()
        if self.handle is not None:
            self.handle.update(self.fig)
        elif final:
            plt.show()

class _BaseAdapter:
    def __init__(self, config: Mapping[str, Any], feature_names: Sequence[str]):
        self.config = deepcopy(dict(config))
        self.feature_names = list(feature_names)

    def fit_selection(self, fit: _SplitData, valid: _SplitData, plotter: _LiveLossPlot) -> _FitOutcome:
        raise NotImplementedError

    def fit_frozen(
        self,
        parts: Sequence[_SplitData],
        fixed_iterations: int,
        plotter: _LiveFullTrainPlot,
    ) -> _FrozenOutcome:
        raise NotImplementedError


_TRAINING_METHODS: MutableMapping[str, Callable[[Mapping[str, Any], Sequence[str]], _BaseAdapter]] = {}


def register_training_method(name: str) -> Callable[[Callable[..., _BaseAdapter]], Callable[..., _BaseAdapter]]:
    """Register a future model adapter factory under a new method name."""
    normalised = name.lower().strip()
    if not normalised:
        raise ValueError("training method name cannot be empty")

    def decorator(factory: Callable[..., _BaseAdapter]) -> Callable[..., _BaseAdapter]:
        if normalised in _TRAINING_METHODS:
            raise KeyError(f"training method already registered: {normalised}")
        _TRAINING_METHODS[normalised] = factory
        return factory

    return decorator


def available_training_methods() -> tuple[str, ...]:
    return tuple(sorted(_TRAINING_METHODS))


def _pearson_ic(prediction: Any, target: Any) -> float:
    """成对 finite 的 Pearson IC。

    单线程计算，避免每轮评估的 BLAS 点积线程池与训练本身的 OpenMP 线程池
    叠加，导致阶段 1 CPU 占用远超 CPU_THREADS。
    """
    from threadpoolctl import threadpool_limits

    with threadpool_limits(limits=1):
        return float(pd.Series(prediction).corr(pd.Series(target)))


def _best_ic(values: Sequence[float]) -> tuple[Optional[int], float]:
    array = np.asarray(values, dtype=np.float64)
    finite_positions = np.flatnonzero(np.isfinite(array))
    if finite_positions.size == 0:
        # IC 不可用时不再阻断训练；RMSE 选轮照常进行。
        return None, float("nan")
    best_position = int(finite_positions[np.argmax(array[finite_positions])])
    return best_position + 1, float(array[best_position])


def _history_frame(
    train_values: Sequence[float],
    valid_values: Sequence[float],
    valid_ic_values: Sequence[float],
    valid_label_ic_values: Optional[Sequence[float]] = None,
) -> pd.DataFrame:
    if len(train_values) != len(valid_values) or not train_values:
        raise RuntimeError("Train/Validation RMSE histories are empty or have different lengths")
    if len(valid_ic_values) != len(valid_values):
        raise RuntimeError(
            "Validation IC history length differs from the RMSE history length"
        )
    train = np.asarray(train_values, dtype=np.float64)
    valid = np.asarray(valid_values, dtype=np.float64)
    valid_ic = np.asarray(valid_ic_values, dtype=np.float64)
    if not np.isfinite(train).all() or not np.isfinite(valid).all():
        raise RuntimeError("Train/Validation RMSE history contains non-finite values")
    if not np.isfinite(valid_ic).any():
        valid_ic = np.full(len(valid), np.nan)
    frame = pd.DataFrame(
        {
            "iteration": np.arange(1, len(train) + 1, dtype=np.int64),
            "train_rmse": train,
            "valid_rmse": valid,
            "valid_pearson_ic": valid_ic,
        }
    )
    # 可选的第二个 IC：prediction vs 训练标签（target）；仅诊断、不参与选轮。
    if valid_label_ic_values is not None:
        label_ic = np.asarray(valid_label_ic_values, dtype=np.float64)
        if len(label_ic) != len(valid):
            raise RuntimeError("Label IC history length differs from the RMSE history length")
        frame["valid_label_ic"] = label_ic
    return frame


def _frozen_history_frame(values: Sequence[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "iteration": np.arange(1, len(values) + 1, dtype=np.int64),
            "full_train_rmse": values,
        }
    )


def _select_best_from_history(
    history: pd.DataFrame,
) -> tuple[int, int, float, float]:
    best_idx = int(history["valid_rmse"].to_numpy().argmin())
    best_ic_iteration, best_ic = _best_ic(history["valid_pearson_ic"])
    best_valid_loss = float(history.iloc[best_idx]["valid_rmse"])
    return best_idx, best_ic_iteration, best_ic, best_valid_loss


# 诊断用 Pearson IC 的计算节流间隔：每 N 轮才真正算一次 IC，其余轮复用缓存值，
# 避免每轮触发 BLAS 多线程与训练本身的 OpenMP 线程叠加（阶段1满核的根因）。
_IC_INTERVAL = 30


class _PearsonICMetric:
    """CatBoost custom eval_metric computing signed pooled Pearson IC."""

    def __init__(self, ic_label: Optional[np.ndarray] = None) -> None:
        # CatBoost 每轮对 learn 先调、validation 后调各一次 evaluate；
        # 偶数次调用 = validation，按 _IC_INTERVAL 节流真正计算 IC。
        # ic_label：可选的独立 IC 评估标签；None 时用训练 label（target）。
        self._ic_label = ic_label
        self._tick = 0
        self._cached = float("nan")

    def is_max_optimal(self) -> bool:
        return True

    def evaluate(self, approxes, target, weight):
        self._tick += 1
        if self._tick % 2 == 0:  # validation 集
            label = self._ic_label if self._ic_label is not None else target
            if self._tick == 2 or self._tick % (2 * _IC_INTERVAL) == 0:
                self._cached = _pearson_ic(approxes[0], label)
            return self._cached, 1.0
        return float("nan"), 1.0  # learn 集不算 IC

    def get_final_error(self, error, weight):
        return error


class _LabelICMetric(_PearsonICMetric):
    """CatBoost custom eval_metric：prediction vs 训练标签(target) 的 Pearson IC。

    独立成子类是为了让 CatBoost 的 metric 名（类名）不同，从而在
    get_evals_result() 里与 ``_PearsonICMetric``（vs 原始收益）区分开。
    """

    def __init__(self) -> None:
        # ic_label=None 时，_PearsonICMetric.evaluate 回退到用训练 label（target）。
        super().__init__(ic_label=None)


def _catboost_callback_allowed(model: Any, params: Mapping[str, Any]) -> bool:
    import inspect

    return (
        "callbacks" in inspect.signature(model.fit).parameters
        and str(params.get("task_type", "CPU")).upper() == "CPU"
    )


@register_training_method("xgboost")
class _XGBoostAdapter(_BaseAdapter):
    def fit_selection(self, fit: _SplitData, valid: _SplitData, plotter: _LiveLossPlot) -> _FitOutcome:
        """阶段1：仅用Fit训练、Valid评估，返回Valid RMSE最低轮数的best模型。"""
        xgb = _require_dependency("xgboost", "xgboost")
        import inspect

        # 1. 过滤标签非有限的行，构建训练/验证 DMatrix。
        X_fit, y_fit, _ic_fit, dropped_fit = _finite_label_rows(fit)
        X_valid, y_valid, ic_valid, dropped_valid = _finite_label_rows(valid)
        dfit = xgb.DMatrix(
            X_fit,
            label=y_fit,
            feature_names=self.feature_names,
            missing=np.nan,
            nthread=CPU_THREADS,
        )
        dvalid = xgb.DMatrix(
            X_valid,
            label=y_valid,
            feature_names=self.feature_names,
            missing=np.nan,
            nthread=CPU_THREADS,
        )
        history: dict[str, Any] = {}

        # 2. 自定义 metric：每 _IC_INTERVAL 轮算两个 IC（诊断用）——
        #    pearson_ic = prediction vs 原始收益(ic_y)；label_ic = prediction vs 训练标签(target)。
        _ic_state: dict[str, Any] = {"tick": 0, "value_return": float("nan"), "value_label": float("nan")}

        def _maybe_recompute_ic(prediction):
            _ic_state["tick"] += 1
            if _ic_state["tick"] == 1 or _ic_state["tick"] % _IC_INTERVAL == 0:
                _ic_state["value_return"] = _pearson_ic(prediction, ic_valid)
                _ic_state["value_label"] = _pearson_ic(prediction, y_valid)

        def pearson_ic_metric(prediction, matrix):
            if matrix is dvalid:  # 只对验证集计数
                _maybe_recompute_ic(prediction)
            return "pearson_ic", _ic_state["value_return"]

        def label_ic_metric(prediction, matrix):
            return "label_ic", _ic_state["value_label"]

        # 3. 训练回调：把每轮 Train/Valid RMSE 与 IC 喂给实时绘图。
        class LiveCallback(xgb.callback.TrainingCallback):
            def after_iteration(callback_self, model, epoch, evals_log):
                train = evals_log["train"]["rmse"]
                validation = evals_log["valid"]["rmse"]
                valid_ic = evals_log["valid"].get("pearson_ic")
                valid_label_ic = evals_log["valid"].get("label_ic")
                plotter.update(train, validation, valid_ic, valid_label_ic_values=valid_label_ic)
                return False

        callbacks: list[Any] = [LiveCallback()]
        train_kwargs: dict[str, Any] = {
            "params": dict(self.config["params"]),
            "dtrain": dfit,
            "num_boost_round": self.config["max_iterations"],
            "evals": [(dfit, "train"), (dvalid, "valid")],
            "evals_result": history,
            "callbacks": callbacks,
            "verbose_eval": False,
        }
        # 4. 兼容新旧版本 XGBoost 的自定义 metric 参数名。
        metric_argument = (
            "custom_metric"
            if "custom_metric" in inspect.signature(xgb.train).parameters
            else "feval"
        )
        if metric_argument == "custom_metric":
            # xgboost >= 2.0：custom_metric 必须是单个可调用对象，返回
            # [(name, value), ...]；旧版"list[callable] + 返回元组"的接口已移除。
            def combined_metric(prediction, matrix):
                return [pearson_ic_metric(prediction, matrix),
                        label_ic_metric(prediction, matrix)]

            train_kwargs["custom_metric"] = combined_metric
        else:
            train_kwargs["feval"] = [pearson_ic_metric, label_ic_metric]

        # 5. 跑满固定轮数训练（无早停）。
        model = xgb.train(
            **train_kwargs,
        )

        # 6. 汇总历史，截取 Valid RMSE 最低轮数的 best 模型。
        frame = _history_frame(
            history["train"]["rmse"],
            history["valid"]["rmse"],
            history["valid"]["pearson_ic"],
            valid_label_ic_values=history["valid"]["label_ic"],
        )
        best_idx, best_ic_iteration, best_ic, best_valid_loss = _select_best_from_history(frame)
        # 返回的模型必须就是所选轮数（best模型）。
        model = model[: best_idx + 1]
        plotter.update(
            frame["train_rmse"].tolist(),
            frame["valid_rmse"].tolist(),
            frame["valid_pearson_ic"].tolist(),
            valid_label_ic_values=frame["valid_label_ic"].tolist(),
            final=True,
        )
        return _FitOutcome(
            model=model,
            history=frame,
            best_iteration=best_idx + 1,
            best_valid_loss=best_valid_loss,
            best_valid_ic_iteration=best_ic_iteration,
            best_valid_ic=best_ic,
            metadata={"dropped_nonfinite_fit_labels": dropped_fit, "dropped_nonfinite_valid_labels": dropped_valid},
        )

    def fit_frozen(
        self,
        parts: Sequence[_SplitData],
        fixed_iterations: int,
        plotter: _LiveFullTrainPlot,
    ) -> _FrozenOutcome:
        """阶段2：合并Fit+Valid，固定轮数训练最终模型，不做选择。"""
        xgb = _require_dependency("xgboost", "xgboost")
        # 合并全部训练行。
        X, y, _ic, dropped = _combine_finite_parts(parts)
        matrix = xgb.DMatrix(
            X,
            label=y,
            feature_names=self.feature_names,
            missing=np.nan,
            nthread=CPU_THREADS,
        )
        history: dict[str, Any] = {}

        class LiveCallback(xgb.callback.TrainingCallback):
            def after_iteration(callback_self, model, epoch, evals_log):
                values = evals_log["full_train"]["rmse"]
                plotter.update(values)
                return False

        # 固定轮数训练。
        model = xgb.train(
            params=dict(self.config["params"]), dtrain=matrix,
            num_boost_round=fixed_iterations, evals=[(matrix, "full_train")],
            evals_result=history, callbacks=[LiveCallback()],
            verbose_eval=False,
        )
        train_rmse = history.get("full_train", {}).get("rmse", [])
        frame = _frozen_history_frame(train_rmse)
        plotter.update(frame["full_train_rmse"].tolist(), final=True)
        return _FrozenOutcome(
            model=model,
            history=frame,
            metadata={
                "full_train_rows": int(len(y)),
                "dropped_nonfinite_labels": dropped,
            },
        )


def _lgb_loss_key(config: Mapping[str, Any]) -> str:
    """LightGBM 的选轮/绘图 metric key：binary → binary_logloss，否则 rmse。"""
    objective = str(config.get("params", {}).get("objective", "")).lower()
    return "binary_logloss" if objective == "binary" else "rmse"


@register_training_method("lightgbm")
class _LightGBMAdapter(_BaseAdapter):
    def fit_selection(self, fit: _SplitData, valid: _SplitData, plotter: _LiveLossPlot) -> _FitOutcome:
        """阶段1：仅用Fit训练、Valid评估，返回Valid loss最低树数的best模型。"""
        lgb = _require_dependency("lightgbm", "lightgbm")
        loss_key = _lgb_loss_key(self.config)
        # 1. 过滤标签非有限的行，构建训练/验证 Dataset。
        X_fit, y_fit, _ic_fit, dropped_fit = _finite_label_rows(fit)
        X_valid, y_valid, ic_valid, dropped_valid = _finite_label_rows(valid)
        dataset_params = {"num_threads": CPU_THREADS}
        dfit = lgb.Dataset(
            X_fit,
            label=y_fit,
            feature_name=self.feature_names,
            params=dataset_params,
            free_raw_data=True,
        )
        dvalid = lgb.Dataset(
            X_valid,
            label=y_valid,
            reference=dfit,
            feature_name=self.feature_names,
            params=dataset_params,
            free_raw_data=True,
        )
        history: dict[str, Any] = {}

        # 2. 自定义 feval：每 _IC_INTERVAL 轮算两个 IC（诊断用）——
        #    pearson_ic = prediction vs 原始收益(ic_y)；label_ic = prediction vs 训练标签(target)。
        #    只对 valid 计数，train 集复用缓存，避免每轮触发 BLAS 多线程与训练线程叠加。
        _ic_state: dict[str, Any] = {"tick": 0, "value_return": float("nan"), "value_label": float("nan")}

        def _maybe_recompute_ic(prediction):
            _ic_state["tick"] += 1
            if _ic_state["tick"] == 1 or _ic_state["tick"] % _IC_INTERVAL == 0:
                _ic_state["value_return"] = _pearson_ic(prediction, ic_valid)
                _ic_state["value_label"] = _pearson_ic(prediction, y_valid)

        def pearson_ic_metric(prediction, dataset):
            if dataset is dvalid:  # 只对验证集计数
                _maybe_recompute_ic(prediction)
            return "pearson_ic", _ic_state["value_return"], True

        def label_ic_metric(prediction, dataset):
            return "label_ic", _ic_state["value_label"], True

        # 3. 训练回调：收集每轮 Train/Valid RMSE 与 IC，喂给实时绘图。
        def live_callback(env):
            if not env.evaluation_result_list:
                return
            values = {(str(item[0]), str(item[1]).lower()): float(item[2]) for item in env.evaluation_result_list}
            train_value = values.get(("train", loss_key))
            valid_value = values.get(("valid", loss_key))
            valid_ic_value = values.get(("valid", "pearson_ic"))
            if train_value is not None and valid_value is not None:
                live_callback.train.append(train_value)
                live_callback.valid.append(valid_value)
                live_callback.valid_ic.append(
                    float("nan") if valid_ic_value is None else valid_ic_value
                )
                plotter.update(
                    live_callback.train,
                    live_callback.valid,
                    live_callback.valid_ic,
                )

        live_callback.train = []
        live_callback.valid = []
        live_callback.valid_ic = []
        live_callback.order = 20
        callbacks = [lgb.record_evaluation(history), live_callback, lgb.log_evaluation(0)]
        # 4. 跑满固定轮数训练（无早停）。
        model = lgb.train(
            params=dict(self.config["params"]), train_set=dfit,
            num_boost_round=self.config["max_iterations"],
            valid_sets=[dfit, dvalid], valid_names=["train", "valid"],
            feval=[pearson_ic_metric, label_ic_metric],
            callbacks=callbacks,
        )
        frame = _history_frame(
            history["train"][loss_key],
            history["valid"][loss_key],
            history["valid"]["pearson_ic"],
            valid_label_ic_values=history["valid"]["label_ic"],
        )
        # 5. 截取 Valid loss 最低树数的 best 模型。
        best_idx, best_ic_iteration, best_ic, best_valid_loss = _select_best_from_history(frame)
        model = lgb.Booster(model_str=model.model_to_string(num_iteration=best_idx + 1))
        plotter.update(
            frame["train_rmse"].tolist(),
            frame["valid_rmse"].tolist(),
            frame["valid_pearson_ic"].tolist(),
            valid_label_ic_values=frame["valid_label_ic"].tolist(),
            final=True,
        )
        return _FitOutcome(
            model=model, history=frame, best_iteration=best_idx + 1,
            best_valid_loss=best_valid_loss,
            best_valid_ic_iteration=best_ic_iteration,
            best_valid_ic=best_ic,
            metadata={"dropped_nonfinite_fit_labels": dropped_fit, "dropped_nonfinite_valid_labels": dropped_valid},
        )

    def fit_frozen(
        self,
        parts: Sequence[_SplitData],
        fixed_iterations: int,
        plotter: _LiveFullTrainPlot,
    ) -> _FrozenOutcome:
        """阶段2：合并Fit+Valid，固定轮数训练最终模型，不做选择。"""
        lgb = _require_dependency("lightgbm", "lightgbm")
        loss_key = _lgb_loss_key(self.config)
        # 合并全部训练行。
        X, y, _ic, dropped = _combine_finite_parts(parts)
        dataset = lgb.Dataset(
            X,
            label=y,
            feature_name=self.feature_names,
            params={"num_threads": CPU_THREADS},
            free_raw_data=True,
        )
        history: dict[str, Any] = {}

        def live_callback(env):
            for item in env.evaluation_result_list or ():
                if str(item[0]) == "full_train" and str(item[1]).lower() == loss_key:
                    live_callback.values.append(float(item[2]))
                    plotter.update(live_callback.values)
                    break

        live_callback.values = []
        live_callback.order = 20
        # 固定轮数训练。
        model = lgb.train(
            params=dict(self.config["params"]), train_set=dataset,
            num_boost_round=fixed_iterations, valid_sets=[dataset], valid_names=["full_train"],
            callbacks=[
                lgb.record_evaluation(history),
                live_callback,
                lgb.log_evaluation(0),
            ],
        )
        values = history.get("full_train", {}).get(loss_key, [])
        frame = _frozen_history_frame(values)
        plotter.update(frame["full_train_rmse"].tolist(), final=True)
        return _FrozenOutcome(
            model=model,
            history=frame,
            metadata={
                "full_train_rows": int(len(y)),
                "dropped_nonfinite_labels": dropped,
            },
        )


@register_training_method("catboost")
class _CatBoostAdapter(_BaseAdapter):
    def fit_selection(self, fit: _SplitData, valid: _SplitData, plotter: _LiveLossPlot) -> _FitOutcome:
        """阶段1：仅用Fit训练、Valid评估，返回Valid RMSE最低轮数的best模型。"""
        cat = _require_dependency("catboost", "catboost")

        # 1. 过滤标签非有限的行。
        X_fit, y_fit, _ic_fit, dropped_fit = _finite_label_rows(fit)
        X_valid, y_valid, ic_valid, dropped_valid = _finite_label_rows(valid)
        # 2. eval_metric 同时挂 RMSE 和两个 Pearson IC：
        #    _PearsonICMetric = vs 原始收益(ic_y)；_LabelICMetric = vs 训练标签(target)。
        params = dict(self.config["params"])
        params["iterations"] = self.config["max_iterations"]
        # CatBoost 1.2.10 不接受 eval_metric 为 list（即使全是字符串也会在
        # _try_jit_methods 处崩溃）。这里只挂字符串 RMSE；Valid IC 曲线留空，
        # 由 Notebook 用 OOF 预测自行计算，语义更真。
        params["eval_metric"] = "RMSE"
        model = cat.CatBoostRegressor(**params)

        # 3. 训练回调：收集每轮 Train/Valid RMSE，喂给实时绘图。
        class LiveCallback:
            def __init__(callback_self):
                callback_self.train = []
                callback_self.valid = []

            @staticmethod
            def _last(metrics, dataset):
                dataset_metrics = metrics.get(dataset, {})
                for key in ("RMSE", "rmse"):
                    if key in dataset_metrics and dataset_metrics[key]:
                        return float(dataset_metrics[key][-1])
                return None

            def after_iteration(callback_self, info):
                train_value = callback_self._last(info.metrics, "learn")
                valid_value = callback_self._last(info.metrics, "validation")
                if train_value is not None and valid_value is not None:
                    callback_self.train.append(train_value)
                    callback_self.valid.append(valid_value)
                    plotter.update(callback_self.train, callback_self.valid)
                return True

        callback = LiveCallback()
        fit_kwargs: dict[str, Any] = {
            "X": X_fit, "y": y_fit, "eval_set": (X_valid, y_valid),
            "use_best_model": False,
        }
        # 4. 不同 CatBoost 版本的 fit 签名不一致：优先用 callbacks 参数（仅CPU），
        #    否则退回 CatBoost 原生绘图，但最终仍渲染我们的审计图。
        callback_allowed = _catboost_callback_allowed(model, params)
        if callback_allowed:
            fit_kwargs["callbacks"] = [callback]
            fit_kwargs["verbose"] = False
        else:
            fit_kwargs["verbose"] = self.config["live_plot"]["update_every"]
            fit_kwargs["plot"] = (
                self.config["live_plot"]["enabled"]
                and str(params.get("task_type", "CPU")).upper() == "CPU"
            )

        # 5. 跑满固定轮数训练（无早停），从评估结果读取每轮 RMSE 与 IC。
        model.fit(**fit_kwargs)
        evaluations = model.get_evals_result()
        learn_metrics = evaluations["learn"]
        train_values = learn_metrics["RMSE"] if "RMSE" in learn_metrics else next(iter(learn_metrics.values()))
        valid_key = "validation" if "validation" in evaluations else next(k for k in evaluations if k != "learn")
        valid_metrics = evaluations[valid_key]
        valid_values = valid_metrics["RMSE"] if "RMSE" in valid_metrics else next(iter(valid_metrics.values()))
        valid_ic_values = valid_metrics.get(
            "_PearsonICMetric", [float("nan")] * len(valid_values)
        )
        valid_label_ic_values = valid_metrics.get(
            "_LabelICMetric", [float("nan")] * len(valid_values)
        )

        # 6. 截取 Valid RMSE 最低轮数的 best 模型。
        frame = _history_frame(
            train_values, valid_values, valid_ic_values,
            valid_label_ic_values=valid_label_ic_values,
        )
        best_idx, best_ic_iteration, best_ic, best_valid_loss = _select_best_from_history(frame)
        model.shrink(best_idx + 1)
        plotter.update(
            frame["train_rmse"].tolist(),
            frame["valid_rmse"].tolist(),
            frame["valid_pearson_ic"].tolist(),
            valid_label_ic_values=frame["valid_label_ic"].tolist(),
            final=True,
        )
        return _FitOutcome(
            model=model, history=frame, best_iteration=best_idx + 1,
            best_valid_loss=best_valid_loss,
            best_valid_ic_iteration=best_ic_iteration,
            best_valid_ic=best_ic,
            metadata={
                "dropped_nonfinite_fit_labels": dropped_fit,
                "dropped_nonfinite_valid_labels": dropped_valid,
                "live_plot_backend": "callback" if callback_allowed else "catboost_native",
            },
        )

    def fit_frozen(
        self,
        parts: Sequence[_SplitData],
        fixed_iterations: int,
        plotter: _LiveFullTrainPlot,
    ) -> _FrozenOutcome:
        """阶段2：合并Fit+Valid，固定轮数训练最终模型，不做选择。"""
        cat = _require_dependency("catboost", "catboost")

        # 合并全部训练行。
        X, y, _ic, dropped = _combine_finite_parts(parts)
        params = dict(self.config["params"])
        params["iterations"] = fixed_iterations
        params["verbose"] = False
        model = cat.CatBoostRegressor(**params)

        # 训练回调：收集每轮全量训练 RMSE。
        class LiveCallback:
            def __init__(callback_self):
                callback_self.values = []

            def after_iteration(callback_self, info):
                learn = info.metrics.get("learn", {})
                for key in ("RMSE", "rmse"):
                    if key in learn and learn[key]:
                        callback_self.values.append(float(learn[key][-1]))
                        plotter.update(callback_self.values)
                        break
                return True

        callback = LiveCallback()
        callback_allowed = _catboost_callback_allowed(model, params)
        fit_kwargs: dict[str, Any] = {
            "X": X,
            "y": y,
            "verbose": (
                False
                if callback_allowed
                else self.config["live_plot"]["update_every"]
            ),
        }
        if callback_allowed:
            fit_kwargs["callbacks"] = [callback]
        else:
            fit_kwargs["plot"] = (
                self.config["live_plot"]["enabled"]
                and str(params.get("task_type", "CPU")).upper() == "CPU"
            )
        model.fit(**fit_kwargs)
        evaluations = model.get_evals_result()
        learn_metrics = evaluations.get("learn", {})
        rmse_values = None
        for key in ("RMSE", "rmse"):
            if key in learn_metrics:
                rmse_values = learn_metrics[key]
                break
        # 兜底：极端情况下拿不到逐轮 RMSE 时，只记录最终一轮。
        if rmse_values is None:
            prediction = np.asarray(
                model.predict(X, thread_count=CPU_THREADS),
                dtype=np.float64,
            )
            rmse_values = [float(np.sqrt(np.mean(np.square(prediction - y))))]
            iterations = [fixed_iterations]
        else:
            iterations = np.arange(1, len(rmse_values) + 1, dtype=np.int64)
        frame = pd.DataFrame(
            {"iteration": iterations, "full_train_rmse": rmse_values}
        )
        plotter.update(frame["full_train_rmse"].tolist(), final=True)
        return _FrozenOutcome(
            model=model,
            history=frame,
            metadata={
                "full_train_rows": int(len(y)),
                "dropped_nonfinite_labels": dropped,
                "live_plot_backend": (
                    "callback" if callback_allowed else "catboost_native"
                ),
            },
        )


def _torch_device(torch: Any, requested: str) -> Any:
    requested = requested.lower().strip()
    if requested == "auto":
        if torch.cuda.is_available():
            return torch.device("cuda")
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")
    if requested not in {"cpu", "cuda", "mps"}:
        raise ValueError("PyTorch device must be auto, cpu, cuda, or mps")
    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable")
    mps = getattr(torch.backends, "mps", None)
    if requested == "mps" and (mps is None or not mps.is_available()):
        raise RuntimeError("MPS was requested but is unavailable")
    return torch.device(requested)


def _configure_torch_threads(
    torch: Any,
    config: Mapping[str, Any],
) -> dict[str, int]:
    """Apply and verify both PyTorch native CPU thread pools."""
    configured = int(config["params"]["thread_count"])
    if configured != CPU_THREADS:
        raise ValueError(
            f"PyTorch thread_count={configured} conflicts with CPU_THREADS={CPU_THREADS}"
        )
    return configure_torch_threads(torch)


def _set_random_seeds(seed: int, torch: Any = None) -> None:
    random.seed(seed)
    np.random.seed(seed)
    if torch is not None:
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)


def _session_code(datetimes: pd.DatetimeIndex) -> np.ndarray:
    minutes = datetimes.hour * 60 + datetimes.minute
    return np.where(minutes < 12 * 60, "AM", "PM")


def _build_window_dataset(split: _SplitData, config: Mapping[str, Any], torch: Any):
    params = config["params"]
    method = str(config["method"])
    window = int(params["window_size"])
    if window <= 0:
        raise ValueError(f"{method} window_size must be positive")

    frame = split.index.to_frame(index=False)
    frame["_position"] = np.arange(len(frame), dtype=np.int64)
    sort_columns = ["Contract", "Datetime", "_position"]
    order = frame.sort_values(sort_columns, kind="mergesort")["_position"].to_numpy(np.int64)

    sorted_index = split.index[order]
    sorted_frame = sorted_index.to_frame(index=False)
    sorted_datetimes = pd.DatetimeIndex(sorted_frame["Datetime"])
    duplicate_time = pd.MultiIndex.from_arrays(
        [sorted_frame["Contract"], sorted_frame["Datetime"]]
    ).duplicated().any()
    if duplicate_time:
        raise ValueError(
            f"{split.name} contains duplicate Contract + Datetime rows; sequence order is ambiguous"
        )

    group_arrays: list[np.ndarray] = [sorted_frame["Contract"].to_numpy()]
    if not params["allow_cross_day"]:
        group_arrays.append(sorted_frame["Date"].to_numpy())
    if not params["allow_cross_session"]:
        group_arrays.append(_session_code(sorted_datetimes))
    groups = pd.MultiIndex.from_arrays(group_arrays)
    codes, _ = pd.factorize(groups, sort=False)

    endpoints: list[np.ndarray] = []
    if len(codes):
        boundaries = np.flatnonzero(np.r_[True, codes[1:] != codes[:-1], True])
        for left, right in zip(boundaries[:-1], boundaries[1:]):
            if right - left >= window:
                endpoints.append(np.arange(left + window - 1, right, dtype=np.int64))
    end_positions = np.concatenate(endpoints) if endpoints else np.empty(0, dtype=np.int64)
    sorted_y = split.y[order]
    if len(end_positions):
        end_positions = end_positions[np.isfinite(sorted_y[end_positions])]
    if len(end_positions) == 0:
        raise ValueError(
            f"{split.name} creates zero valid {method} windows; inspect "
            "window_size, day/session boundaries and labels"
        )

    # Integer advanced indexing already returns an independent array.
    X = split.X[order]
    missing_count = int(np.isnan(X).sum())
    strategy = str(params["missing_value_strategy"]).lower()
    if strategy == "raise" and missing_count:
        raise ValueError(
            f"{split.name} contains {missing_count} missing factor values; set "
            "missing_value_strategy='zero' explicitly or clean the data"
        )
    if strategy == "zero":
        np.nan_to_num(X, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
    elif strategy != "raise":
        raise ValueError(
            f"{method} missing_value_strategy must be 'zero' or 'raise'"
        )

    base_X = torch.from_numpy(X.astype(np.float32, copy=False))
    base_y = torch.from_numpy(sorted_y.astype(np.float32, copy=False))
    ends = torch.from_numpy(end_positions)

    class WindowDataset(torch.utils.data.Dataset):
        def __len__(self):
            return int(len(ends))

        def __getitem__(self, item):
            end = int(ends[item])
            return base_X[end - window + 1 : end + 1], base_y[end]

    return WindowDataset()


def _make_itransformer_model(torch: Any, n_features: int, params: Mapping[str, Any]):
    nn = torch.nn
    window = int(params["window_size"])
    d_model = int(params["d_model"])
    n_heads = int(params["n_heads"])
    if d_model % n_heads != 0:
        raise ValueError("iTransformer d_model must be divisible by n_heads")

    class ITransformerRegressor(nn.Module):
        def __init__(self):
            super().__init__()
            self.time_projection = nn.Linear(window, d_model)
            self.variable_embedding = nn.Parameter(torch.zeros(1, n_features, d_model))
            nn.init.normal_(self.variable_embedding, mean=0.0, std=0.02)
            layer = nn.TransformerEncoderLayer(
                d_model=d_model,
                nhead=n_heads,
                dim_feedforward=int(params["feedforward_dim"]),
                dropout=float(params["dropout"]),
                activation="gelu",
                batch_first=True,
                norm_first=True,
            )
            self.encoder = nn.TransformerEncoder(layer, num_layers=int(params["encoder_layers"]))
            self.head = nn.Sequential(nn.LayerNorm(d_model), nn.Linear(d_model, 1))

        def forward(self, values):
            # [batch, window, factors] -> [batch, factors, window]
            tokens = values.transpose(1, 2)
            tokens = self.time_projection(tokens) + self.variable_embedding
            encoded = self.encoder(tokens)
            return self.head(encoded.mean(dim=1)).squeeze(-1)

    return ITransformerRegressor()


def _make_transformer_model(torch: Any, n_features: int, params: Mapping[str, Any]):
    """Build a standard Transformer whose tokens are sampled time points."""
    nn = torch.nn
    window = int(params["window_size"])
    d_model = int(params["d_model"])
    n_heads = int(params["n_heads"])
    if d_model % n_heads != 0:
        raise ValueError("Transformer d_model must be divisible by n_heads")

    class TransformerRegressor(nn.Module):
        def __init__(self):
            super().__init__()
            self.factor_projection = nn.Linear(n_features, d_model)
            self.position_embedding = nn.Parameter(
                torch.zeros(1, window, d_model)
            )
            nn.init.normal_(self.position_embedding, mean=0.0, std=0.02)
            layer = nn.TransformerEncoderLayer(
                d_model=d_model,
                nhead=n_heads,
                dim_feedforward=int(params["feedforward_dim"]),
                dropout=float(params["dropout"]),
                activation="gelu",
                batch_first=True,
                norm_first=True,
            )
            self.encoder = nn.TransformerEncoder(
                layer,
                num_layers=int(params["encoder_layers"]),
            )
            self.head = nn.Sequential(
                nn.LayerNorm(d_model),
                nn.Linear(d_model, 1),
            )

        def forward(self, values):
            # [batch, window, factors] -> [batch, window, d_model]
            tokens = self.factor_projection(values)
            tokens = tokens + self.position_embedding[:, : values.shape[1], :]
            encoded = self.encoder(tokens)
            # The final token is row t and predicts the already-aligned y[t].
            return self.head(encoded[:, -1, :]).squeeze(-1)

    return TransformerRegressor()


def _make_torch_sequence_model(
    torch: Any,
    method: str,
    n_features: int,
    params: Mapping[str, Any],
):
    if method == "itransformer":
        return _make_itransformer_model(torch, n_features, params)
    if method == "transformer":
        return _make_transformer_model(torch, n_features, params)
    raise KeyError(f"unsupported PyTorch sequence method: {method!r}")


def _log_torch_model_summary(
    model: Any,
    method: str,
    stage: str,
    n_features: int = 0,
    window: int = 0,
) -> None:
    """Print the structure tree and the parameter count once, right after the model is built.

    Uses only built-in PyTorch APIs: ``nn.Module.__repr__`` for the structure and
    ``sum(p.numel() for p in model.parameters())`` for the count. PyTorch has no
    ``model.summary()`` equivalent, so the summary is assembled here.
    """
    total = sum(int(tensor.numel()) for tensor in model.parameters())
    trainable = sum(
        int(tensor.numel()) for tensor in model.parameters() if tensor.requires_grad
    )
    groups: dict[str, int] = {}
    for name, tensor in model.named_parameters():
        top = name.split(".")[0]
        groups[top] = groups.get(top, 0) + int(tensor.numel())

    tag = f"{method}/{stage}"
    if n_features and window:
        print(f"[{tag}] input = (batch, window={window}, n_features={n_features})")
    print(f"[{tag}] model = {type(model).__name__}")
    print(model)
    print(
        f"[{tag}] total params = {total:,}  trainable = {trainable:,}  "
        f"frozen = {total - trainable:,}  ({total * 4 / 1024 ** 2:.3f} MB float32)"
    )
    for name, count in sorted(groups.items(), key=lambda item: -item[1]):
        print(f"    {name:<24}{count:>12,}  {count / total:7.1%}")


def _loader(torch: Any, dataset: Any, config: Mapping[str, Any], shuffle: bool):
    generator = torch.Generator()
    generator.manual_seed(config["random_seed"])
    return torch.utils.data.DataLoader(
        dataset,
        batch_size=int(config["params"]["batch_size"]),
        shuffle=shuffle,
        num_workers=int(config["params"]["num_workers"]),
        generator=generator if shuffle else None,
        drop_last=False,
    )


def _epoch_rmse(torch: Any, model: Any, loader: Any, device: Any, optimizer: Any = None, clip: float = 0.0) -> float:
    training = optimizer is not None
    model.train(training)
    squared_error = 0.0
    count = 0
    context = torch.enable_grad() if training else torch.no_grad()
    with context:
        for values, labels in loader:
            values = values.to(device)
            labels = labels.to(device)
            if training:
                optimizer.zero_grad(set_to_none=True)
            prediction = model(values)
            loss = torch.mean((prediction - labels) ** 2)
            if training:
                loss.backward()
                if clip > 0:
                    torch.nn.utils.clip_grad_norm_(model.parameters(), clip)
                optimizer.step()
            squared_error += float(torch.sum((prediction.detach() - labels) ** 2).cpu())
            count += int(labels.numel())
    if count == 0:
        raise RuntimeError("empty PyTorch sequence DataLoader")
    return math.sqrt(squared_error / count)


def _validation_rmse_ic(torch: Any, model: Any, loader: Any, device: Any) -> tuple[float, float]:
    """Evaluate one PyTorch sequence checkpoint once and return RMSE plus IC."""
    model.eval()
    predictions: list[np.ndarray] = []
    targets: list[np.ndarray] = []
    with torch.no_grad():
        for values, labels in loader:
            prediction = model(values.to(device))
            predictions.append(
                prediction.detach().cpu().numpy().astype(np.float64, copy=False)
            )
            targets.append(labels.numpy().astype(np.float64, copy=False))
    if not predictions:
        raise RuntimeError("empty PyTorch sequence Validation DataLoader")
    prediction_values = np.concatenate(predictions)
    target_values = np.concatenate(targets)
    rmse = float(
        np.sqrt(np.mean(np.square(prediction_values - target_values), dtype=np.float64))
    )
    return rmse, _pearson_ic(prediction_values, target_values)


@register_training_method("itransformer")
class _ITransformerAdapter(_BaseAdapter):
    def fit_selection(self, fit: _SplitData, valid: _SplitData, plotter: _LiveLossPlot) -> _FitOutcome:
        method = str(self.config["method"])
        torch = _require_dependency("torch", method)
        thread_policy = _configure_torch_threads(torch, self.config)
        _set_random_seeds(self.config["random_seed"], torch)
        device = _torch_device(torch, str(self.config["params"]["device"]))
        fit_dataset = _build_window_dataset(fit, self.config, torch)
        valid_dataset = _build_window_dataset(valid, self.config, torch)
        train_loader = _loader(torch, fit_dataset, self.config, shuffle=True)
        valid_loader = _loader(torch, valid_dataset, self.config, shuffle=False)
        model = _make_torch_sequence_model(
            torch,
            method,
            len(self.feature_names),
            self.config["params"],
        ).to(device)
        _log_torch_model_summary(
            model,
            method,
            "selection",
            len(self.feature_names),
            int(self.config["params"]["window_size"]),
        )
        optimizer = torch.optim.AdamW(
            model.parameters(),
            lr=float(self.config["params"]["learning_rate"]),
            weight_decay=float(self.config["params"]["weight_decay"]),
        )
        train_values: list[float] = []
        valid_values: list[float] = []
        valid_ic_values: list[float] = []
        best_loss = float("inf")
        best_iteration = 0
        best_state = None

        for epoch in range(1, self.config["max_iterations"] + 1):
            train_rmse = _epoch_rmse(
                torch, model, train_loader, device, optimizer,
                float(self.config["params"]["gradient_clip_norm"]),
            )
            valid_rmse, valid_ic = _validation_rmse_ic(
                torch, model, valid_loader, device
            )
            train_values.append(train_rmse)
            valid_values.append(valid_rmse)
            valid_ic_values.append(valid_ic)
            if valid_rmse < best_loss:
                best_loss = valid_rmse
                best_iteration = epoch
                best_state = {name: tensor.detach().cpu().clone() for name, tensor in model.state_dict().items()}
            plotter.update(train_values, valid_values, valid_ic_values)

        if best_state is None:
            raise RuntimeError(f"{method} did not produce a valid checkpoint")
        model.load_state_dict(best_state)
        model.to(device)
        frame = _history_frame(train_values, valid_values, valid_ic_values)
        best_ic_iteration, best_ic = _best_ic(valid_ic_values)
        plotter.update(train_values, valid_values, valid_ic_values, final=True)
        return _FitOutcome(
            model=model,
            history=frame,
            best_iteration=best_iteration,
            best_valid_loss=float(best_loss),
            best_valid_ic_iteration=best_ic_iteration,
            best_valid_ic=best_ic,
            metadata={
                "device": str(device),
                "cpu_threads": thread_policy["intra_op_threads"],
                "cpu_interop_threads": thread_policy["inter_op_threads"],
            },
        )

    def fit_frozen(
        self,
        parts: Sequence[_SplitData],
        fixed_iterations: int,
        plotter: _LiveFullTrainPlot,
    ) -> _FrozenOutcome:
        method = str(self.config["method"])
        torch = _require_dependency("torch", method)
        thread_policy = _configure_torch_threads(torch, self.config)
        _set_random_seeds(self.config["random_seed"], torch)
        device = _torch_device(torch, str(self.config["params"]["device"]))
        datasets = []
        for part in parts:
            dataset = _build_window_dataset(part, self.config, torch)
            datasets.append(dataset)
        full_dataset = torch.utils.data.ConcatDataset(datasets)
        loader = _loader(torch, full_dataset, self.config, shuffle=True)
        model = _make_torch_sequence_model(
            torch,
            method,
            len(self.feature_names),
            self.config["params"],
        ).to(device)
        _log_torch_model_summary(
            model,
            method,
            "final",
            len(self.feature_names),
            int(self.config["params"]["window_size"]),
        )
        optimizer = torch.optim.AdamW(
            model.parameters(),
            lr=float(self.config["params"]["learning_rate"]),
            weight_decay=float(self.config["params"]["weight_decay"]),
        )
        values: list[float] = []
        for epoch in range(1, fixed_iterations + 1):
            values.append(
                _epoch_rmse(
                    torch, model, loader, device, optimizer,
                    float(self.config["params"]["gradient_clip_norm"]),
                )
            )
            plotter.update(values)
        frame = pd.DataFrame({"iteration": np.arange(1, fixed_iterations + 1), "full_train_rmse": values})
        plotter.update(values, final=True)
        return _FrozenOutcome(
            model=model,
            history=frame,
            metadata={
                "device": str(device),
                "cpu_threads": thread_policy["intra_op_threads"],
                "cpu_interop_threads": thread_policy["inter_op_threads"],
            },
        )


@register_training_method("transformer")
class _TransformerAdapter(_ITransformerAdapter):
    """Standard time-token Transformer using the shared train loop."""


@limit_numeric_threads
@temporal_operation
def train_factor_model(
    x: pd.DataFrame,
    y: Target,
    target: Optional[str],
    method: str,
    training_stage: Union[str, int],
    training_iterations: int,
    fit_start: Any,
    fit_end: Any,
    valid_start: Any,
    valid_end: Any,
    config: Optional[Mapping[str, Any]] = None,
    output_dir: Optional[Union[str, os.PathLike[str]]] = "runs/training",
    ic_y: Optional[Target] = None,
) -> TrainingResult:
    """Execute one training stage with declared point-in-time boundaries.

    Parameters
    ----------
    x, y:
        Already processed/sampled factor and target objects with identical
        ``Date, Contract, Datetime`` MultiIndex.
    target:
        Label column such as ``"ret_30m"``. Required when ``y`` is a DataFrame.
    method:
        ``"xgboost"``, ``"lightgbm"``, ``"catboost"``, ``"itransformer"``
        or ``"transformer"``.
    training_stage:
        ``"selection"``/``1`` means stage 1: fit only on Fit and evaluate on
        Validation. ``"final"``/``2`` means stage 2: fit a new model on
        Fit + Validation without model selection.
    training_iterations:
        Maximum rounds/epochs in stage 1. Exact fixed rounds/epochs in stage 2.
        Normally stage 2 receives ``stage1_result.best_iteration``.
    fit_start, fit_end, valid_start, valid_end:
        Half-open time intervals ``[fit_start, fit_end)`` and
        ``[valid_start, valid_end)``.  Contiguous intervals may therefore use
        the same boundary for ``fit_end`` and ``valid_start``.
    config:
        Normally created with :func:`make_training_config` and then edited.
    output_dir:
        Stage 2 atomically saves the native model, full-training history and
        manifest beneath this directory. It is ignored in stage 1. ``None`` is
        rejected in stage 2 because a final model must be persisted.

    Notes
    -----
    This function intentionally has no Test arguments. One call never runs both
    stages. The stage-1 result must be inspected and its selected iteration
    passed explicitly to a separate stage-2 call before Test is read.
    """
    if (
        not isinstance(training_iterations, int)
        or isinstance(training_iterations, bool)
        or training_iterations <= 0
    ):
        raise ValueError("training_iterations must be a positive integer")

    stage = _canonical_training_stage(training_stage)
    if stage == "final" and output_dir is None:
        raise ValueError(
            "output_dir is required for training_stage='final'; the final "
            "model may not exist only in memory"
        )
    selected_y, datetimes, feature_names = validate_training_inputs(x, y, target)
    target_name = str(selected_y.name) if selected_y.name is not None else (target or "target")
    # 独立的 IC 评估标签：提供 ic_y 时用它算 IC，否则 IC 用训练 label（target）。
    ic_label = _extract_ic_series(ic_y, x) if ic_y is not None else None
    fs, fe, vs, ve = _normalise_bounds(fit_start, fit_end, valid_start, valid_end)
    normalised_config = _normalise_config(
        method,
        config,
        training_stage=stage,
        training_iterations=training_iterations,
    )
    normalised_method = normalised_config["method"]
    if normalised_method not in _TRAINING_METHODS:
        raise KeyError(
            f"method {normalised_method!r} has no registered trainer; available: {available_training_methods()}"
        )

    label_ends, temporal_audit = temporal_metadata_for(x.index)
    fit_mask = np.asarray((datetimes >= fs) & (datetimes < fe))
    valid_mask = np.asarray((datetimes >= vs) & (datetimes < ve))
    raw_fit_count, raw_valid_count = int(fit_mask.sum()), int(valid_mask.sum())
    cutoff = temporal_fit_cutoff(ve)
    validate_preprocessing_boundary(x, vs if stage == "selection" else cutoff)
    fit_mask &= np.asarray(label_ends < (vs if stage == "selection" else cutoff))
    valid_mask &= np.asarray(label_ends < (ve if stage == "selection" else cutoff))
    temporal_audit.update({"purged_fit_rows": raw_fit_count - int(fit_mask.sum()),
                           "purged_valid_rows": raw_valid_count - int(valid_mask.sum()),
                           "fit_as_of": str(cutoff),
                           "max_training_label_end": str(label_ends[fit_mask | (valid_mask if stage == "final" else False)].max()),
                           "purge_check_executed": True})
    if np.any(fit_mask & valid_mask):
        raise RuntimeError("internal error: Fit and Validation masks overlap")
    if stage == "selection":
        fit = _make_split("fit", fit_mask, x, selected_y, ic_label, fs, fe)
        valid = _make_split("valid", valid_mask, x, selected_y, ic_label, vs, ve)
        final_parts: Sequence[_SplitData] = ()
        split_audit_sources = {
            "fit": (fit, None, fs, fe, "fit"),
            "valid": (valid, None, vs, ve, "validation_only"),
        }
    else:
        full_mask = fit_mask | valid_mask
        full = _make_split("full_train", full_mask, x, selected_y, ic_label, fs, ve)
        full_datetimes = pd.DatetimeIndex(full.index.get_level_values("Datetime"))
        full_fit_mask = np.asarray((full_datetimes >= fs) & (full_datetimes < fe))
        full_valid_mask = np.asarray((full_datetimes >= vs) & (full_datetimes < ve))
        if np.any(full_fit_mask & full_valid_mask):
            raise RuntimeError("internal error: final Fit/Validation audit masks overlap")
        if int(full_fit_mask.sum() + full_valid_mask.sum()) != len(full.X):
            raise RuntimeError("internal error: final training union contains unassigned rows")
        final_parts = (full,)
        split_audit_sources = {
            "fit": (full, full_fit_mask, fs, fe, "fit"),
            "valid": (full, full_valid_mask, vs, ve, "fit"),
        }

    _set_random_seeds(normalised_config["random_seed"])
    adapter = _TRAINING_METHODS[normalised_method](normalised_config, feature_names)

    started = time.perf_counter()
    selection_seconds = 0.0
    final_training_seconds = 0.0
    selection_history = pd.DataFrame(
        columns=["iteration", "train_rmse", "valid_rmse", "valid_pearson_ic", "valid_label_ic"]
    )
    frozen_history = pd.DataFrame(
        columns=["iteration", "full_train_rmse"]
    )
    selection_metadata: dict[str, Any] = {}
    frozen_metadata: dict[str, Any] = {}
    selection_model: Optional[Any] = None
    best_validation_loss: Optional[float] = None
    best_validation_loss_iteration: Optional[int] = None
    best_validation_ic: Optional[float] = None
    best_validation_ic_iteration: Optional[int] = None

    if stage == "selection":
        plotter = _LiveLossPlot(
            enabled=normalised_config["live_plot"]["enabled"],
            update_every=normalised_config["live_plot"]["update_every"],
            title=f"{normalised_method} — {target_name} — Fit/Validation RMSE",
        )
        selection_started = time.perf_counter()
        selection = adapter.fit_selection(fit, valid, plotter)
        selection_seconds = time.perf_counter() - selection_started
        if selection.best_iteration < 1 or selection.best_iteration > len(selection.history):
            raise RuntimeError("selected iteration is outside the recorded history")
        explicit_best = int(selection.history["valid_rmse"].to_numpy().argmin()) + 1
        if explicit_best != selection.best_iteration:
            raise RuntimeError(
                "adapter-selected iteration does not equal explicit minimum Validation RMSE"
            )
        explicit_ic_iteration, explicit_best_ic = _best_ic(
            selection.history["valid_pearson_ic"]
        )
        # IC 不可用时（例如 CatBoost 1.2.10 不能挂自定义 IC metric）跳过 IC 自检，
        # RMSE 选轮的自检仍然强制执行。
        if np.isfinite(explicit_best_ic) or np.isfinite(selection.best_valid_ic):
            if explicit_ic_iteration != selection.best_valid_ic_iteration:
                raise RuntimeError(
                    "adapter-selected Validation IC iteration does not equal the "
                    "explicit maximum Pearson IC iteration"
                )
            if not np.isclose(
                explicit_best_ic,
                selection.best_valid_ic,
                rtol=1e-12,
                atol=1e-15,
            ):
                raise RuntimeError(
                    "adapter-reported best Validation IC differs from the history"
                )
        if len(selection.history) > training_iterations:
            raise RuntimeError("stage 1 exceeded requested training_iterations")

        final_model = selection.model
        selection_model = selection.model
        selection_history = selection.history.copy()
        selection_metadata = deepcopy(selection.metadata)
        result_iteration = int(selection.best_iteration)
        best_validation_loss = float(selection.best_valid_loss)
        best_validation_loss_iteration = int(selection.best_iteration)
        if selection.best_valid_ic is None or not np.isfinite(selection.best_valid_ic):
            best_validation_ic = None
        else:
            best_validation_ic = float(selection.best_valid_ic)
        best_validation_ic_iteration = (
            None
            if selection.best_valid_ic_iteration is None
            else int(selection.best_valid_ic_iteration)
        )
    else:
        final_plotter = _LiveFullTrainPlot(
            enabled=normalised_config["live_plot"]["enabled"],
            update_every=normalised_config["live_plot"]["update_every"],
            title=f"{normalised_method} — {target_name} — Final Fit+Validation RMSE",
        )
        final_started = time.perf_counter()
        frozen = adapter.fit_frozen(final_parts, training_iterations, final_plotter)
        final_training_seconds = time.perf_counter() - final_started
        final_model = frozen.model
        frozen_history = frozen.history.copy()
        frozen_metadata = deepcopy(frozen.metadata)
        result_iteration = int(training_iterations)

        if final_model is None:
            raise RuntimeError("stage 2 did not return a final model")
        if frozen_history.empty:
            raise RuntimeError("stage 2 did not return a full-training loss history")
        if int(frozen_history.iloc[-1]["iteration"]) != training_iterations:
            raise RuntimeError(
                "stage-2 history does not end at the explicitly requested "
                "training_iterations"
            )

    total_seconds = time.perf_counter() - started
    split_audit = {
        name: _split_audit_from_matrix(
            source,
            name=name,
            row_mask=row_mask,
            start=start,
            end=end,
            role=role,
        )
        for name, (source, row_mask, start, end, role) in split_audit_sources.items()
    }
    # The native trainer has finished and the split audit is materialised.
    # Release multi-gigabyte float32 split matrices before hashing the original
    # feature frame and assembling result metadata.
    split_audit_sources.clear()
    if stage == "selection":
        del fit, valid
    else:
        del full, final_parts, full_fit_mask, full_valid_mask

    config_hash = _sha256_json(normalised_config)
    schema_hash = _sha256_json(feature_names)
    training_feature_sha256 = frame_fingerprint(
        x,
        positions=np.flatnonzero(fit_mask | valid_mask),
    )
    data_binding = {
        "source_sha256": {name: _sha256_file(Path(__file__).with_name(name + ".py"))
                          for name in ("factor_model_training", "factor_runtime")},
        "fit_index": split_audit["fit"]["index_fingerprint"],
        "valid_index": split_audit["valid"]["index_fingerprint"],
        "target": target_name,
        "training_stage": stage,
        "temporal_audit": temporal_audit,
        "requested_iterations": training_iterations,
        "feature_schema_sha256": schema_hash,
        "training_feature_values_sha256": training_feature_sha256,
        "config_sha256": config_hash,
        "processing_fingerprint": x.attrs.get("processing_fingerprint"),
        "sampling_result_fingerprint": x.attrs.get("sampling_result_fingerprint"),
        "fit_target_sha256": _target_fingerprint(selected_y.loc[fit_mask]),
        "valid_target_sha256": _target_fingerprint(selected_y.loc[valid_mask]),
    }
    run_id = _sha256_json(data_binding)[:16]
    if stage == "selection":
        best_params = {
            "method": normalised_method,
            "training_stage": stage,
            "selected_by": "minimum Validation RMSE",
            "requested_max_iterations": int(training_iterations),
            "iterations_actually_run": int(len(selection_history)),
            "best_iteration": result_iteration,
            "best_validation_rmse": best_validation_loss,
            "best_validation_rmse_iteration": best_validation_loss_iteration,
            "best_validation_pearson_ic": best_validation_ic,
            "best_validation_pearson_ic_iteration": best_validation_ic_iteration,
            "checkpoint_selected_by_ic": False,
            "params": deepcopy(normalised_config["params"]),
        }
    else:
        best_params = {
            "method": normalised_method,
            "training_stage": stage,
            "selected_by": None,
            "fixed_iterations_source": "explicit training_iterations input",
            "final_fixed_iterations": result_iteration,
            "best_validation_rmse": None,
            "best_validation_rmse_iteration": None,
            "best_validation_pearson_ic": None,
            "best_validation_pearson_ic_iteration": None,
            "params": deepcopy(normalised_config["params"]),
        }
    audit = {
        "library_version": __version__,
        "temporal_audit": temporal_audit,
        "run_id": run_id,
        "method": normalised_method,
        "target": target_name,
        "training_stage": stage,
        "requested_iterations": int(training_iterations),
        "feature_count": len(feature_names),
        "feature_schema_sha256": schema_hash,
        "training_feature_values_sha256": training_feature_sha256,
        "config": deepcopy(normalised_config),
        "config_sha256": config_hash,
        "split_audit": split_audit,
        "selection_metadata": selection_metadata,
        "frozen_metadata": frozen_metadata,
        "selection_training_seconds": selection_seconds,
        "final_training_seconds": final_training_seconds,
        "total_training_seconds": total_seconds,
        "result_iteration": result_iteration,
        "best_iteration": result_iteration if stage == "selection" else None,
        "best_validation_rmse": best_validation_loss,
        "best_validation_rmse_iteration": best_validation_loss_iteration,
        "best_validation_pearson_ic": best_validation_ic,
        "best_validation_pearson_ic_iteration": best_validation_ic_iteration,
        "validation_ic_definition": (
            "signed pooled Pearson correlation across pairwise-finite Valid rows"
            if stage == "selection"
            else None
        ),
        "selection_metric_is_validation_rmse": stage == "selection",
        "validation_ic_is_diagnostic_only": stage == "selection",
        "validation_used_for_model_selection": stage == "selection",
        "validation_used_for_training": stage == "final",
        "fit_and_valid_combined_only_in_final_stage": stage == "final",
        "one_call_executed_exactly_one_stage": True,
        "final_model_starts_from_new_model": stage == "final",
        # A training function cannot observe whether unrelated caller code has
        # already opened Test data.  It can only prove that Test is absent from
        # this API and from its Fit/Validation matrices.
        "test_was_read": None,
        "test_data_accepted_by_training_api": False,
        "x_y_index_exactly_equal": True,
        "processing_fingerprint": x.attrs.get("processing_fingerprint"),
        "sampling_result_fingerprint": x.attrs.get("sampling_result_fingerprint"),
        "fit_target_sha256": data_binding["fit_target_sha256"],
        "valid_target_sha256": data_binding["valid_target_sha256"],
        "runtime_threads": runtime_thread_snapshot(),
        "source_sha256": {
            "factor_model_training": _sha256_file(Path(__file__)),
            "factor_runtime": _sha256_file(Path(__file__).with_name("factor_runtime.py")),
        },
        "versions": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            (
                "torch"
                if normalised_method in {"itransformer", "transformer"}
                else normalised_method
            ): _package_version(
                "torch"
                if normalised_method in {"itransformer", "transformer"}
                else normalised_method
            ),
        },
    }
    artifact_paths: dict[str, str] = {}
    if stage == "final":
        artifact_paths, artifact_hashes = _save_final_artifacts(
            model=final_model,
            method=normalised_method,
            target=target_name,
            feature_names=feature_names,
            config=normalised_config,
            training_iterations=training_iterations,
            history=frozen_history,
            audit=audit,
            run_id=run_id,
            output_dir=output_dir,
        )
        audit["artifact_paths"] = deepcopy(artifact_paths)
        audit["artifact_sha256"] = deepcopy(artifact_hashes)
        audit["final_model_saved"] = True
    else:
        audit["artifact_paths"] = {}
        audit["artifact_sha256"] = {}
        audit["final_model_saved"] = False
    return TrainingResult(
        model=final_model,
        selection_model=selection_model,
        method=normalised_method,
        target=target_name,
        training_stage=stage,
        requested_iterations=int(training_iterations),
        feature_names=feature_names,
        history=selection_history.copy(),
        frozen_history=frozen_history.copy(),
        best_iteration=result_iteration,
        best_validation_loss=best_validation_loss,
        best_validation_loss_iteration=best_validation_loss_iteration,
        best_validation_ic=best_validation_ic,
        best_validation_ic_iteration=best_validation_ic_iteration,
        best_params=best_params,
        refit_full=stage == "final",
        artifact_paths=artifact_paths,
        audit=audit,
    )
