"""Leakage-controlled walk-forward OOF Ridge stacking for 12 factor groups.

This module orchestrates the existing project modules instead of replacing them:

``factor_processing.process_factor_data(execute=False)``
    validates the raw factor/label contract without scaling the factors.
``factor_sampling.sample_factor_data``
    applies caller-declared intraday sampling rules.
``factor_grouping.extract_factor_groups``
    creates the mutually exclusive G01--G12 factor frames.
``factor_model_training.train_factor_model(training_stage="final")``
    trains every fixed-round XGBoost base model.

The public workflow is:

1. :func:`run_walk_forward_oof` creates base-model OOF predictions.
2. :func:`fit_ridge_meta_model` fits StandardScaler + fixed-alpha Ridge.
3. :func:`fit_final_base_models` refits G01--G12 through an explicit cutoff.
4. Only then load Test data and call :func:`evaluate_final_ridge_stack`.

The module never tunes a parameter, never runs the selection stage, never
intersects mismatched indices silently, and never fills a missing base-model
prediction with zero.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import tempfile
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional, Sequence, Union

import joblib
import numpy as np
import pandas as pd
from .factor_runtime import (temporal_operation, temporal_metadata_for, fit_as_of,
                             require_plan_frozen_before, frame_fingerprint, CPU_THREADS, validate_preprocessing_boundary)
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler


__version__ = "1.0.0"

EXPECTED_INDEX_NAMES = ("Date", "Contract", "Datetime")
EXPECTED_GROUPS = tuple(f"G{number:02d}" for number in range(1, 13))


Target = Union[pd.Series, pd.DataFrame]

__all__ = [
    "WalkForwardFold",
    "WalkForwardOOFResult",
    "RidgeMetaModel",
    "FinalBaseModels",
    "StackEvaluationResult",
    "make_default_walk_forward_folds",
    "validate_group_xgb_plans",
    "run_walk_forward_oof",
    "fit_ridge_meta_model",
    "fit_final_base_models",
    "evaluate_final_ridge_stack",
]


@dataclass(frozen=True)
class WalkForwardFold:
    """One expanding-window base-model training/prediction fold.

    ``fit`` and ``valid`` are both training data because every call uses the
    existing trainer's ``training_stage='final'`` path. The names are retained
    only to match that module's interface.
    """

    fold_id: str
    fit_start: pd.Timestamp
    fit_end: pd.Timestamp
    valid_start: pd.Timestamp
    valid_end: pd.Timestamp
    predict_start: pd.Timestamp
    predict_end: pd.Timestamp


@dataclass(frozen=True)
class WalkForwardOOFResult:
    """Strict base-model OOF table used to train the Ridge meta-model."""

    features: pd.DataFrame
    target: pd.Series
    fold_ids: pd.Series
    group_order: tuple[str, ...]
    group_feature_names: Mapping[str, tuple[str, ...]]
    artifact_paths: Mapping[str, Any]
    audit: Mapping[str, Any]


@dataclass(frozen=True)
class RidgeMetaModel:
    """Frozen StandardScaler + Ridge and their exact OOF binding."""

    scaler: StandardScaler
    ridge: Ridge
    group_order: tuple[str, ...]
    target_name: str
    alpha: float
    fit_intercept: bool
    positive: bool
    coefficients: pd.DataFrame
    oof_predictions: pd.DataFrame
    artifact_paths: Mapping[str, str]
    audit: Mapping[str, Any]


@dataclass(frozen=True)
class FinalBaseModels:
    """Twelve XGBoost models refitted through the final development date."""

    models: Mapping[str, Any]
    group_order: tuple[str, ...]
    group_feature_names: Mapping[str, tuple[str, ...]]
    target_name: str
    artifact_paths: Mapping[str, Any]
    audit: Mapping[str, Any]


@dataclass(frozen=True)
class StackEvaluationResult:
    """Immutable Test predictions, comparisons, metrics and audit records."""

    predictions: pd.DataFrame
    overall_metrics: pd.DataFrame
    monthly_metrics: pd.DataFrame
    daily_metrics: pd.DataFrame
    monthly_comparison: pd.DataFrame
    artifact_paths: Mapping[str, str]
    audit: Mapping[str, Any]


def make_default_walk_forward_folds(*, training_start, first_predict_start, n_folds=2):
    """Build annual folds from caller-provided dates; all endpoints are inclusive."""
    if not isinstance(n_folds, int) or isinstance(n_folds, bool) or n_folds < 1:
        raise ValueError("n_folds must be a positive integer")
    folds = []
    for number in range(n_folds):
        start = pd.Timestamp(first_predict_start) + pd.DateOffset(years=number)
        valid_start = start - pd.DateOffset(years=1)
        folds.append(WalkForwardFold(
            fold_id=f"oof_{number + 1:02d}", fit_start=pd.Timestamp(training_start),
            fit_end=valid_start - pd.Timedelta(nanoseconds=1),
            valid_start=valid_start, valid_end=start - pd.Timedelta(nanoseconds=1),
            predict_start=start,
            predict_end=start + pd.DateOffset(years=1) - pd.Timedelta(nanoseconds=1),
        ))
    return _validate_folds(folds)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        result = float(value)
        return result if np.isfinite(result) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, np.ndarray):
        return _jsonable(value.tolist())
    if isinstance(value, pd.Series):
        return _jsonable(value.to_dict())
    return value


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _package_version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _sha256_file(path: Union[str, os.PathLike[str]]) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _index_fingerprint(index: pd.MultiIndex) -> str:
    frame = index.to_frame(index=False)
    hashed = pd.util.hash_pandas_object(frame, index=False).to_numpy(np.uint64)
    digest = hashlib.sha256()
    digest.update("|".join(map(str, index.names)).encode("utf-8"))
    digest.update(hashed.tobytes())
    return digest.hexdigest()


def _series_fingerprint(series: pd.Series) -> str:
    values = pd.to_numeric(series, errors="coerce").to_numpy(np.float64)
    digest = hashlib.sha256()
    digest.update(_index_fingerprint(series.index).encode("ascii"))
    digest.update(np.ascontiguousarray(values).tobytes())
    return digest.hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(_jsonable(payload), handle, ensure_ascii=False, indent=2)
        os.replace(temporary_name, path)
    except Exception:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
        raise


def _atomic_csv(frame: Union[pd.DataFrame, pd.Series], path: Path, *, index: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    os.close(descriptor)
    try:
        frame.to_csv(temporary_name, index=index)
        os.replace(temporary_name, path)
    except Exception:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
        raise


def _atomic_parquet(frame: Union[pd.DataFrame, pd.Series], path: Path) -> None:
    """Write Parquet atomically and give a useful dependency error."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".parquet", dir=str(path.parent)
    )
    os.close(descriptor)
    try:
        object_to_write = frame.to_frame() if isinstance(frame, pd.Series) else frame
        object_to_write.to_parquet(temporary_name, index=True)
        os.replace(temporary_name, path)
    except ImportError as exc:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
        raise ImportError(
            "Saving stacking artifacts requires pyarrow or fastparquet. "
            "The project already reads Parquet; run this Notebook in that same environment."
        ) from exc
    except Exception:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
        raise


def _atomic_save_xgboost_model(model: Any, path: Path) -> None:
    """Publish a native XGBoost model without exposing a partial final file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=path.suffix, dir=str(path.parent)
    )
    os.close(descriptor)
    try:
        model.save_model(temporary_name)
        os.replace(temporary_name, path)
    except Exception:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
        raise


def _atomic_joblib_dump(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    os.close(descriptor)
    try:
        joblib.dump(value, temporary_name)
        os.replace(temporary_name, path)
    except Exception:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
        raise


def _select_target(y: Target, target: Optional[str]) -> pd.Series:
    if isinstance(y, pd.Series):
        selected = y.copy(deep=False)
        if target is not None and selected.name is not None and str(selected.name) != str(target):
            raise ValueError(
                f"target={target!r} conflicts with y.name={selected.name!r}"
            )
        if selected.name is None:
            selected = selected.rename(target or "target")
        return selected
    if not isinstance(y, pd.DataFrame):
        raise TypeError("y must be a pandas Series or DataFrame")
    if target is None:
        if y.shape[1] != 1:
            raise ValueError("target is required when y contains multiple columns")
        return y.iloc[:, 0]
    if target not in y.columns:
        raise KeyError(f"target {target!r} is absent from y")
    return y[target]


def _validate_index(obj: Union[pd.DataFrame, pd.Series], name: str) -> pd.DatetimeIndex:
    if not isinstance(obj.index, pd.MultiIndex):
        raise TypeError(f"{name}.index must be a pandas MultiIndex")
    if tuple(obj.index.names) != EXPECTED_INDEX_NAMES:
        raise ValueError(
            f"{name}.index names must be {list(EXPECTED_INDEX_NAMES)}, "
            f"got {list(obj.index.names)}"
        )
    if not obj.index.is_unique:
        raise ValueError(f"{name} contains duplicate Date+Contract+Datetime keys")
    datetimes = pd.DatetimeIndex(obj.index.get_level_values("Datetime"))
    if datetimes.isna().any():
        raise ValueError(f"{name} contains NaT in Datetime")
    if not datetimes.is_monotonic_increasing:
        raise ValueError(
            f"{name} must be ordered by Datetime; this module never sorts silently"
        )
    contracts = pd.Index(obj.index.get_level_values("Contract").unique())
    if len(contracts) != 1:
        raise ValueError(
            f"this workflow requires one contract; {name} contracts={contracts.tolist()}"
        )
    return datetimes


def _validate_grouped_data(
    grouped_x: Mapping[str, pd.DataFrame],
    y: Target,
    target: Optional[str],
) -> tuple[pd.Series, pd.MultiIndex, Mapping[str, tuple[str, ...]]]:
    if not isinstance(grouped_x, Mapping):
        raise TypeError("grouped_x must be a mapping such as grouping_result.groups")
    actual_groups = tuple(grouped_x.keys())
    if actual_groups != EXPECTED_GROUPS:
        raise ValueError(
            f"grouped_x keys/order must be exactly {EXPECTED_GROUPS}; got {actual_groups}"
        )
    selected_y = _select_target(y, target)
    _validate_index(selected_y, "y")
    reference_index = selected_y.index
    feature_names: dict[str, tuple[str, ...]] = {}
    owners: dict[str, str] = {}
    for group in EXPECTED_GROUPS:
        frame = grouped_x[group]
        if not isinstance(frame, pd.DataFrame):
            raise TypeError(f"grouped_x[{group!r}] must be a DataFrame")
        _validate_index(frame, f"grouped_x[{group}]")
        if not frame.index.equals(reference_index):
            raise ValueError(
                f"{group} index differs from y; automatic intersection/reindex is forbidden"
            )
        if frame.columns.has_duplicates:
            raise ValueError(f"{group} contains duplicated factor columns")
        if frame.shape[1] == 0:
            raise ValueError(f"{group} contains zero factors")
        names = tuple(map(str, frame.columns))
        if str(selected_y.name) in names:
            raise RuntimeError(
                f"label leakage: target {selected_y.name!r} appears in {group} factors"
            )
        for name in names:
            if name in owners:
                raise ValueError(
                    f"factor {name!r} appears in both {owners[name]} and {group}"
                )
            owners[name] = group
        feature_names[group] = names
    return selected_y, reference_index, feature_names


def _validate_sampling_times(index: pd.MultiIndex, allowed_times=None) -> Mapping[str, int]:
    datetimes = pd.DatetimeIndex(index.get_level_values("Datetime"))
    observed = pd.Series(datetimes.strftime("%H:%M"), dtype="string")
    if allowed_times is not None:
        unexpected = sorted(set(observed.dropna().unique()) - set(allowed_times))
        if unexpected:
            raise ValueError(f"rows outside declared sampling times: {unexpected}")
    counts = observed.value_counts().sort_index()
    return {str(key): int(value) for key, value in counts.items()}


def validate_group_xgb_plans(
    group_xgb_plans: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Mapping[str, Any]]:
    """Validate 12 explicit fixed-round XGBoost plans without adding defaults."""

    if not isinstance(group_xgb_plans, Mapping):
        raise TypeError("group_xgb_plans must be a mapping")
    if tuple(group_xgb_plans.keys()) != EXPECTED_GROUPS:
        raise ValueError(
            f"group_xgb_plans keys/order must be exactly {EXPECTED_GROUPS}"
        )
    required_params = {
        "objective",
        "eval_metric",
        "tree_method",
        "device",
        "eta",
        "max_depth",
        "subsample",
        "colsample_bytree",
        "reg_alpha",
        "reg_lambda",
        "seed",
        "nthread",
    }
    normalised: dict[str, Mapping[str, Any]] = {}
    seen_param_ids: set[int] = set()
    for group in EXPECTED_GROUPS:
        plan = group_xgb_plans[group]
        if not isinstance(plan, Mapping):
            raise TypeError(f"{group} plan must be a mapping")
        if set(plan) != {"iterations", "params"}:
            raise ValueError(
                f"{group} plan must contain exactly 'iterations' and 'params'; "
                f"got {sorted(plan)}"
            )
        iterations = plan["iterations"]
        if not isinstance(iterations, int) or isinstance(iterations, bool) or iterations <= 0:
            raise ValueError(f"{group}.iterations must be a positive integer")
        params = plan["params"]
        if not isinstance(params, Mapping):
            raise TypeError(f"{group}.params must be a mapping")
        if id(params) in seen_param_ids:
            raise ValueError(
                f"{group}.params reuses the same mutable mapping as another group; "
                "use an independent dict for every group"
            )
        seen_param_ids.add(id(params))
        missing = sorted(required_params - set(params))
        if missing:
            raise ValueError(f"{group}.params is incomplete; missing {missing}")
        forbidden = {"num_boost_round", "iterations", "early_stopping_rounds"}.intersection(params)
        if forbidden:
            raise ValueError(
                f"{group}.params contains iteration controls {sorted(forbidden)}; "
                "use plan['iterations'] only"
            )
        if params["objective"] != "reg:squarederror":
            raise ValueError(f"{group} objective must be reg:squarederror")
        if str(params["eval_metric"]).lower() != "rmse":
            raise ValueError(f"{group} eval_metric must be rmse")
        if int(params["nthread"]) != CPU_THREADS:
            raise ValueError(f"{group} nthread must match FUTURES_RESEARCH_THREADS in the frozen protocol")
        normalised[group] = {
            "iterations": int(iterations),
            "params": deepcopy(dict(params)),
        }
    return normalised


def _validate_folds(folds: Sequence[WalkForwardFold]) -> tuple[WalkForwardFold, ...]:
    if not folds:
        raise ValueError("at least one walk-forward fold is required")
    normalised: list[WalkForwardFold] = []
    previous_predict_end: Optional[pd.Timestamp] = None
    for position, raw in enumerate(folds):
        if not isinstance(raw, WalkForwardFold):
            raise TypeError("folds must contain WalkForwardFold instances")
        fold = WalkForwardFold(
            fold_id=str(raw.fold_id),
            fit_start=pd.Timestamp(raw.fit_start),
            fit_end=pd.Timestamp(raw.fit_end),
            valid_start=pd.Timestamp(raw.valid_start),
            valid_end=pd.Timestamp(raw.valid_end),
            predict_start=pd.Timestamp(raw.predict_start),
            predict_end=pd.Timestamp(raw.predict_end),
        )
        if not (
            fold.fit_start <= fold.fit_end
            < fold.valid_start <= fold.valid_end
            < fold.predict_start <= fold.predict_end
        ):
            raise ValueError(f"invalid or overlapping time bounds in {fold.fold_id}")
        if previous_predict_end is not None and fold.predict_start <= previous_predict_end:
            raise ValueError("OOF prediction intervals overlap or are not chronological")
        if position and fold.fit_start != normalised[0].fit_start:
            raise ValueError("walk-forward folds must share the same expanding-window start")
        previous_predict_end = fold.predict_end
        normalised.append(fold)
    if len({fold.fold_id for fold in normalised}) != len(normalised):
        raise ValueError("fold_id values must be unique")
    return tuple(normalised)


def _training_api() -> tuple[Any, Any]:
    try:
        from .factor_model_training import make_training_config, train_factor_model
    except ImportError as exc:
        raise ImportError(
            "Install futures_research with its model dependencies before training"
        ) from exc
    return make_training_config, train_factor_model


def _xgboost_module() -> Any:
    try:
        import xgboost as xgb
    except ImportError as exc:
        raise ImportError("xgboost is required to train/predict the base models") from exc
    return xgb


def _predict_xgboost(model: Any, frame: pd.DataFrame, feature_names: Sequence[str]) -> np.ndarray:
    xgb = _xgboost_module()
    values = frame.to_numpy(dtype=np.float32, na_value=np.nan, copy=True)
    values[~np.isfinite(values)] = np.nan
    matrix = xgb.DMatrix(
        values,
        feature_names=list(feature_names),
        missing=np.nan,
    )
    prediction = np.asarray(model.predict(matrix), dtype=np.float64).reshape(-1)
    if len(prediction) != len(frame):
        raise RuntimeError("XGBoost prediction length differs from input row count")
    if not np.isfinite(prediction).all():
        bad = int((~np.isfinite(prediction)).sum())
        raise RuntimeError(f"XGBoost returned {bad} non-finite predictions")
    return prediction


def _load_xgboost_model(path: Union[str, os.PathLike[str]]) -> Any:
    xgb = _xgboost_module()
    model = xgb.Booster()
    model.load_model(str(path))
    return model


def _make_fixed_final_config(params: Mapping[str, Any], random_seed: int) -> MutableMapping[str, Any]:
    make_training_config, _ = _training_api()
    config = make_training_config("xgboost", random_seed=random_seed)
    # Deliberately replace, never update, the default model params. This ensures
    # the exact hand-written group plan is the only source of model parameters.
    config["params"] = deepcopy(dict(params))
    config["early_stopping"] = {"enabled": False, "rounds": 1}
    config["live_plot"] = {"enabled": False, "update_every": 25}
    return config


def _mask_between(datetimes: pd.DatetimeIndex, start: pd.Timestamp, end: pd.Timestamp) -> np.ndarray:
    return np.asarray((datetimes >= start) & (datetimes <= end), dtype=bool)


def _train_one_final_model(
    *,
    x_train: pd.DataFrame,
    y_train: pd.Series,
    target_name: str,
    group: str,
    plan: Mapping[str, Any],
    fit_start: pd.Timestamp,
    fit_end: pd.Timestamp,
    valid_start: pd.Timestamp,
    valid_end: pd.Timestamp,
    output_dir: Path,
) -> Any:
    _, train_factor_model = _training_api()
    config = _make_fixed_final_config(plan["params"], int(plan["params"]["seed"]))
    print(
        f"[STACK][TRAIN] group={group} rows={len(x_train):,} "
        f"factors={x_train.shape[1]:,} iterations={plan['iterations']:,} "
        f"train={fit_start} -> {valid_end}"
    )
    result = train_factor_model(
        x=x_train,
        y=y_train,
        target=target_name,
        method="xgboost",
        training_stage="final",
        training_iterations=int(plan["iterations"]),
        fit_start=fit_start,
        fit_end=fit_end + pd.Timedelta(nanoseconds=1),
        valid_start=valid_start,
        valid_end=valid_end + pd.Timedelta(nanoseconds=1),
        config=config,
        output_dir=output_dir,
    )
    if result.training_stage != "final" or not result.refit_full:
        raise RuntimeError("base trainer did not return a frozen final model")
    if result.requested_iterations != int(plan["iterations"]):
        raise RuntimeError("base trainer changed the fixed iteration count")
    if tuple(result.feature_names) != tuple(map(str, x_train.columns)):
        raise RuntimeError("base trainer feature order differs from the requested group order")
    return result


def _checkpoint_signature(
    *,
    stage: str,
    group: str,
    plan: Mapping[str, Any],
    feature_names: Sequence[str],
    training_index: pd.MultiIndex,
    training_target: pd.Series,
    bounds: Mapping[str, pd.Timestamp],
    prediction_index: Optional[pd.MultiIndex] = None,
    training_features: pd.DataFrame,
    prediction_features: Optional[pd.DataFrame] = None,
) -> tuple[str, Mapping[str, Any]]:
    payload = {
        "protocol_version": __version__,
        "runtime_versions": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "xgboost": _package_version("xgboost"),
            "scikit_learn": _package_version("scikit-learn"),
        },
        "stage": stage,
        "contract_scope": "one_caller_supplied_contract",
        "group": group,
        "plan": deepcopy(dict(plan)),
        "feature_names": list(feature_names),
        "feature_schema_sha256": _sha256_json(list(feature_names)),
        "training_index_sha256": _index_fingerprint(training_index),
        "training_target_sha256": _series_fingerprint(training_target),
        "training_feature_values_sha256": frame_fingerprint(training_features),
        "prediction_feature_values_sha256": frame_fingerprint(prediction_features) if prediction_features is not None else None,
        "temporal_metadata": temporal_metadata_for(training_index)[1],
        "source_sha256": {name: _sha256_file(Path(__file__).with_name(name + ".py"))
                          for name in ("factor_ridge_stacking", "factor_model_training", "factor_runtime")},
        "prediction_temporal_metadata": temporal_metadata_for(prediction_index)[1] if prediction_index is not None else None,
        "bounds": dict(bounds),
        "prediction_index_sha256": (
            _index_fingerprint(prediction_index) if prediction_index is not None else None
        ),
    }
    return _sha256_json(payload), payload


def _valid_checkpoint(
    checkpoint_path: Path,
    expected_signature: str,
    required_files: Mapping[str, Path],
) -> Optional[Mapping[str, Any]]:
    if not checkpoint_path.exists():
        return None
    try:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if checkpoint.get("signature") != expected_signature:
        return None
    recorded_hashes = checkpoint.get("artifact_sha256", {})
    for name, path in required_files.items():
        if not path.exists() or recorded_hashes.get(name) != _sha256_file(path):
            return None
    return checkpoint


@temporal_operation
def run_walk_forward_oof(
    grouped_x: Mapping[str, pd.DataFrame],
    y: Target,
    target: Optional[str],
    group_xgb_plans: Mapping[str, Mapping[str, Any]],
    *,
    folds: Sequence[WalkForwardFold],
    plans_frozen_at: Any,
    sampling_times: Optional[Sequence[str]] = None,
    output_dir: Union[str, os.PathLike[str]] = "runs/ridge_stacking",
    resume: bool = True,
) -> WalkForwardOOFResult:
    """Train 12 fixed XGBoost models per fold and assemble strict OOF rows.

    Every base model is trained by the existing ``train_factor_model`` final
    stage. The prediction year is never passed to that training function.
    """

    started = time.perf_counter()
    selected_y, reference_index, group_feature_names = _validate_grouped_data(
        grouped_x, y, target
    )
    sampling_counts = _validate_sampling_times(reference_index, sampling_times)
    plans = validate_group_xgb_plans(group_xgb_plans)
    fold_specs = _validate_folds(folds)
    require_plan_frozen_before(plans_frozen_at, fold_specs[0].predict_start)
    label_ends, temporal_audit = temporal_metadata_for(reference_index)
    output_root = Path(output_dir).expanduser().resolve() / "walk_forward_oof"
    datetimes = pd.DatetimeIndex(reference_index.get_level_values("Datetime"))

    prediction_frames: list[pd.DataFrame] = []
    target_parts: list[pd.Series] = []
    fold_id_parts: list[pd.Series] = []
    fold_audits: dict[str, Any] = {}
    artifact_paths: dict[str, Any] = {}

    for fold in fold_specs:
        fit_mask = _mask_between(datetimes, fold.fit_start, fold.fit_end)
        valid_mask = _mask_between(datetimes, fold.valid_start, fold.valid_end)
        train_mask = (fit_mask | valid_mask) & np.asarray(label_ends < fold.predict_start)
        predict_mask = _mask_between(datetimes, fold.predict_start, fold.predict_end)
        if not fit_mask.any() or not valid_mask.any() or not predict_mask.any():
            raise ValueError(
                f"{fold.fold_id} has an empty fit, valid-as-training, or prediction interval"
            )
        if np.any(train_mask & predict_mask):
            raise RuntimeError(f"{fold.fold_id} training and prediction rows overlap")

        training_index = reference_index[train_mask]
        prediction_index = reference_index[predict_mask]
        training_target = selected_y.loc[training_index]
        fold_predictions = pd.DataFrame(index=prediction_index)
        fold_artifacts: dict[str, Any] = {}
        group_audits: dict[str, Any] = {}

        print(
            f"[STACK][FOLD] {fold.fold_id}: train_rows={int(train_mask.sum()):,} "
            f"predict_rows={int(predict_mask.sum()):,}"
        )
        for group in EXPECTED_GROUPS:
            validate_preprocessing_boundary(grouped_x[group], fold.predict_start)
            group_root = output_root / fold.fold_id / group
            checkpoint_path = group_root / "checkpoint.json"
            prediction_path = group_root / "oof_predictions.parquet"
            signature, signature_payload = _checkpoint_signature(
                stage="walk_forward_oof",
                group=group,
                plan=plans[group],
                feature_names=group_feature_names[group],
                training_index=training_index,
                training_target=training_target,
                training_features=grouped_x[group].loc[training_index],
                prediction_features=grouped_x[group].loc[prediction_index],
                bounds={
                    "fit_start": fold.fit_start,
                    "fit_end": fold.fit_end,
                    "valid_start": fold.valid_start,
                    "valid_end": fold.valid_end,
                    "predict_start": fold.predict_start,
                    "predict_end": fold.predict_end,
                },
                prediction_index=prediction_index,
            )
            checkpoint = (
                _valid_checkpoint(
                    checkpoint_path,
                    signature,
                    {"prediction": prediction_path},
                )
                if resume
                else None
            )
            if checkpoint is not None:
                cached_model_path = Path(
                    checkpoint.get("training_artifact_paths", {}).get("model", "")
                )
                cached_model_hash = checkpoint.get("artifact_sha256", {}).get(
                    "training_model"
                )
                if (
                    not cached_model_path.is_file()
                    or not cached_model_hash
                    or _sha256_file(cached_model_path) != cached_model_hash
                ):
                    checkpoint = None
            if checkpoint is not None:
                cached = pd.read_parquet(prediction_path)
                if not cached.index.equals(prediction_index):
                    raise RuntimeError(
                        f"validated checkpoint {fold.fold_id}/{group} has a mismatched index"
                    )
                column_name = f"pred_{group}"
                if tuple(cached.columns) != (column_name,):
                    raise RuntimeError(f"cached prediction schema is invalid for {group}")
                values = cached[column_name].to_numpy(np.float64)
                reused = True
                print(f"[STACK][RESUME] {fold.fold_id}/{group}")
            else:
                x_train = grouped_x[group].loc[training_index]
                y_train = selected_y.loc[training_index]
                with fit_as_of(fold.predict_start):
                    training_result = _train_one_final_model(
                        x_train=x_train,
                        y_train=y_train,
                        target_name=str(selected_y.name),
                        group=group,
                        plan=plans[group],
                        fit_start=fold.fit_start,
                        fit_end=fold.fit_end,
                        valid_start=fold.valid_start,
                        valid_end=fold.valid_end,
                        output_dir=group_root / "training",
                    )
                x_predict = grouped_x[group].loc[prediction_index]
                values = _predict_xgboost(
                    training_result.model,
                    x_predict,
                    group_feature_names[group],
                )
                prediction_frame = pd.DataFrame(
                    {f"pred_{group}": values}, index=prediction_index
                )
                _atomic_parquet(prediction_frame, prediction_path)
                training_model_path = Path(training_result.artifact_paths["model"])
                checkpoint_payload = {
                    "format_version": 1,
                    "signature": signature,
                    "signature_payload": signature_payload,
                    "training_run_id": training_result.audit.get("run_id"),
                    "training_artifact_paths": dict(training_result.artifact_paths),
                    "prediction_path": str(prediction_path),
                    "artifact_sha256": {
                        "prediction": _sha256_file(prediction_path),
                        "training_model": _sha256_file(training_model_path),
                    },
                    "created_at_utc": pd.Timestamp.utcnow(),
                }
                _atomic_json(checkpoint_path, checkpoint_payload)
                checkpoint = checkpoint_payload
                reused = False
            if not np.isfinite(values).all():
                raise RuntimeError(f"{fold.fold_id}/{group} contains non-finite predictions")
            fold_predictions[f"pred_{group}"] = values
            fold_artifacts[group] = {
                "checkpoint": str(checkpoint_path),
                "prediction": str(prediction_path),
                "training_artifact_paths": checkpoint.get("training_artifact_paths", {}),
            }
            group_audits[group] = {
                "reused_checkpoint": reused,
                "iterations": plans[group]["iterations"],
                "factor_count": len(group_feature_names[group]),
                "parameter_sha256": _sha256_json(plans[group]["params"]),
                "signature": signature,
            }

        expected_columns = tuple(f"pred_{group}" for group in EXPECTED_GROUPS)
        if tuple(fold_predictions.columns) != expected_columns:
            raise RuntimeError("internal error: OOF prediction column order changed")
        if not np.isfinite(fold_predictions.to_numpy(np.float64)).all():
            raise RuntimeError(f"{fold.fold_id} OOF table contains non-finite values")
        fold_target = selected_y.loc[prediction_index].copy()
        finite_target = np.isfinite(fold_target.to_numpy(np.float64, na_value=np.nan))
        if not finite_target.any():
            raise ValueError(f"{fold.fold_id} contains no finite OOF targets")
        # Label filtering is applied once, identically to all 12 predictions.
        fold_predictions = fold_predictions.loc[finite_target]
        fold_target = fold_target.loc[finite_target]
        fold_ids = pd.Series(
            fold.fold_id,
            index=fold_predictions.index,
            name="fold_id",
            dtype="string",
        )
        prediction_frames.append(fold_predictions)
        target_parts.append(fold_target)
        fold_id_parts.append(fold_ids)
        artifact_paths[fold.fold_id] = fold_artifacts
        fold_audits[fold.fold_id] = {
            "fit_start": fold.fit_start,
            "fit_end": fold.fit_end,
            "valid_start": fold.valid_start,
            "valid_end": fold.valid_end,
            "predict_start": fold.predict_start,
            "predict_end": fold.predict_end,
            "fit_rows": int(fit_mask.sum()),
            "valid_rows_used_as_training": int(valid_mask.sum()),
            "total_training_rows": int(train_mask.sum()),
            "raw_prediction_rows": int(predict_mask.sum()),
            "finite_target_oof_rows": len(fold_predictions),
            "dropped_nonfinite_oof_targets": int((~finite_target).sum()),
            "training_index_sha256": _index_fingerprint(training_index),
            "prediction_index_sha256": _index_fingerprint(prediction_index),
            "group_audits": group_audits,
            "training_and_prediction_overlap": False,
            "purge_check_executed": True,
        }

    oof_features = pd.concat(prediction_frames, axis=0)
    oof_target = pd.concat(target_parts, axis=0)
    oof_fold_ids = pd.concat(fold_id_parts, axis=0)
    if not oof_features.index.equals(oof_target.index) or not oof_features.index.equals(oof_fold_ids.index):
        raise RuntimeError("OOF features, labels and fold IDs are not exactly aligned")
    if not oof_features.index.is_unique:
        raise RuntimeError("OOF folds produced duplicated prediction keys")
    if not pd.DatetimeIndex(
        oof_features.index.get_level_values("Datetime")
    ).is_monotonic_increasing:
        raise RuntimeError("OOF rows are not chronological")
    oof_path = output_root / "oof_meta_features.parquet"
    target_path = output_root / "oof_target.parquet"
    fold_ids_path = output_root / "oof_fold_ids.parquet"
    _atomic_parquet(oof_features, oof_path)
    _atomic_parquet(oof_target.rename(str(selected_y.name)), target_path)
    _atomic_parquet(oof_fold_ids, fold_ids_path)

    audit = {
        "module_version": __version__,
        "contract_scope": "one_caller_supplied_contract",
        "target": str(selected_y.name),
        "group_order": list(EXPECTED_GROUPS),
        "group_factor_counts": {
            group: len(group_feature_names[group]) for group in EXPECTED_GROUPS
        },
        "group_feature_schema_sha256": {
            group: _sha256_json(group_feature_names[group]) for group in EXPECTED_GROUPS
        },
        "group_plan_sha256": {
            group: _sha256_json(plans[group]) for group in EXPECTED_GROUPS
        },
        "sampling_times": list(sampling_counts),
        "sampling_time_counts_development_input": sampling_counts,
        "strict_complete_grid": False,
        "factor_processing_method": None,
        "zscore_executed": False,
        "folds": fold_audits,
        "oof_row_count": len(oof_features),
        "oof_index_sha256": _index_fingerprint(oof_features.index),
        "oof_target_sha256": _series_fingerprint(oof_target),
        "oof_feature_sha256": _sha256_json(
            {
                "index": _index_fingerprint(oof_features.index),
                "columns": list(oof_features.columns),
                "values": hashlib.sha256(
                    np.ascontiguousarray(oof_features.to_numpy(np.float64)).tobytes()
                ).hexdigest(),
            }
        ),
        "all_base_predictions_are_out_of_sample": True,
        "plans_frozen_at_caller_declared": pd.Timestamp(plans_frozen_at),
        "temporal_audit": temporal_audit,
        "ridge_not_fitted_in_this_step": True,
        "test_was_read": None,
        "purge_check_executed": True,
        "elapsed_seconds": time.perf_counter() - started,
        "versions": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "xgboost": _package_version("xgboost"),
            "scikit_learn": _package_version("scikit-learn"),
        },
    }
    audit_path = output_root / "oof_audit.json"
    _atomic_json(audit_path, audit)
    artifact_paths["combined"] = {
        "features": str(oof_path),
        "target": str(target_path),
        "fold_ids": str(fold_ids_path),
        "audit": str(audit_path),
    }
    print(
        f"[STACK][OOF COMPLETE] shape={oof_features.shape} "
        f"elapsed={audit['elapsed_seconds']:.1f}s"
    )
    return WalkForwardOOFResult(
        features=oof_features,
        target=oof_target,
        fold_ids=oof_fold_ids,
        group_order=EXPECTED_GROUPS,
        group_feature_names=group_feature_names,
        artifact_paths=artifact_paths,
        audit=audit,
    )


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    finite = np.isfinite(a) & np.isfinite(b)
    if finite.sum() < 2:
        return float("nan")
    left = a[finite]
    right = b[finite]
    if np.std(left) == 0.0 or np.std(right) == 0.0:
        return float("nan")
    return float(np.corrcoef(left, right)[0, 1])


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    left = pd.Series(a).rank(method="average").to_numpy(np.float64)
    right = pd.Series(b).rank(method="average").to_numpy(np.float64)
    return _pearson(left, right)


def _metric_record(prediction: pd.Series, actual: pd.Series) -> Mapping[str, Any]:
    if not prediction.index.equals(actual.index):
        raise ValueError("prediction and actual indices differ")
    pred = prediction.to_numpy(np.float64)
    truth = actual.to_numpy(np.float64, na_value=np.nan)
    finite = np.isfinite(pred) & np.isfinite(truth)
    if not finite.any():
        return {
            "row_count": 0,
            "pearson_ic": np.nan,
            "spearman_ic": np.nan,
            "rmse": np.nan,
            "mae": np.nan,
        }
    error = pred[finite] - truth[finite]
    return {
        "row_count": int(finite.sum()),
        "pearson_ic": _pearson(pred, truth),
        "spearman_ic": _spearman(pred[finite], truth[finite]),
        "rmse": float(np.sqrt(np.mean(np.square(error)))),
        "mae": float(np.mean(np.abs(error))),
    }


def _distribution_summary(values: pd.Series, prefix: str) -> Mapping[str, Any]:
    finite = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    count = int(len(finite))
    mean = float(finite.mean()) if count else np.nan
    std = float(finite.std(ddof=1)) if count >= 2 else np.nan
    ir = mean / std if count >= 2 and np.isfinite(std) and std > 0 else np.nan
    t_stat = mean / (std / np.sqrt(count)) if count >= 2 and np.isfinite(std) and std > 0 else np.nan
    return {
        f"{prefix}_count": count,
        f"mean_{prefix}": mean,
        f"{prefix}ir": ir,
        f"positive_{prefix}_ratio": float((finite > 0).mean()) if count else np.nan,
        f"{prefix}_t_stat": t_stat,
    }


@temporal_operation
def fit_ridge_meta_model(
    oof_result: WalkForwardOOFResult,
    *,
    alpha: float = 1.0,
    fit_intercept: bool = True,
    positive: bool = False,
    solver: str = "auto",
    output_dir: Union[str, os.PathLike[str]] = "runs/ridge_stacking",
) -> RidgeMetaModel:
    """Fit the frozen StandardScaler + fixed-alpha Ridge on declared OOF rows."""

    if not isinstance(oof_result, WalkForwardOOFResult):
        raise TypeError("oof_result must be returned by run_walk_forward_oof")
    if not np.isfinite(alpha) or alpha <= 0:
        raise ValueError("alpha must be finite and > 0")
    expected_columns = tuple(f"pred_{group}" for group in EXPECTED_GROUPS)
    if tuple(oof_result.features.columns) != expected_columns:
        raise ValueError("OOF feature schema/order differs from the frozen 12-group schema")
    if not oof_result.features.index.equals(oof_result.target.index):
        raise ValueError("OOF features and target indices differ")
    meta_label_ends, temporal_audit = temporal_metadata_for(oof_result.features.index)
    if not oof_result.audit.get("purge_check_executed"):
        raise ValueError("OOF result has no label-end purge evidence")
    X = oof_result.features.to_numpy(np.float64)
    y = oof_result.target.to_numpy(np.float64, na_value=np.nan)
    if not np.isfinite(X).all() or not np.isfinite(y).all():
        raise ValueError("Ridge training requires 12 finite predictions and a finite target per row")

    scaler = StandardScaler(with_mean=True, with_std=True)
    X_scaled = scaler.fit_transform(X)
    if not np.isfinite(X_scaled).all():
        raise RuntimeError("StandardScaler produced non-finite values")
    zero_variance = np.asarray(scaler.var_) == 0.0
    if zero_variance.any():
        groups = [EXPECTED_GROUPS[index] for index in np.flatnonzero(zero_variance)]
        raise ValueError(f"constant OOF base predictions cannot enter Ridge: {groups}")
    ridge = Ridge(
        alpha=float(alpha),
        fit_intercept=bool(fit_intercept),
        positive=bool(positive),
        solver=str(solver),
    )
    ridge.fit(X_scaled, y)
    oof_prediction_values = np.asarray(ridge.predict(X_scaled), dtype=np.float64)
    if not np.isfinite(oof_prediction_values).all():
        raise RuntimeError("Ridge returned non-finite OOF fitted predictions")

    standardised_coef = np.asarray(ridge.coef_, dtype=np.float64).reshape(-1)
    raw_coef = standardised_coef / np.asarray(scaler.scale_, dtype=np.float64)
    raw_intercept = float(ridge.intercept_) - float(
        np.dot(standardised_coef, np.asarray(scaler.mean_) / np.asarray(scaler.scale_))
    )
    coefficients = pd.DataFrame(
        {
            "group": EXPECTED_GROUPS,
            "oof_mean": scaler.mean_,
            "oof_std": scaler.scale_,
            "ridge_coef_standardised": standardised_coef,
            "ridge_coef_raw_scale": raw_coef,
        }
    ).set_index("group")

    oof_predictions = oof_result.features.copy()
    oof_predictions["actual"] = oof_result.target
    oof_predictions["ridge_fitted_prediction"] = oof_prediction_values
    oof_predictions["equal_weight_prediction"] = oof_result.features.mean(axis=1)
    oof_predictions["fold_id"] = oof_result.fold_ids

    oof_prediction_correlations = oof_result.features.corr(method="pearson")
    oof_base_metric_records: list[dict[str, Any]] = []
    for column in expected_columns:
        record = dict(_metric_record(oof_result.features[column], oof_result.target))
        record["model"] = column
        oof_base_metric_records.append(record)
    oof_base_metrics = pd.DataFrame(oof_base_metric_records).set_index("model")

    output_root = Path(output_dir).expanduser().resolve() / "ridge_meta_model"
    output_root.mkdir(parents=True, exist_ok=True)
    scaler_path = output_root / "standard_scaler.joblib"
    ridge_path = output_root / "ridge.joblib"
    coefficients_path = output_root / "ridge_coefficients.csv"
    oof_predictions_path = output_root / "ridge_training_predictions.parquet"
    oof_correlations_path = output_root / "oof_prediction_correlations.csv"
    oof_base_metrics_path = output_root / "oof_base_metrics.csv"
    _atomic_joblib_dump(scaler, scaler_path)
    _atomic_joblib_dump(ridge, ridge_path)
    _atomic_csv(coefficients, coefficients_path, index=True)
    _atomic_parquet(oof_predictions, oof_predictions_path)
    _atomic_csv(oof_prediction_correlations, oof_correlations_path, index=True)
    _atomic_csv(oof_base_metrics, oof_base_metrics_path, index=True)

    ridge_metrics = _metric_record(
        oof_predictions["ridge_fitted_prediction"], oof_predictions["actual"]
    )
    equal_metrics = _metric_record(
        oof_predictions["equal_weight_prediction"], oof_predictions["actual"]
    )
    audit = {
        "module_version": __version__,
        "contract_scope": "one_caller_supplied_contract",
        "target": str(oof_result.target.name),
        "group_order": list(EXPECTED_GROUPS),
        "standard_scaler_fitted": True,
        "scaler_fit_rows": len(X),
        "scaler_fit_index_sha256": _index_fingerprint(oof_result.features.index),
        "ridge_alpha": float(alpha),
        "ridge_fit_intercept": bool(fit_intercept),
        "ridge_positive": bool(positive),
        "ridge_solver": str(solver),
        "ridge_fit_rows": len(X),
        "meta_fit_max_label_end": str(meta_label_ends.max()),
        "temporal_audit": temporal_audit,
        "ridge_fit_index_sha256": _index_fingerprint(oof_result.features.index),
        "ridge_fit_target_sha256": _series_fingerprint(oof_result.target),
        "source_oof_index_sha256": oof_result.audit["oof_index_sha256"],
        "source_oof_feature_sha256": oof_result.audit["oof_feature_sha256"],
        "ridge_oof_metrics": ridge_metrics,
        "equal_weight_oof_metrics": equal_metrics,
        "oof_base_metrics": oof_base_metrics.to_dict(orient="index"),
        "oof_prediction_correlation_sha256": _sha256_json(
            oof_prediction_correlations.to_dict()
        ),
        "ridge_oof_prediction_is_in_sample_meta_fit": True,
        "ridge_has_validation_set": False,
        "ridge_cv_executed": False,
        "test_was_read": None,
        "raw_scale_intercept": raw_intercept,
    }
    manifest_path = output_root / "ridge_manifest.json"
    audit["artifact_sha256"] = {
        "scaler": _sha256_file(scaler_path),
        "ridge": _sha256_file(ridge_path),
        "coefficients": _sha256_file(coefficients_path),
        "oof_predictions": _sha256_file(oof_predictions_path),
        "oof_correlations": _sha256_file(oof_correlations_path),
        "oof_base_metrics": _sha256_file(oof_base_metrics_path),
    }
    _atomic_json(manifest_path, audit)
    artifact_paths = {
        "scaler": str(scaler_path),
        "ridge": str(ridge_path),
        "coefficients": str(coefficients_path),
        "oof_predictions": str(oof_predictions_path),
        "oof_correlations": str(oof_correlations_path),
        "oof_base_metrics": str(oof_base_metrics_path),
        "manifest": str(manifest_path),
    }
    print(
        "[STACK][RIDGE COMPLETE] "
        f"rows={len(X):,} alpha={alpha} "
        f"fitted_pearson={ridge_metrics['pearson_ic']:.6g}"
    )
    return RidgeMetaModel(
        scaler=scaler,
        ridge=ridge,
        group_order=EXPECTED_GROUPS,
        target_name=str(oof_result.target.name),
        alpha=float(alpha),
        fit_intercept=bool(fit_intercept),
        positive=bool(positive),
        coefficients=coefficients,
        oof_predictions=oof_predictions,
        artifact_paths=artifact_paths,
        audit=audit,
    )


@temporal_operation
def fit_final_base_models(
    grouped_x: Mapping[str, pd.DataFrame],
    y: Target,
    target: Optional[str],
    group_xgb_plans: Mapping[str, Mapping[str, Any]],
    *,
    fit_start: Any,
    fit_end: Any,
    valid_start: Any,
    valid_end: Any,
    output_dir: Union[str, os.PathLike[str]] = "runs/ridge_stacking",
    resume: bool = True,
    as_of_time: Any,
    plans_frozen_at: Any,
    sampling_times: Optional[Sequence[str]] = None,
) -> FinalBaseModels:
    """Refit 12 frozen base models through an explicit cutoff."""

    started = time.perf_counter()
    selected_y, reference_index, group_feature_names = _validate_grouped_data(
        grouped_x, y, target
    )
    _validate_sampling_times(reference_index, sampling_times)
    plans = validate_group_xgb_plans(group_xgb_plans)
    fs, fe, vs, ve = map(pd.Timestamp, (fit_start, fit_end, valid_start, valid_end))
    require_plan_frozen_before(plans_frozen_at, as_of_time)
    if pd.Timestamp(as_of_time) <= ve:
        raise ValueError("as_of_time must be after the inclusive training end")
    label_ends, temporal_audit = temporal_metadata_for(reference_index)
    if not fs <= fe < vs <= ve:
        raise ValueError("final base-model fit/valid-as-training bounds are invalid")
    datetimes = pd.DatetimeIndex(reference_index.get_level_values("Datetime"))
    train_mask = (_mask_between(datetimes, fs, fe) | _mask_between(datetimes, vs, ve)) & np.asarray(label_ends < pd.Timestamp(as_of_time))
    if not train_mask.any():
        raise ValueError("final base-model training interval contains zero rows")
    if np.any(datetimes[train_mask] > ve):
        raise RuntimeError("internal error: final training contains rows after valid_end")
    training_index = reference_index[train_mask]
    training_target = selected_y.loc[training_index]
    output_root = Path(output_dir).expanduser().resolve() / "final_base_models"

    models: dict[str, Any] = {}
    artifact_paths: dict[str, Any] = {}
    group_audits: dict[str, Any] = {}
    for group in EXPECTED_GROUPS:
        validate_preprocessing_boundary(grouped_x[group], as_of_time)
        group_root = output_root / group
        checkpoint_path = group_root / "checkpoint.json"
        deterministic_model_path = group_root / "model.json"
        signature, signature_payload = _checkpoint_signature(
            stage="final_base_model",
            group=group,
            plan=plans[group],
            feature_names=group_feature_names[group],
            training_index=training_index,
            training_target=training_target,
            training_features=grouped_x[group].loc[training_index],
            bounds={
                "fit_start": fs,
                "fit_end": fe,
                "valid_start": vs,
                "valid_end": ve,
            },
        )
        checkpoint = (
            _valid_checkpoint(
                checkpoint_path,
                signature,
                {"model": deterministic_model_path},
            )
            if resume
            else None
        )
        if checkpoint is not None:
            model = _load_xgboost_model(deterministic_model_path)
            reused = True
            print(f"[STACK][RESUME] final/{group}")
        else:
            x_train = grouped_x[group].loc[training_index]
            y_train = selected_y.loc[training_index]
            with fit_as_of(as_of_time):
                training_result = _train_one_final_model(
                    x_train=x_train,
                    y_train=y_train,
                    target_name=str(selected_y.name),
                    group=group,
                    plan=plans[group],
                    fit_start=fs,
                    fit_end=fe,
                    valid_start=vs,
                    valid_end=ve,
                    output_dir=group_root / "training",
                )
            model = training_result.model
            _atomic_save_xgboost_model(model, deterministic_model_path)
            checkpoint_payload = {
                "format_version": 1,
                "signature": signature,
                "signature_payload": signature_payload,
                "training_run_id": training_result.audit.get("run_id"),
                "training_artifact_paths": dict(training_result.artifact_paths),
                "model_path": str(deterministic_model_path),
                "artifact_sha256": {"model": _sha256_file(deterministic_model_path)},
                "created_at_utc": pd.Timestamp.utcnow(),
            }
            _atomic_json(checkpoint_path, checkpoint_payload)
            checkpoint = checkpoint_payload
            reused = False
        models[group] = model
        artifact_paths[group] = {
            "checkpoint": str(checkpoint_path),
            "model": str(deterministic_model_path),
            "training_artifact_paths": checkpoint.get("training_artifact_paths", {}),
        }
        group_audits[group] = {
            "reused_checkpoint": reused,
            "iterations": plans[group]["iterations"],
            "factor_count": len(group_feature_names[group]),
            "signature": signature,
            "model_sha256": _sha256_file(deterministic_model_path),
        }

    audit = {
        "module_version": __version__,
        "contract_scope": "one_caller_supplied_contract",
        "target": str(selected_y.name),
        "group_order": list(EXPECTED_GROUPS),
        "fit_start": fs,
        "fit_end": fe,
        "valid_start_used_as_training": vs,
        "valid_end_used_as_training": ve,
        "training_stage": "final",
        "selection_stage_executed": False,
        "early_stopping_executed": False,
        "final_training_rows": len(training_index),
        "as_of_time": pd.Timestamp(as_of_time),
        "max_training_label_end": str(label_ends[train_mask].max()),
        "plans_frozen_at_caller_declared": pd.Timestamp(plans_frozen_at),
        "temporal_audit": temporal_audit,
        "final_training_index_sha256": _index_fingerprint(training_index),
        "final_training_target_sha256": _series_fingerprint(training_target),
        "group_feature_names": {
            group: list(group_feature_names[group]) for group in EXPECTED_GROUPS
        },
        "group_audits": group_audits,
        "test_was_read": None,
        "elapsed_seconds": time.perf_counter() - started,
        "versions": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "xgboost": _package_version("xgboost"),
            "scikit_learn": _package_version("scikit-learn"),
        },
    }
    audit_path = output_root / "final_base_models_audit.json"
    _atomic_json(audit_path, audit)
    artifact_paths["audit"] = str(audit_path)
    print(
        f"[STACK][FINAL BASE COMPLETE] models=12 rows={len(training_index):,} "
        f"elapsed={audit['elapsed_seconds']:.1f}s"
    )
    return FinalBaseModels(
        models=models,
        group_order=EXPECTED_GROUPS,
        group_feature_names=group_feature_names,
        target_name=str(selected_y.name),
        artifact_paths=artifact_paths,
        audit=audit,
    )


def _period_metrics(
    predictions: pd.DataFrame,
    prediction_columns: Sequence[str],
    period: str,
) -> pd.DataFrame:
    datetimes = pd.DatetimeIndex(predictions.index.get_level_values("Datetime"))
    if period == "month":
        keys = datetimes.to_period("M").astype(str)
    elif period == "day":
        keys = datetimes.normalize().strftime("%Y-%m-%d")
    else:
        raise ValueError("period must be month or day")
    records: list[dict[str, Any]] = []
    key_series = pd.Series(keys, index=predictions.index)
    for period_key in pd.unique(keys):
        mask = key_series == period_key
        actual = predictions.loc[mask, "actual"]
        for column in prediction_columns:
            record = dict(_metric_record(predictions.loc[mask, column], actual))
            record.update({"period": str(period_key), "model": column})
            records.append(record)
    result = pd.DataFrame(records)
    if result.empty:
        return result
    return result.set_index(["period", "model"]).sort_index()


@temporal_operation
def evaluate_final_ridge_stack(
    final_base: FinalBaseModels,
    ridge_meta: RidgeMetaModel,
    grouped_x_test: Mapping[str, pd.DataFrame],
    y_test: Target,
    target: Optional[str],
    *,
    test_start: Any,
    test_end: Any,
    sampling_times: Optional[Sequence[str]] = None,
    output_dir: Union[str, os.PathLike[str]] = "runs/ridge_stacking",
) -> StackEvaluationResult:
    """Predict/evaluate untouched Test after all 13 training models are frozen."""

    started = time.perf_counter()
    if not isinstance(final_base, FinalBaseModels):
        raise TypeError("final_base must be returned by fit_final_base_models")
    if not isinstance(ridge_meta, RidgeMetaModel):
        raise TypeError("ridge_meta must be returned by fit_ridge_meta_model")
    if final_base.group_order != EXPECTED_GROUPS or ridge_meta.group_order != EXPECTED_GROUPS:
        raise ValueError("base and Ridge group orders differ from the frozen schema")
    if final_base.target_name != ridge_meta.target_name:
        raise ValueError("base and Ridge targets differ")
    selected_y, reference_index, test_feature_names = _validate_grouped_data(
        grouped_x_test, y_test, target
    )
    sampling_counts = _validate_sampling_times(reference_index, sampling_times)
    if str(selected_y.name) != final_base.target_name:
        raise ValueError("Test target differs from the frozen training target")
    for group in EXPECTED_GROUPS:
        if tuple(test_feature_names[group]) != tuple(final_base.group_feature_names[group]):
            raise ValueError(f"Test factor names/order differ for {group}")

    ts, te = pd.Timestamp(test_start), pd.Timestamp(test_end)
    datetimes = pd.DatetimeIndex(reference_index.get_level_values("Datetime"))
    if datetimes.min() < ts or datetimes.max() > te:
        raise ValueError(
            f"Test input must be restricted to {ts} -> {te}; "
            f"actual={datetimes.min()} -> {datetimes.max()}"
        )
    temporal_metadata_for(reference_index)
    final_training_end = max(pd.Timestamp(final_base.audit["valid_end_used_as_training"]),
                             pd.Timestamp(final_base.audit["max_training_label_end"]),
                             pd.Timestamp(ridge_meta.audit["meta_fit_max_label_end"]))
    if datetimes.min() < pd.Timestamp(final_base.audit["as_of_time"]):
        raise ValueError("Test begins before the declared final fit time")
    if datetimes.min() <= final_training_end:
        raise RuntimeError("Test begins before the final base-model training end")

    base_predictions = pd.DataFrame(index=reference_index)
    for group in EXPECTED_GROUPS:
        values = _predict_xgboost(
            final_base.models[group],
            grouped_x_test[group],
            final_base.group_feature_names[group],
        )
        base_predictions[f"pred_{group}"] = values
    if not np.isfinite(base_predictions.to_numpy(np.float64)).all():
        raise RuntimeError("Test base prediction table contains non-finite values")

    scaled = ridge_meta.scaler.transform(base_predictions.to_numpy(np.float64))
    ridge_prediction = np.asarray(ridge_meta.ridge.predict(scaled), dtype=np.float64)
    equal_prediction = base_predictions.mean(axis=1).to_numpy(np.float64)
    if not np.isfinite(ridge_prediction).all() or not np.isfinite(equal_prediction).all():
        raise RuntimeError("final combined prediction contains non-finite values")

    predictions = base_predictions.copy()
    predictions["equal_weight_prediction"] = equal_prediction
    predictions["ridge_prediction"] = ridge_prediction
    predictions["actual"] = selected_y
    finite_target = np.isfinite(predictions["actual"].to_numpy(np.float64, na_value=np.nan))
    if not finite_target.any():
        raise ValueError("Test contains no finite target rows")
    dropped_nonfinite_targets = int((~finite_target).sum())
    predictions = predictions.loc[finite_target]
    prediction_columns = [
        *(f"pred_{group}" for group in EXPECTED_GROUPS),
        "equal_weight_prediction",
        "ridge_prediction",
    ]
    overall_records = []
    for column in prediction_columns:
        record = dict(_metric_record(predictions[column], predictions["actual"]))
        record["model"] = column
        overall_records.append(record)
    overall_metrics = pd.DataFrame(overall_records).set_index("model")
    monthly_metrics = _period_metrics(predictions, prediction_columns, "month")
    daily_metrics = _period_metrics(predictions, prediction_columns, "day")

    # Attach the same stability summaries used throughout the project. Daily
    # IC uses every available row in that Date; incomplete eight-point days are
    # retained and their row_count remains visible in daily_metrics.
    for column in prediction_columns:
        monthly_one = monthly_metrics.xs(column, level="model")
        daily_one = daily_metrics.xs(column, level="model")
        summaries: dict[str, Any] = {}
        summaries.update(
            _distribution_summary(monthly_one["pearson_ic"], "monthly_pearson_ic")
        )
        summaries.update(
            _distribution_summary(monthly_one["spearman_ic"], "monthly_spearman_ic")
        )
        summaries.update(
            _distribution_summary(daily_one["pearson_ic"], "daily_pearson_ic")
        )
        summaries.update(
            _distribution_summary(daily_one["spearman_ic"], "daily_spearman_ic")
        )
        for name, value in summaries.items():
            overall_metrics.loc[column, name] = value
    overall_metrics["pooled_pearson_ic"] = overall_metrics["pearson_ic"]
    overall_metrics["pooled_spearman_ic"] = overall_metrics["spearman_ic"]

    ridge_monthly = monthly_metrics.xs("ridge_prediction", level="model")
    equal_monthly = monthly_metrics.xs("equal_weight_prediction", level="model")
    monthly_comparison = pd.DataFrame(index=ridge_monthly.index)
    for metric in ("pearson_ic", "spearman_ic", "rmse", "mae"):
        monthly_comparison[f"ridge_{metric}"] = ridge_monthly[metric]
        monthly_comparison[f"equal_weight_{metric}"] = equal_monthly[metric]
        monthly_comparison[f"ridge_minus_equal_{metric}"] = (
            ridge_monthly[metric] - equal_monthly[metric]
        )

    output_root = Path(output_dir).expanduser().resolve() / "test_evaluation"
    predictions_path = output_root / "test_predictions.parquet"
    overall_path = output_root / "overall_metrics.csv"
    monthly_path = output_root / "monthly_metrics.csv"
    daily_path = output_root / "daily_metrics.csv"
    comparison_path = output_root / "ridge_vs_equal_monthly.csv"
    _atomic_parquet(predictions, predictions_path)
    _atomic_csv(overall_metrics, overall_path, index=True)
    _atomic_csv(monthly_metrics, monthly_path, index=True)
    _atomic_csv(daily_metrics, daily_path, index=True)
    _atomic_csv(monthly_comparison, comparison_path, index=True)

    audit = {
        "module_version": __version__,
        "contract_scope": "one_caller_supplied_contract",
        "target": str(selected_y.name),
        "test_start_configured": ts,
        "test_end_configured": te,
        "test_start_actual": datetimes.min(),
        "test_end_actual": datetimes.max(),
        "test_rows_before_target_filter": len(reference_index),
        "test_rows_evaluated": len(predictions),
        "dropped_nonfinite_test_targets": dropped_nonfinite_targets,
        "test_index_sha256": _index_fingerprint(reference_index),
        "evaluated_index_sha256": _index_fingerprint(predictions.index),
        "group_order": list(EXPECTED_GROUPS),
        "sampling_times": list(sampling_counts),
        "sampling_time_counts_test": sampling_counts,
        "strict_complete_grid": False,
        "final_base_training_end": final_training_end,
        "final_base_audit_sha256": _sha256_json(final_base.audit),
        "ridge_meta_audit_sha256": _sha256_json(ridge_meta.audit),
        "standard_scaler_reused_without_refit": True,
        "ridge_reused_without_refit": True,
        "test_used_for_training": False,
        "test_used_for_early_stopping": False,
        "test_used_for_parameter_selection": False,
        "test_was_read": True,
        "purge_check_executed": True,
        "daily_ic_incomplete_days_retained": True,
        "elapsed_seconds": time.perf_counter() - started,
        "versions": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "xgboost": _package_version("xgboost"),
            "scikit_learn": _package_version("scikit-learn"),
        },
    }
    audit_path = output_root / "test_audit.json"
    artifact_hashes = {
        "predictions": _sha256_file(predictions_path),
        "overall_metrics": _sha256_file(overall_path),
        "monthly_metrics": _sha256_file(monthly_path),
        "daily_metrics": _sha256_file(daily_path),
        "monthly_comparison": _sha256_file(comparison_path),
    }
    audit["artifact_sha256"] = artifact_hashes
    _atomic_json(audit_path, audit)
    artifact_paths = {
        "predictions": str(predictions_path),
        "overall_metrics": str(overall_path),
        "monthly_metrics": str(monthly_path),
        "daily_metrics": str(daily_path),
        "monthly_comparison": str(comparison_path),
        "audit": str(audit_path),
    }
    print(
        "[STACK][TEST COMPLETE] "
        f"rows={len(predictions):,} "
        f"ridge_pearson={overall_metrics.loc['ridge_prediction', 'pearson_ic']:.6g} "
        f"ridge_spearman={overall_metrics.loc['ridge_prediction', 'spearman_ic']:.6g}"
    )
    return StackEvaluationResult(
        predictions=predictions,
        overall_metrics=overall_metrics,
        monthly_metrics=monthly_metrics,
        daily_metrics=daily_metrics,
        monthly_comparison=monthly_comparison,
        artifact_paths=artifact_paths,
        audit=audit,
    )
