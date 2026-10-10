"""Hierarchical monthly walk-forward residual research.

G01 predicts the target; G02--G12 predict residuals from past-only G01 OOF.
Conditional groups additionally consume the current G01 prediction. All date
boundaries and plan-freeze times are explicit. Legacy experiments influenced
model design, so neither historical results nor this implementation establish
an independent holdout result. Monthly tests admit only realised past labels.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import tempfile
import time
from typing import Any, Callable, Mapping, Optional, Sequence, Union

import numpy as np
import pandas as pd
from .factor_runtime import (temporal_operation, temporal_metadata_for, fit_as_of,
                             require_plan_frozen_before, frame_fingerprint, CPU_THREADS, validate_preprocessing_boundary)


__version__ = "1.1.0"

EXPECTED_INDEX_NAMES = ("Date", "Contract", "Datetime")
EXPECTED_GROUPS = tuple(f"G{number:02d}" for number in range(1, 13))
CORE_GROUP = "G01"
RESIDUAL_GROUPS = tuple(group for group in EXPECTED_GROUPS if group != CORE_GROUP)
SLOW_GROUPS = ("G03", "G04", "G06", "G10", "G12")
INTRADAY_GROUPS = ("G02", "G05", "G09", "G11")
CONDITIONAL_GROUPS = ("G07", "G08")



Target = Union[pd.Series, pd.DataFrame]


@dataclass(frozen=True)
class MonthlyBlock:
    block_id: str
    start: pd.Timestamp
    end: pd.Timestamp


@dataclass(frozen=True)
class ModelFit:
    model: Any
    audit: Mapping[str, Any]
    history: pd.DataFrame


@dataclass(frozen=True)
class SelectionFit:
    history: pd.DataFrame
    audit: Mapping[str, Any]


@dataclass(frozen=True)
class CoreOOFResult:
    predictions: pd.DataFrame
    group_feature_names: Mapping[str, tuple[str, ...]]
    audit: Mapping[str, Any]
    artifact_paths: Mapping[str, Any]


@dataclass(frozen=True)
class ResidualSelectionResult:
    histories: Mapping[str, pd.DataFrame]
    summary: pd.DataFrame
    audit: Mapping[str, Any]
    artifact_paths: Mapping[str, Any]


@dataclass(frozen=True)
class ResidualPromotionResult:
    selected_groups: tuple[str, ...]
    selection_mode: str
    predictions: pd.DataFrame
    overall_metrics: pd.DataFrame
    monthly_metrics: pd.DataFrame
    group_decisions: pd.DataFrame
    audit: Mapping[str, Any]
    artifact_paths: Mapping[str, Any]


@dataclass(frozen=True)
class ResearchTestResult:
    predictions: pd.DataFrame
    overall_metrics: pd.DataFrame
    monthly_metrics: pd.DataFrame
    daily_metrics: pd.DataFrame
    audit: Mapping[str, Any]
    artifact_paths: Mapping[str, Any]


FinalFitFn = Callable[..., ModelFit]
SelectionFitFn = Callable[..., SelectionFit]
PredictFn = Callable[[Any, pd.DataFrame, Sequence[str]], np.ndarray]
FeatureLoader = Callable[[pd.Timestamp, pd.Timestamp], Mapping[str, pd.DataFrame]]
TargetLoader = Callable[[pd.Timestamp, pd.Timestamp], Target]


__all__ = [
    "EXPECTED_GROUPS",
    "CORE_GROUP",
    "RESIDUAL_GROUPS",
    "SLOW_GROUPS",
    "INTRADAY_GROUPS",
    "CONDITIONAL_GROUPS",
    "MonthlyBlock",
    "ModelFit",
    "SelectionFit",
    "CoreOOFResult",
    "ResidualSelectionResult",
    "ResidualPromotionResult",
    "ResearchTestResult",
    "make_monthly_blocks",
    "validate_final_xgb_plan",
    "validate_residual_selection_plans",
    "generate_monthly_core_oof",
    "run_residual_parameter_selection",
    "run_monthly_residual_promotion",
    "run_research_walk_forward_test",
]


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
    if isinstance(value, pd.DataFrame):
        return _jsonable(value.to_dict(orient="split"))
    return value


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        _jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _index_fingerprint(index: pd.MultiIndex) -> str:
    values = pd.util.hash_pandas_object(index, index=True).to_numpy(np.uint64)
    return sha256(values.tobytes()).hexdigest()


def _series_fingerprint(series: pd.Series) -> str:
    values = pd.util.hash_pandas_object(series, index=True).to_numpy(np.uint64)
    return sha256(values.tobytes()).hexdigest()


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(_jsonable(payload), handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _atomic_pickle(obj: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    os.close(descriptor)
    temporary_path = Path(temporary)
    try:
        pd.to_pickle(obj, temporary_path)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _atomic_csv(frame: pd.DataFrame, path: Path, *, index: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    os.close(descriptor)
    temporary_path = Path(temporary)
    try:
        frame.to_csv(temporary_path, index=index)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _select_target(y: Target, target: Optional[str]) -> pd.Series:
    if isinstance(y, pd.Series):
        if target is not None and y.name is not None and str(y.name) != str(target):
            raise ValueError(f"target={target!r} conflicts with y.name={y.name!r}")
        return y.rename(target or y.name or "target")
    if not isinstance(y, pd.DataFrame):
        raise TypeError("y must be a pandas Series or DataFrame")
    if target is None:
        if len(y.columns) != 1:
            raise ValueError("target is required when y has multiple columns")
        target = str(y.columns[0])
    if target not in y.columns:
        raise KeyError(f"target {target!r} is absent from y")
    return y[target]


def _validate_index(obj: Union[pd.Series, pd.DataFrame], name: str) -> None:
    if not isinstance(obj.index, pd.MultiIndex):
        raise TypeError(f"{name}.index must be a MultiIndex")
    if tuple(obj.index.names) != EXPECTED_INDEX_NAMES:
        raise ValueError(
            f"{name} index names must be {EXPECTED_INDEX_NAMES}, got {obj.index.names}"
        )
    if not obj.index.is_unique:
        raise ValueError(f"{name} contains duplicate Date+Contract+Datetime keys")
    datetimes = pd.DatetimeIndex(obj.index.get_level_values("Datetime"))
    if datetimes.isna().any():
        raise ValueError(f"{name} Datetime contains NaT")
    if not datetimes.is_monotonic_increasing:
        raise ValueError(f"{name} must be chronological; silent sorting is forbidden")


def _validate_grouped_data(
    grouped_x: Mapping[str, pd.DataFrame],
    y: Target,
    target: Optional[str],
    *,
    require_all_groups: bool = True,
) -> tuple[pd.Series, pd.MultiIndex, dict[str, tuple[str, ...]]]:
    if not isinstance(grouped_x, Mapping):
        raise TypeError("grouped_x must be a mapping")
    expected = EXPECTED_GROUPS if require_all_groups else tuple(grouped_x.keys())
    if require_all_groups and tuple(grouped_x.keys()) != EXPECTED_GROUPS:
        raise ValueError(f"group order must be {EXPECTED_GROUPS}")
    selected_y = _select_target(y, target)
    _validate_index(selected_y, "y")
    reference = selected_y.index
    features: dict[str, tuple[str, ...]] = {}
    for group in expected:
        if group not in grouped_x:
            raise KeyError(f"missing group {group}")
        frame = grouped_x[group]
        if not isinstance(frame, pd.DataFrame):
            raise TypeError(f"{group} must be a DataFrame")
        _validate_index(frame, group)
        if not frame.index.equals(reference):
            raise ValueError(f"{group} and y indices/order differ")
        names = tuple(map(str, frame.columns))
        if not names or len(names) != len(set(names)):
            raise ValueError(f"{group} has empty or duplicate feature names")
        if str(selected_y.name) in names:
            raise RuntimeError(f"label leakage: {selected_y.name!r} appears in {group}")
        features[group] = names
    contracts = reference.get_level_values("Contract").unique().tolist()
    if len(contracts) != 1:
        raise ValueError(f"this workflow requires one contract, got {contracts}")
    return selected_y, reference, features


def _validate_prediction_groups(
    grouped_x: Mapping[str, pd.DataFrame], required_groups: Sequence[str]
) -> tuple[pd.MultiIndex, dict[str, tuple[str, ...]]]:
    if not required_groups:
        raise ValueError("required_groups cannot be empty")
    reference: Optional[pd.MultiIndex] = None
    names: dict[str, tuple[str, ...]] = {}
    for group in required_groups:
        if group not in grouped_x:
            raise KeyError(f"feature loader did not return {group}")
        frame = grouped_x[group]
        _validate_index(frame, f"test {group}")
        if reference is None:
            reference = frame.index
        elif not frame.index.equals(reference):
            raise ValueError("loaded Test group indices/order differ")
        names[group] = tuple(map(str, frame.columns))
    assert reference is not None
    contracts = reference.get_level_values("Contract").unique().tolist()
    if len(contracts) != 1:
        raise ValueError(f"Test loader must return one contract: {contracts}")
    return reference, names


def _datetimes(index: pd.MultiIndex) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(index.get_level_values("Datetime"))


def _between(datetimes: pd.DatetimeIndex, start: Any, end: Any) -> np.ndarray:
    return np.asarray((datetimes >= pd.Timestamp(start)) & (datetimes <= pd.Timestamp(end)))


def make_monthly_blocks(start: Any, end: Any) -> tuple[MonthlyBlock, ...]:
    """Return chronological month blocks, preserving partial first/last months."""
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    if pd.isna(start_ts) or pd.isna(end_ts) or start_ts > end_ts:
        raise ValueError("monthly block start/end are invalid")
    blocks: list[MonthlyBlock] = []
    cursor = start_ts
    while cursor <= end_ts:
        next_month = (cursor.normalize().replace(day=1) + pd.offsets.MonthBegin(1))
        month_end = next_month - pd.Timedelta(nanoseconds=1)
        block_end = min(end_ts, month_end)
        blocks.append(
            MonthlyBlock(
                block_id=cursor.strftime("%Y-%m"),
                start=cursor,
                end=block_end,
            )
        )
        cursor = next_month
    if not blocks:
        raise RuntimeError("no monthly blocks were generated")
    for previous, current in zip(blocks, blocks[1:]):
        if previous.end >= current.start:
            raise RuntimeError("monthly blocks overlap")
    return tuple(blocks)


def _validate_xgb_params(params: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(params, Mapping):
        raise TypeError(f"{label}.params must be a mapping")
    result = deepcopy(dict(params))
    if result.get("objective") != "reg:squarederror":
        raise ValueError(f"{label} objective must be reg:squarederror")
    if str(result.get("eval_metric", "")).lower() != "rmse":
        raise ValueError(f"{label} eval_metric must be rmse")
    if int(result.get("nthread", -1)) != CPU_THREADS:
        raise ValueError(f"{label} nthread must match FUTURES_RESEARCH_THREADS")
    return result


def validate_final_xgb_plan(plan: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(plan, Mapping):
        raise TypeError(f"{label} plan must be a mapping")
    iterations = plan.get("iterations")
    if not isinstance(iterations, int) or isinstance(iterations, bool) or iterations <= 0:
        raise ValueError(f"{label}.iterations must be a positive hand-entered integer")
    return {
        "iterations": int(iterations),
        "params": _validate_xgb_params(plan.get("params", {}), label),
    }


def validate_residual_selection_plans(
    plans: Mapping[str, Mapping[str, Any]],
    groups: Optional[Sequence[str]] = None,
) -> dict[str, dict[str, Any]]:
    selected_groups = RESIDUAL_GROUPS if groups is None else tuple(groups)
    if not selected_groups:
        raise ValueError("at least one residual group must be selected")
    if len(set(selected_groups)) != len(selected_groups):
        raise ValueError("residual selection groups must be unique")
    invalid_groups = tuple(group for group in selected_groups if group not in RESIDUAL_GROUPS)
    if invalid_groups:
        raise ValueError(f"invalid residual selection groups: {invalid_groups}")
    if tuple(plans.keys()) != selected_groups:
        raise ValueError(f"selection plan order must be {selected_groups}")
    validated: dict[str, dict[str, Any]] = {}
    for group in selected_groups:
        plan = plans[group]
        maximum = plan.get("max_iterations")
        if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum <= 0:
            raise ValueError(f"{group}.max_iterations must be a positive integer")
        validated[group] = {
            "max_iterations": int(maximum),
            "params": _validate_xgb_params(plan.get("params", {}), group),
        }
    return validated


def _validate_residual_final_plans(
    plans: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    if tuple(plans.keys()) != RESIDUAL_GROUPS:
        raise ValueError(f"frozen residual plan order must be {RESIDUAL_GROUPS}")
    return {
        group: validate_final_xgb_plan(plans[group], group)
        for group in RESIDUAL_GROUPS
    }


def _training_api() -> tuple[Callable[..., Any], Callable[..., Any]]:
    """Import the existing training module lazily so audits/tests need no XGBoost."""
    try:
        from .factor_model_training import make_training_config, train_factor_model
    except ImportError as exc:
        raise ImportError(
            "factor_model_training.py is not importable. Add "
            "the installed futures_research package to your environment."
        ) from exc
    return make_training_config, train_factor_model


def _training_config(params: Mapping[str, Any]) -> dict[str, Any]:
    make_training_config, _ = _training_api()
    config = make_training_config("xgboost")
    config["params"] = deepcopy(dict(params))
    config["early_stopping"] = {"enabled": False, "rounds": 1}
    config["live_plot"] = {"enabled": False, "update_every": 1}
    return config


def _guarded_final_fit(fit_fn, *, as_of_time, x, y, **kwargs):
    ends, _ = temporal_metadata_for(x.index)
    validate_preprocessing_boundary(x, as_of_time)
    if not (ends < pd.Timestamp(as_of_time)).all():
        raise ValueError("final fit contains labels unavailable at its prediction cutoff")
    with fit_as_of(as_of_time):
        return fit_fn(x=x, y=y, **kwargs)


def _default_final_fit(
    *,
    x: pd.DataFrame,
    y: pd.Series,
    target: str,
    plan: Mapping[str, Any],
    role: str,
) -> ModelFit:
    """Call the existing Stage-2 API; its two shards are both training data."""
    if len(x) < 2:
        raise ValueError(f"{role} needs at least two training rows")
    _, train_factor_model = _training_api()
    datetimes = _datetimes(x.index)
    distinct = datetimes.unique().sort_values()
    if len(distinct) < 2:
        raise ValueError("final fit needs at least two distinct decision times")
    cut = max(1, min(len(distinct) - 1, int(np.floor(len(distinct) * 0.8))))
    boundary = distinct[cut]
    with tempfile.TemporaryDirectory(prefix="hierarchical_residual_final_") as temporary:
        result = train_factor_model(
            x=x,
            y=y,
            target=target,
            method="xgboost",
            training_stage="final",
            training_iterations=int(plan["iterations"]),
            fit_start=datetimes.min(),
            fit_end=boundary,
            valid_start=boundary,
            valid_end=datetimes.max() + pd.Timedelta(nanoseconds=1),
            config=_training_config(plan["params"]),
            output_dir=temporary,
        )
    source_audit = dict(result.audit)
    source_audit.pop("artifact_paths", None)
    source_audit.pop("artifact_sha256", None)
    audit = {
        "role": role,
        "training_stage": "final",
        "internal_fit_valid_are_both_training_shards": True,
        "source_audit": source_audit,
    }
    return ModelFit(result.model, audit, result.frozen_history.copy())


def _default_selection_fit(
    *,
    x: pd.DataFrame,
    y: pd.Series,
    target: str,
    fit_start: Any,
    fit_end: Any,
    valid_start: Any,
    valid_end: Any,
    plan: Mapping[str, Any],
    role: str,
) -> SelectionFit:
    _, train_factor_model = _training_api()
    result = train_factor_model(
        x=x,
        y=y,
        target=target,
        method="xgboost",
        training_stage="selection",
        training_iterations=int(plan["max_iterations"]),
        fit_start=fit_start,
        fit_end=pd.Timestamp(fit_end) + pd.Timedelta(nanoseconds=1),
        valid_start=valid_start,
        valid_end=pd.Timestamp(valid_end) + pd.Timedelta(nanoseconds=1),
        config=_training_config(plan["params"]),
        output_dir=None,
    )
    return SelectionFit(result.history.copy(), dict(result.audit))


def _default_predict(model: Any, x: pd.DataFrame, feature_names: Sequence[str]) -> np.ndarray:
    if tuple(map(str, x.columns)) != tuple(feature_names):
        raise ValueError("prediction feature names/order differ from training")
    import xgboost as xgb

    values = model.predict(xgb.DMatrix(x, feature_names=list(feature_names)))
    return np.asarray(values, dtype=np.float64).reshape(-1)


def _save_latest_model(model: Any, path: Path) -> Optional[str]:
    """Persist one overwriteable latest model; monthly historical boosters are omitted."""
    if not hasattr(model, "save_model"):
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.stem}.{os.getpid()}.tmp{path.suffix}")
    model.save_model(str(temporary))
    os.replace(temporary, path)
    return str(path)


def _prediction_frame(index: pd.MultiIndex, values: np.ndarray, column: str) -> pd.DataFrame:
    vector = np.asarray(values, dtype=np.float64).reshape(-1)
    if len(vector) != len(index):
        raise RuntimeError(f"{column} prediction length mismatch")
    if not np.isfinite(vector).all():
        raise RuntimeError(f"{column} contains non-finite predictions")
    return pd.DataFrame({column: vector}, index=index)


def _cache_paths(root: Path, stage: str, key: str) -> tuple[Path, Path]:
    directory = root / "checkpoints" / stage
    return directory / f"{key}.pkl", directory / f"{key}.json"


def _load_prediction_cache(
    root: Path, stage: str, key: str, signature: Mapping[str, Any]
) -> Optional[pd.DataFrame]:
    prediction_path, manifest_path = _cache_paths(root, stage, key)
    if not prediction_path.exists() or not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("signature_sha256") != _sha256_json(signature):
            return None
        if manifest.get("prediction_sha256") != _sha256_file(prediction_path):
            return None
        frame = pd.read_pickle(prediction_path)
        if not isinstance(frame, pd.DataFrame):
            return None
        return frame
    except (OSError, ValueError, TypeError, EOFError, json.JSONDecodeError):
        return None


def _save_prediction_cache(
    frame: pd.DataFrame,
    root: Path,
    stage: str,
    key: str,
    signature: Mapping[str, Any],
) -> tuple[str, str]:
    prediction_path, manifest_path = _cache_paths(root, stage, key)
    _atomic_pickle(frame, prediction_path)
    _atomic_json(
        manifest_path,
        {
            "signature": signature,
            "signature_sha256": _sha256_json(signature),
            "prediction_sha256": _sha256_file(prediction_path),
            "created_at_unix": time.time(),
        },
    )
    return str(prediction_path), str(manifest_path)


def _fit_signature(
    *,
    role: str,
    plan: Mapping[str, Any],
    x_train: pd.DataFrame,
    y_train: pd.Series,
    prediction_index: pd.MultiIndex,
    x_prediction: pd.DataFrame,
) -> dict[str, Any]:
    return {
        "module_version": __version__,
        "role": role,
        "plan": plan,
        "feature_names": list(map(str, x_train.columns)),
        "train_index_sha256": _index_fingerprint(x_train.index),
        "train_target_sha256": _series_fingerprint(y_train),
        "train_feature_values_sha256": frame_fingerprint(x_train),
        "prediction_feature_values_sha256": frame_fingerprint(x_prediction),
        "temporal_metadata": temporal_metadata_for(x_train.index)[1],
        "source_sha256": {name: _sha256_file(Path(__file__).with_name(name + ".py"))
                          for name in ("factor_hierarchical_residual", "factor_model_training", "factor_runtime")},
        "prediction_temporal_metadata": temporal_metadata_for(prediction_index)[1] if prediction_index is not None else None,
        "prediction_index_sha256": _index_fingerprint(prediction_index),
        "processing_fingerprint": x_train.attrs.get("processing_fingerprint"),
        "sampling_result_fingerprint": x_train.attrs.get("sampling_result_fingerprint"),
        "target_name": str(y_train.name),
    }


def _residual_features(
    group: str, frame: pd.DataFrame, core_prediction: pd.Series
) -> pd.DataFrame:
    result = frame.copy()
    if group in CONDITIONAL_GROUPS:
        if "pred_G01" in result.columns:
            raise RuntimeError(f"{group} already contains reserved pred_G01")
        if not result.index.equals(core_prediction.index):
            raise ValueError(f"{group} and pred_G01 indices/order differ")
        result["pred_G01"] = core_prediction.to_numpy(np.float64)
    return result


def _common_audit() -> dict[str, Any]:
    return {
        "module": "factor_hierarchical_residual",
        "module_version": __version__,
        "contract_scope": "one_caller_supplied_contract",
        "target_scope": "caller_supplied_label",
        "sampling_rule": "caller_supplied_pre_sampled_rows",
        "z_score_applied": False,
        "xgboost_native_missing_handling": True,
        "g01_coefficient_fixed": 1.0,
        "ridge_used": False,
        "legacy_parameter_and_promotion_validation_reused": True,
        "independent_holdout_validated": False,
        "purge_check_executed": True,
        "research_walk_forward_test": True,
        "legacy_test_informed_model_design": True,
        "versions": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "xgboost": _package_version("xgboost"),
        },
    }


@temporal_operation
def generate_monthly_core_oof(
    grouped_x: Mapping[str, pd.DataFrame],
    y: Target,
    target: str,
    core_plan: Mapping[str, Any],
    *,
    core_train_start: Any,
    oof_start: Any,
    oof_end: Any,
    output_dir: Union[str, os.PathLike[str]] = "runs/hierarchical_residual",
    resume: bool = True,
    final_fit_fn: Optional[FinalFitFn] = None,
    predict_fn: Optional[PredictFn] = None,
    show_progress: bool = True,
    plans_frozen_at: Any,
) -> CoreOOFResult:
    """Create expanding monthly OOF G01 predictions and exact residual labels.

    The first prediction block may be a partial month. Every
    fit uses rows from ``core_train_start`` strictly before its block start.
    """
    selected_y, index, feature_names = _validate_grouped_data(grouped_x, y, target)
    plan = validate_final_xgb_plan(core_plan, CORE_GROUP)
    root = Path(output_dir)
    fit = final_fit_fn or _default_final_fit
    predict = predict_fn or _default_predict
    datetimes = _datetimes(index)
    start, end = pd.Timestamp(oof_start), pd.Timestamp(oof_end)
    require_plan_frozen_before(plans_frozen_at, start)
    label_ends, temporal_audit = temporal_metadata_for(index)
    if pd.Timestamp(core_train_start) >= start:
        raise ValueError("core_train_start must precede oof_start")
    if not isinstance(show_progress, bool):
        raise TypeError("show_progress must be bool")
    if not np.isfinite(selected_y.to_numpy(np.float64)).all():
        raise ValueError("target contains non-finite values; frozen residual identity requires all labels")

    parts: list[pd.DataFrame] = []
    monthly_audits: list[dict[str, Any]] = []
    latest_model_path: Optional[str] = None
    blocks = make_monthly_blocks(start, end)
    run_started = time.perf_counter()
    for block_number, block in enumerate(blocks, start=1):
        block_started = time.perf_counter()
        train_mask = _between(datetimes, core_train_start, block.start - pd.Timedelta(nanoseconds=1)) & np.asarray(label_ends < block.start)
        prediction_mask = _between(datetimes, block.start, block.end)
        if not prediction_mask.any():
            if show_progress:
                print(
                    f"[G01 OOF {block_number:02d}/{len(blocks):02d}] "
                    f"{block.block_id}：无预测样本，跳过",
                    flush=True,
                )
            continue
        if not train_mask.any():
            raise ValueError(f"no G01 training rows before {block.start}")
        x_train = grouped_x[CORE_GROUP].loc[train_mask]
        y_train = selected_y.loc[train_mask]
        x_prediction = grouped_x[CORE_GROUP].loc[prediction_mask]
        train_end_observed = _datetimes(x_train.index)[-1]
        if show_progress:
            print(
                f"[G01 OOF {block_number:02d}/{len(blocks):02d}] {block.block_id} 开始 | "
                f"train_end={train_end_observed} | train_rows={len(x_train):,} | "
                f"predict_rows={len(x_prediction):,}",
                flush=True,
            )
        validate_preprocessing_boundary(x_train, block.start)
        signature = _fit_signature(
            role=f"core_oof:{block.block_id}",
            plan=plan,
            x_train=x_train,
            y_train=y_train,
            prediction_index=x_prediction.index,
            x_prediction=x_prediction,
        )
        temporal_metadata_for(x_prediction.index)
        cached = _load_prediction_cache(root, "core_oof", block.block_id, signature) if resume else None
        cache_hit = cached is not None
        fit_audit: Mapping[str, Any] = {}
        if cached is None:
            fitted = _guarded_final_fit(fit, as_of_time=block.start,
                x=x_train,
                y=y_train,
                target=target,
                plan=plan,
                role=f"core_oof:{block.block_id}",
            )
            prediction = _prediction_frame(
                x_prediction.index,
                predict(fitted.model, x_prediction, tuple(x_train.columns)),
                "pred_G01",
            )
            _save_prediction_cache(prediction, root, "core_oof", block.block_id, signature)
            latest_model_path = _save_latest_model(
                fitted.model, root / "models" / "latest_G01.json"
            ) or latest_model_path
            fit_audit = fitted.audit
        else:
            prediction = cached
            if not prediction.index.equals(x_prediction.index) or list(prediction.columns) != ["pred_G01"]:
                raise RuntimeError(f"invalid cached G01 prediction for {block.block_id}")

        block_seconds = time.perf_counter() - block_started
        total_seconds = time.perf_counter() - run_started
        if show_progress:
            source = "checkpoint" if cache_hit else "重新训练"
            print(
                f"[G01 OOF {block_number:02d}/{len(blocks):02d}] {block.block_id} 完成 | "
                f"source={source} | month={block_seconds:.1f}s | total={total_seconds:.1f}s",
                flush=True,
            )

        actual = selected_y.loc[prediction.index].rename("actual")
        part = prediction.copy()
        part["actual"] = actual.to_numpy(np.float64)
        part["core_residual"] = part["actual"] - part["pred_G01"]
        part["month_id"] = block.block_id
        if not np.array_equal(
            part["core_residual"].to_numpy(),
            (part["actual"] - part["pred_G01"]).to_numpy(),
            equal_nan=True,
        ):
            raise RuntimeError("core residual identity failed")
        parts.append(part)
        monthly_audits.append(
            {
                "block_id": block.block_id,
                "prediction_start": block.start,
                "prediction_end": block.end,
                "train_start": pd.Timestamp(core_train_start),
                "train_end_observed": train_end_observed,
                "train_rows": len(x_train),
                "prediction_rows": len(part),
                "strictly_past_training": bool(_datetimes(x_train.index).max() < block.start),
                "cache_hit": cache_hit,
                "elapsed_seconds": block_seconds,
                "fit_audit": fit_audit,
            }
        )

    if not parts:
        raise ValueError("no G01 OOF rows fall inside the requested interval")
    predictions = pd.concat(parts).sort_index()
    expected_mask = _between(datetimes, start, end)
    expected_index = index[expected_mask]
    if not predictions.index.equals(expected_index):
        raise RuntimeError("G01 OOF output does not exactly cover requested source rows")
    audit = _common_audit()
    audit.update(
        {
            "stage": "monthly_core_oof",
            "plans_frozen_at_caller_declared": pd.Timestamp(plans_frozen_at),
            "temporal_audit": temporal_audit,
            "core_group": CORE_GROUP,
            "core_initial_complete_training_end": start - pd.Timedelta(nanoseconds=1),
            "core_plan": plan,
            "expanding_window": True,
            "monthly_refit_from_scratch": True,
            "monthly_progress_enabled": show_progress,
            "elapsed_seconds": time.perf_counter() - run_started,
            "monthly_blocks": monthly_audits,
            "all_training_strictly_before_prediction": all(
                row["strictly_past_training"] for row in monthly_audits
            ),
            "residual_definition": "actual - monthly_OOF_pred_G01",
        }
    )
    audit_path = root / "audit" / "core_oof_audit.json"
    prediction_path = root / "predictions" / "core_oof.pkl"
    _atomic_pickle(predictions, prediction_path)
    _atomic_json(audit_path, audit)
    return CoreOOFResult(
        predictions=predictions,
        group_feature_names=feature_names,
        audit=audit,
        artifact_paths={
            "predictions": str(prediction_path),
            "audit": str(audit_path),
            "latest_model": latest_model_path,
        },
    )


def _require_core_frame(core_oof: Union[CoreOOFResult, pd.DataFrame]) -> pd.DataFrame:
    if not isinstance(core_oof, CoreOOFResult) or not core_oof.audit.get("purge_check_executed"):
        raise ValueError("core_oof must carry audited CoreOOFResult temporal evidence")
    frame = core_oof.predictions
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("core_oof must be CoreOOFResult or DataFrame")
    required = {"pred_G01", "actual", "core_residual"}
    if not required.issubset(frame.columns):
        raise KeyError(f"core_oof is missing {sorted(required.difference(frame.columns))}")
    _validate_index(frame, "core_oof")
    expected = frame["actual"] - frame["pred_G01"]
    if not np.allclose(frame["core_residual"], expected, rtol=0.0, atol=0.0, equal_nan=True):
        raise RuntimeError("core_oof residual is not exactly actual - pred_G01")
    return frame


def _best_iteration(history: pd.DataFrame, column: str, maximise: bool) -> tuple[Optional[int], float]:
    if column not in history.columns or "iteration" not in history.columns:
        raise KeyError(f"selection history needs iteration and {column}")
    values = pd.to_numeric(history[column], errors="coerce")
    valid = values.notna() & np.isfinite(values)
    if not valid.any():
        return None, float("nan")
    position = values[valid].idxmax() if maximise else values[valid].idxmin()
    return int(history.loc[position, "iteration"]), float(values.loc[position])


@temporal_operation
def run_residual_parameter_selection(
    grouped_x: Mapping[str, pd.DataFrame],
    core_oof: Union[CoreOOFResult, pd.DataFrame],
    selection_plans: Mapping[str, Mapping[str, Any]],
    *,
    target: str = "ret_30m",
    fit_start: Any,
    fit_end: Any,
    valid_start: Any,
    valid_end: Any,
    output_dir: Union[str, os.PathLike[str]] = "runs/hierarchical_residual",
    selection_fit_fn: Optional[SelectionFitFn] = None,
    groups: Optional[Sequence[str]] = None,
    test_start: Any,
) -> ResidualSelectionResult:
    """Produce fixed Stage-1 Train/Validation curves; never choose a round."""
    selected_groups = RESIDUAL_GROUPS if groups is None else tuple(groups)
    plans = validate_residual_selection_plans(selection_plans, selected_groups)
    core = _require_core_frame(core_oof)
    root = Path(output_dir)
    fit = selection_fit_fn or _default_selection_fit
    if pd.Timestamp(fit_end) >= pd.Timestamp(valid_start):
        raise ValueError("Residual Train and Validation overlap")
    if pd.Timestamp(valid_end) >= pd.Timestamp(test_start):
        raise ValueError("parameter selection must end before reserved Test")
    datetimes = _datetimes(core.index)
    mask = _between(datetimes, fit_start, valid_end)
    used_core = core.loc[mask]
    if used_core.empty:
        raise ValueError("core_oof has no rows for residual parameter selection")
    histories: dict[str, pd.DataFrame] = {}
    audits: dict[str, Any] = {}
    summary_rows: list[dict[str, Any]] = []
    paths: dict[str, str] = {}
    for group in selected_groups:
        if group not in grouped_x:
            raise KeyError(f"missing {group}")
        raw = grouped_x[group]
        _validate_index(raw, group)
        if not used_core.index.isin(raw.index).all():
            raise ValueError(f"{group} does not cover all residual selection rows")
        x = raw.loc[used_core.index]
        x = _residual_features(group, x, used_core["pred_G01"])
        y_residual = used_core["core_residual"].rename(target)
        ends, _ = temporal_metadata_for(x.index)
        validate_preprocessing_boundary(x, valid_start)
        dt = _datetimes(x.index)
        keep = np.asarray(((dt <= pd.Timestamp(fit_end)) & (ends < pd.Timestamp(valid_start))) |
                          ((dt >= pd.Timestamp(valid_start)) & (ends <= pd.Timestamp(valid_end))))
        x, y_residual = x.loc[keep], y_residual.loc[keep]
        result = fit(
            x=x,
            y=y_residual,
            target=target,
            fit_start=fit_start,
            fit_end=fit_end,
            valid_start=valid_start,
            valid_end=valid_end,
            plan=plans[group],
            role=f"residual_parameter_selection:{group}",
        )
        history = result.history.copy().reset_index(drop=True)
        rmse_iteration, rmse_value = _best_iteration(history, "valid_rmse", False)
        ic_iteration, ic_value = _best_iteration(history, "valid_pearson_ic", True)
        histories[group] = history
        audits[group] = dict(result.audit)
        path = root / "selection_curves" / f"{group}.csv"
        _atomic_csv(history, path, index=False)
        paths[f"history_{group}"] = str(path)
        summary_rows.append(
            {
                "group": group,
                "role": (
                    "conditional" if group in CONDITIONAL_GROUPS else
                    "slow_supplement" if group in SLOW_GROUPS else "intraday_supplement"
                ),
                "raw_factor_count": grouped_x[group].shape[1],
                "uses_pred_G01_feature": group in CONDITIONAL_GROUPS,
                "max_iterations": plans[group]["max_iterations"],
                "best_valid_rmse_iteration": rmse_iteration,
                "best_valid_rmse": rmse_value,
                "best_valid_pearson_ic_iteration": ic_iteration,
                "best_valid_pearson_ic": ic_value,
                "automatic_iteration_selection": False,
            }
        )
    summary = pd.DataFrame(summary_rows).set_index("group")
    selection_id = "all" if selected_groups == RESIDUAL_GROUPS else "_".join(selected_groups)
    summary_path = root / "selection_curves" / f"selection_summary_{selection_id}.csv"
    _atomic_csv(summary, summary_path)
    paths["summary"] = str(summary_path)
    audit = _common_audit()
    audit.update(
        {
            "stage": "residual_parameter_selection",
            "residual_train": [pd.Timestamp(fit_start), pd.Timestamp(fit_end)],
            "residual_parameter_validation": [pd.Timestamp(valid_start), pd.Timestamp(valid_end)],
            "target_definition": "ret_30m - monthly_OOF_pred_G01",
            "fixed_single_stage1_model_per_group": True,
            "automatic_iteration_selection": False,
            "manual_hard_pause_required_after_curves": True,
            "test_rows_read": False,
            "selected_groups": selected_groups,
            "plans": plans,
            "group_audits": audits,
        }
    )
    audit_path = root / "audit" / f"residual_parameter_selection_audit_{selection_id}.json"
    _atomic_json(audit_path, audit)
    paths["audit"] = str(audit_path)
    return ResidualSelectionResult(histories, summary, audit, paths)


def _correlation(left: pd.Series, right: pd.Series, method: str) -> float:
    pair = pd.concat([left, right], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    if len(pair) < 2 or pair.iloc[:, 0].nunique() < 2 or pair.iloc[:, 1].nunique() < 2:
        return float("nan")
    return float(pair.iloc[:, 0].corr(pair.iloc[:, 1], method=method))


def _metric_row(prediction: pd.Series, actual: pd.Series) -> dict[str, Any]:
    pair = pd.concat([prediction.rename("prediction"), actual.rename("actual")], axis=1)
    pair = pair.replace([np.inf, -np.inf], np.nan).dropna()
    error = pair["prediction"] - pair["actual"]
    return {
        "row_count": int(len(pair)),
        "pooled_pearson_ic": _correlation(pair["prediction"], pair["actual"], "pearson"),
        "pooled_spearman_ic": _correlation(pair["prediction"], pair["actual"], "spearman"),
        "rmse": float(np.sqrt(np.mean(np.square(error)))) if len(pair) else float("nan"),
        "mae": float(np.mean(np.abs(error))) if len(pair) else float("nan"),
    }


def _period_metrics(
    frame: pd.DataFrame,
    prediction_columns: Sequence[str],
    frequency: str,
) -> pd.DataFrame:
    datetimes = _datetimes(frame.index)
    period = datetimes.to_period(frequency).astype(str)
    rows: list[dict[str, Any]] = []
    for model in prediction_columns:
        temporary = pd.DataFrame(
            {"prediction": frame[model].to_numpy(), "actual": frame["actual"].to_numpy(), "period": period},
            index=frame.index,
        )
        for period_id, part in temporary.groupby("period", sort=True):
            row = _metric_row(part["prediction"], part["actual"])
            rows.append({"model": model, "period": period_id, **row})
    if not rows:
        return pd.DataFrame(columns=["model", "period"]).set_index(["model", "period"])
    return pd.DataFrame(rows).set_index(["model", "period"])


def _evaluate_columns(
    frame: pd.DataFrame, prediction_columns: Sequence[str]
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    overall = pd.DataFrame(
        [_metric_row(frame[column], frame["actual"]) for column in prediction_columns],
        index=pd.Index(prediction_columns, name="model"),
    )
    monthly = _period_metrics(frame, prediction_columns, "M")
    daily = _period_metrics(frame, prediction_columns, "D")
    for model in prediction_columns:
        if model not in monthly.index.get_level_values("model"):
            continue
        part = monthly.xs(model, level="model")
        pearson = part["pooled_pearson_ic"].dropna()
        spearman = part["pooled_spearman_ic"].dropna()
        overall.loc[model, "monthly_pearson_ic_count"] = len(pearson)
        overall.loc[model, "mean_monthly_pearson_ic"] = pearson.mean()
        overall.loc[model, "monthly_pearson_icir"] = pearson.mean() / pearson.std(ddof=1) if len(pearson) > 1 and pearson.std(ddof=1) > 0 else np.nan
        overall.loc[model, "positive_monthly_pearson_ic_ratio"] = (pearson > 0).mean() if len(pearson) else np.nan
        overall.loc[model, "monthly_spearman_ic_count"] = len(spearman)
        overall.loc[model, "mean_monthly_spearman_ic"] = spearman.mean()
        overall.loc[model, "monthly_spearman_icir"] = spearman.mean() / spearman.std(ddof=1) if len(spearman) > 1 and spearman.std(ddof=1) > 0 else np.nan
        overall.loc[model, "positive_monthly_spearman_ic_ratio"] = (spearman > 0).mean() if len(spearman) else np.nan
    return overall, monthly, daily


def _gate_row(
    model: str,
    core_model: str,
    overall: pd.DataFrame,
    monthly: pd.DataFrame,
    *,
    minimum_positive_month_delta_ratio: float = 0.5,
) -> dict[str, Any]:
    model_month = monthly.xs(model, level="model")["pooled_pearson_ic"]
    core_month = monthly.xs(core_model, level="model")["pooled_pearson_ic"]
    paired = pd.concat([model_month.rename("model"), core_month.rename("core")], axis=1).dropna()
    delta = paired["model"] - paired["core"]
    ratio = float((delta > 0).mean()) if len(delta) else float("nan")
    pearson_delta = float(overall.loc[model, "pooled_pearson_ic"] - overall.loc[core_model, "pooled_pearson_ic"])
    spearman_delta = float(overall.loc[model, "pooled_spearman_ic"] - overall.loc[core_model, "pooled_spearman_ic"])
    monthly_delta = float(overall.loc[model, "mean_monthly_pearson_ic"] - overall.loc[core_model, "mean_monthly_pearson_ic"])
    gates = {
        "pooled_pearson_improved": bool(pearson_delta > 0),
        "pooled_spearman_improved": bool(spearman_delta > 0),
        "mean_monthly_pearson_improved": bool(monthly_delta > 0),
        "positive_monthly_pearson_delta_ratio_passed": bool(
            np.isfinite(ratio) and ratio >= minimum_positive_month_delta_ratio
        ),
    }
    return {
        "candidate": model,
        "pooled_pearson_delta": pearson_delta,
        "pooled_spearman_delta": spearman_delta,
        "mean_monthly_pearson_delta": monthly_delta,
        "positive_monthly_pearson_delta_count": int((delta > 0).sum()),
        "comparable_month_count": int(len(delta)),
        "positive_monthly_pearson_delta_ratio": ratio,
        **gates,
        "eligible": all(gates.values()),
    }


@temporal_operation
def run_monthly_residual_promotion(
    grouped_x: Mapping[str, pd.DataFrame],
    core_oof: Union[CoreOOFResult, pd.DataFrame],
    frozen_plans: Mapping[str, Mapping[str, Any]],
    *,
    target: str = "ret_30m",
    residual_train_start: Any,
    promotion_start: Any,
    promotion_end: Any,
    frozen_config_confirmed: bool = False,
    plans_frozen_at: Any,
    test_start: Any,
    output_dir: Union[str, os.PathLike[str]] = "runs/hierarchical_residual",
    resume: bool = True,
    final_fit_fn: Optional[FinalFitFn] = None,
    predict_fn: Optional[PredictFn] = None,
) -> ResidualPromotionResult:
    """Run monthly OOF and apply the declared four-part promotion gate."""
    if not frozen_config_confirmed:
        raise RuntimeError(
            "hard pause: inspect Stage-1 curves, hand-fill FROZEN_RESIDUAL_XGB_PLANS, "
            "then set frozen_config_confirmed=True"
        )
    plans = _validate_residual_final_plans(frozen_plans)
    require_plan_frozen_before(plans_frozen_at, promotion_start)
    if pd.Timestamp(promotion_end) >= pd.Timestamp(test_start):
        raise ValueError("promotion must end before reserved Test")
    core = _require_core_frame(core_oof)
    root = Path(output_dir)
    fit = final_fit_fn or _default_final_fit
    predict = predict_fn or _default_predict
    core_dt = _datetimes(core.index)
    core_label_ends, _ = temporal_metadata_for(core.index)
    prediction_parts: list[pd.DataFrame] = []
    monthly_audits: list[dict[str, Any]] = []
    latest_models: dict[str, Optional[str]] = {}
    for block in make_monthly_blocks(promotion_start, promotion_end):
        prediction_mask = _between(core_dt, block.start, block.end)
        if not prediction_mask.any():
            continue
        current_core = core.loc[prediction_mask]
        part = current_core[["actual", "pred_G01"]].copy()
        block_audit: dict[str, Any] = {"block_id": block.block_id, "groups": {}}
        train_mask = _between(
            core_dt, residual_train_start, block.start - pd.Timedelta(nanoseconds=1)
        )
        train_mask &= np.asarray(core_label_ends < block.start)
        residual_history = core.loc[train_mask]
        if residual_history.empty:
            raise ValueError(f"no residual training rows before {block.start}")
        for group in RESIDUAL_GROUPS:
            raw = grouped_x[group]
            if not core.index.isin(raw.index).all():
                raise ValueError(f"{group} does not cover core_oof index")
            x_train = _residual_features(
                group, raw.loc[residual_history.index], residual_history["pred_G01"]
            )
            x_prediction = _residual_features(
                group, raw.loc[current_core.index], current_core["pred_G01"]
            )
            y_train = residual_history["core_residual"].rename(target)
            validate_preprocessing_boundary(x_train, block.start)
            signature = _fit_signature(
                role=f"residual_promotion:{group}:{block.block_id}",
                plan=plans[group],
                x_train=x_train,
                y_train=y_train,
                prediction_index=x_prediction.index,
                x_prediction=x_prediction,
            )
            key = f"{block.block_id}_{group}"
            cached = _load_prediction_cache(root, "residual_promotion", key, signature) if resume else None
            cache_hit = cached is not None
            if cached is None:
                fitted = _guarded_final_fit(fit, as_of_time=block.start,
                    x=x_train,
                    y=y_train,
                    target=target,
                    plan=plans[group],
                    role=f"residual_promotion:{group}:{block.block_id}",
                )
                prediction = _prediction_frame(
                    x_prediction.index,
                    predict(fitted.model, x_prediction, tuple(x_train.columns)),
                    f"residual_{group}",
                )
                _save_prediction_cache(prediction, root, "residual_promotion", key, signature)
                latest_models[group] = _save_latest_model(
                    fitted.model, root / "models" / f"latest_residual_{group}.json"
                ) or latest_models.get(group)
            else:
                prediction = cached
                if not prediction.index.equals(x_prediction.index):
                    raise RuntimeError(f"invalid cached residual prediction: {key}")
            part[f"residual_{group}"] = prediction.iloc[:, 0].to_numpy(np.float64)
            part[f"core_plus_{group}"] = part["pred_G01"] + part[f"residual_{group}"]
            block_audit["groups"][group] = {
                "train_end_observed": _datetimes(x_train.index)[-1],
                "prediction_start": block.start,
                "strictly_past_training": bool(_datetimes(x_train.index).max() < block.start),
                "train_rows": len(x_train),
                "prediction_rows": len(x_prediction),
                "uses_pred_G01_feature": group in CONDITIONAL_GROUPS,
                "cache_hit": cache_hit,
            }
        prediction_parts.append(part)
        monthly_audits.append(block_audit)
    if not prediction_parts:
        raise ValueError("no promotion rows")
    predictions = pd.concat(prediction_parts).sort_index()
    candidate_columns = ["pred_G01"] + [f"core_plus_{group}" for group in RESIDUAL_GROUPS]
    overall, monthly, _ = _evaluate_columns(predictions, candidate_columns)
    decisions = [
        {"group": group, **_gate_row(f"core_plus_{group}", "pred_G01", overall, monthly)}
        for group in RESIDUAL_GROUPS
    ]
    decision_frame = pd.DataFrame(decisions).set_index("group")
    eligible = tuple(decision_frame.index[decision_frame["eligible"]].tolist())
    selection_mode: str
    ensemble_gate: Optional[dict[str, Any]] = None
    if eligible:
        predictions["eligible_residual_mean"] = predictions[
            [f"residual_{group}" for group in eligible]
        ].mean(axis=1)
        predictions["eligible_equal_weight"] = predictions["pred_G01"] + predictions["eligible_residual_mean"]
        expanded_columns = candidate_columns + ["eligible_equal_weight"]
        overall, monthly, _ = _evaluate_columns(predictions, expanded_columns)
        ensemble_gate = _gate_row("eligible_equal_weight", "pred_G01", overall, monthly)
        if ensemble_gate["eligible"]:
            selected_groups = eligible
            predictions["final_prediction"] = predictions["eligible_equal_weight"]
            selection_mode = "eligible_residual_equal_weight"
        else:
            best = decision_frame.loc[list(eligible), "pooled_pearson_delta"].idxmax()
            selected_groups = (str(best),)
            predictions["final_prediction"] = predictions[f"core_plus_{best}"]
            selection_mode = "fallback_best_eligible_single_group"
    else:
        selected_groups = ()
        predictions["final_prediction"] = predictions["pred_G01"]
        selection_mode = "fallback_pure_G01_no_eligible_group"
    final_columns = list(dict.fromkeys(candidate_columns + ["final_prediction"]))
    overall, monthly, _ = _evaluate_columns(predictions, final_columns)
    audit = _common_audit()
    audit.update(
        {
            "stage": "monthly_residual_promotion",
            "residual_train_start": pd.Timestamp(residual_train_start),
            "promotion_validation": [pd.Timestamp(promotion_start), pd.Timestamp(promotion_end)],
            "frozen_config_confirmed": True,
            "plans_frozen_at_caller_declared": pd.Timestamp(plans_frozen_at),
            "plans": plans,
            "promotion_gates": [
                "pooled Pearson improves", "pooled Spearman improves",
                "mean monthly Pearson improves", "positive monthly Pearson delta ratio >= 0.5",
            ],
            "eligible_groups": eligible,
            "selected_groups": selected_groups,
            "selection_mode": selection_mode,
            "ensemble_gate": ensemble_gate,
            "monthly_blocks": monthly_audits,
        }
    )
    prediction_path = root / "predictions" / "residual_promotion.pkl"
    overall_path = root / "metrics" / "residual_promotion_overall.csv"
    monthly_path = root / "metrics" / "residual_promotion_monthly.csv"
    decision_path = root / "metrics" / "residual_promotion_decisions.csv"
    audit_path = root / "audit" / "residual_promotion_audit.json"
    _atomic_pickle(predictions, prediction_path)
    _atomic_csv(overall, overall_path)
    _atomic_csv(monthly, monthly_path)
    _atomic_csv(decision_frame, decision_path)
    _atomic_json(audit_path, audit)
    return ResidualPromotionResult(
        selected_groups, selection_mode, predictions, overall, monthly,
        decision_frame, audit,
        {
            "predictions": str(prediction_path), "overall_metrics": str(overall_path),
            "monthly_metrics": str(monthly_path), "group_decisions": str(decision_path),
            "audit": str(audit_path), "latest_models": latest_models,
        },
    )


@temporal_operation
def run_research_walk_forward_test(
    history_grouped_x: Mapping[str, pd.DataFrame],
    history_y: Target,
    history_core_oof: Union[CoreOOFResult, pd.DataFrame],
    promotion: ResidualPromotionResult,
    core_plan: Mapping[str, Any],
    residual_plans: Mapping[str, Mapping[str, Any]],
    feature_loader: FeatureLoader,
    target_loader: TargetLoader,
    *,
    target: str = "ret_30m",
    core_train_start: Any,
    residual_train_start: Any,
    test_start: Any,
    test_end: Any,
    output_dir: Union[str, os.PathLike[str]] = "runs/hierarchical_residual",
    resume: bool = True,
    final_fit_fn: Optional[FinalFitFn] = None,
    predict_fn: Optional[PredictFn] = None,
    plans_frozen_at: Any,
) -> ResearchTestResult:
    """Run Test month by month, requesting each label block after predictions.

    The model may learn from realised prior Test months, which makes this a
    research walk-forward Test rather than a single frozen holdout evaluation.
    """
    selected_history_y, history_index, history_feature_names = _validate_grouped_data(
        history_grouped_x, history_y, target
    )
    core_history = _require_core_frame(history_core_oof).copy()
    core_plan_valid = validate_final_xgb_plan(core_plan, CORE_GROUP)
    residual_plans_valid = _validate_residual_final_plans(residual_plans)
    selected_groups = tuple(promotion.selected_groups)
    if any(group not in RESIDUAL_GROUPS for group in selected_groups):
        raise ValueError("promotion contains an unknown residual group")
    start, end = pd.Timestamp(test_start), pd.Timestamp(test_end)
    require_plan_frozen_before(plans_frozen_at, start)
    promotion_end = promotion.audit.get("promotion_validation", [None, None])[1]
    if promotion_end is None or pd.Timestamp(promotion_end) >= start:
        raise ValueError("promotion selection must be completed before Test")
    temporal_metadata_for(history_index)
    history_dt = _datetimes(history_index)
    if (history_dt >= start).any():
        raise RuntimeError("future-label fail closed: history_y/features must end before Test")
    if (_datetimes(core_history.index) >= start).any():
        raise RuntimeError("history_core_oof must end before Test")
    if pd.Timestamp(residual_train_start) >= start:
        raise ValueError("residual_train_start must precede Test")
    fit = final_fit_fn or _default_final_fit
    predict = predict_fn or _default_predict
    root = Path(output_dir)

    dynamic_y = selected_history_y.copy()
    dynamic_x = {group: frame.copy() for group, frame in history_grouped_x.items()}
    dynamic_core = core_history.copy()
    prediction_parts: list[pd.DataFrame] = []
    monthly_audits: list[dict[str, Any]] = []
    latest_models: dict[str, Optional[str]] = {}
    for block in make_monthly_blocks(start, end):
        loaded = feature_loader(block.start, block.end)
        required_groups = (CORE_GROUP,) + selected_groups
        current_index, loaded_names = _validate_prediction_groups(loaded, required_groups)
        current_dt = _datetimes(current_index)
        temporal_metadata_for(current_index)
        if len(current_index) == 0:
            raise ValueError(f"feature_loader returned no rows for {block.block_id}")
        if (current_dt < block.start).any() or (current_dt > block.end).any():
            raise RuntimeError("feature_loader returned rows outside requested month block")
        for group in required_groups:
            if loaded_names[group] != history_feature_names[group]:
                raise ValueError(f"{group} Test feature names/order changed")
            if target in loaded[group].columns:
                raise RuntimeError(f"label leakage: target appears in loaded {group}")

        train_dt = _datetimes(dynamic_y.index)
        core_train_mask = _between(
            train_dt, core_train_start, block.start - pd.Timedelta(nanoseconds=1)
        )
        dynamic_label_ends, _ = temporal_metadata_for(dynamic_y.index)
        core_train_mask &= np.asarray(dynamic_label_ends < block.start)
        x_core_train = dynamic_x[CORE_GROUP].loc[dynamic_y.index[core_train_mask]]
        y_core_train = dynamic_y.loc[core_train_mask]
        validate_preprocessing_boundary(x_core_train, block.start)
        signature = _fit_signature(
            role=f"research_test_core:{block.block_id}",
            plan=core_plan_valid,
            x_train=x_core_train,
            y_train=y_core_train,
            prediction_index=current_index,
            x_prediction=loaded[CORE_GROUP],
        )
        cached_core = _load_prediction_cache(root, "research_test_core", block.block_id, signature) if resume else None
        core_cache_hit = cached_core is not None
        if cached_core is None:
            fitted_core = _guarded_final_fit(fit, as_of_time=block.start,
                x=x_core_train, y=y_core_train, target=target,
                plan=core_plan_valid, role=f"research_test_core:{block.block_id}",
            )
            current_core_prediction = _prediction_frame(
                current_index,
                predict(fitted_core.model, loaded[CORE_GROUP], tuple(x_core_train.columns)),
                "pred_G01",
            )
            _save_prediction_cache(
                current_core_prediction, root, "research_test_core", block.block_id, signature
            )
            latest_models[CORE_GROUP] = _save_latest_model(
                fitted_core.model, root / "models" / "latest_G01.json"
            ) or latest_models.get(CORE_GROUP)
        else:
            current_core_prediction = cached_core
            if not current_core_prediction.index.equals(current_index):
                raise RuntimeError("cached Test core prediction index mismatch")

        part = current_core_prediction.copy()
        residual_history_dt = _datetimes(dynamic_core.index)
        residual_train_mask = _between(
            residual_history_dt,
            residual_train_start,
            block.start - pd.Timedelta(nanoseconds=1),
        )
        residual_label_ends, _ = temporal_metadata_for(dynamic_core.index)
        residual_train_mask &= np.asarray(residual_label_ends < block.start)
        residual_history = dynamic_core.loc[residual_train_mask]
        group_audits: dict[str, Any] = {}
        for group in selected_groups:
            x_train_raw = dynamic_x[group].loc[residual_history.index]
            x_residual_train = _residual_features(
                group, x_train_raw, residual_history["pred_G01"]
            )
            current_group_x = _residual_features(
                group, loaded[group], current_core_prediction["pred_G01"]
            )
            y_residual_train = residual_history["core_residual"].rename(target)
            validate_preprocessing_boundary(x_residual_train, block.start)
            signature = _fit_signature(
                role=f"research_test_residual:{group}:{block.block_id}",
                plan=residual_plans_valid[group],
                x_train=x_residual_train,
                y_train=y_residual_train,
                prediction_index=current_index,
                x_prediction=current_group_x,
            )
            key = f"{block.block_id}_{group}"
            cached = _load_prediction_cache(root, "research_test_residual", key, signature) if resume else None
            cache_hit = cached is not None
            if cached is None:
                fitted = _guarded_final_fit(fit, as_of_time=block.start,
                    x=x_residual_train,
                    y=y_residual_train,
                    target=target,
                    plan=residual_plans_valid[group],
                    role=f"research_test_residual:{group}:{block.block_id}",
                )
                prediction = _prediction_frame(
                    current_index,
                    predict(fitted.model, current_group_x, tuple(x_residual_train.columns)),
                    f"residual_{group}",
                )
                _save_prediction_cache(
                    prediction, root, "research_test_residual", key, signature
                )
                latest_models[group] = _save_latest_model(
                    fitted.model, root / "models" / f"latest_residual_{group}.json"
                ) or latest_models.get(group)
            else:
                prediction = cached
                if not prediction.index.equals(current_index):
                    raise RuntimeError(f"cached Test residual index mismatch: {group}")
            part[f"residual_{group}"] = prediction.iloc[:, 0].to_numpy(np.float64)
            group_audits[group] = {
                "train_end_observed": _datetimes(x_residual_train.index)[-1],
                "strictly_past_training": bool(
                    _datetimes(x_residual_train.index).max() < block.start
                ),
                "cache_hit": cache_hit,
                "uses_pred_G01_feature": group in CONDITIONAL_GROUPS,
            }
        if selected_groups:
            part["residual_mean"] = part[
                [f"residual_{group}" for group in selected_groups]
            ].mean(axis=1)
            part["final_prediction"] = part["pred_G01"] + part["residual_mean"]
        else:
            part["final_prediction"] = part["pred_G01"]

        # This is intentionally the first request for labels in this block.
        loaded_target = _select_target(target_loader(block.start, block.end), target)
        _validate_index(loaded_target, f"Test target {block.block_id}")
        if not loaded_target.index.equals(current_index):
            raise RuntimeError("target_loader index/order must exactly equal frozen predictions")
        if not np.isfinite(loaded_target.to_numpy(np.float64)).all():
            raise ValueError("Test target contains non-finite values")
        part["actual"] = loaded_target.to_numpy(np.float64)
        part["core_residual"] = part["actual"] - part["pred_G01"]
        prediction_parts.append(part)

        # Only after evaluation may the realised block enter the next month.
        dynamic_y = pd.concat([dynamic_y, loaded_target])
        for group in EXPECTED_GROUPS:
            if group in loaded:
                new_frame = loaded[group]
            else:
                # Unselected groups are not trained in Test. Their columns are
                # represented as NaN solely to keep the history schema rectangular.
                new_frame = pd.DataFrame(
                    np.nan, index=current_index, columns=dynamic_x[group].columns
                )
            dynamic_x[group] = pd.concat([dynamic_x[group], new_frame])
        dynamic_core = pd.concat(
            [
                dynamic_core,
                pd.DataFrame(
                    {
                        "pred_G01": part["pred_G01"],
                        "actual": part["actual"],
                        "core_residual": part["core_residual"],
                        "month_id": block.block_id,
                    },
                    index=current_index,
                ),
            ]
        )
        monthly_audits.append(
            {
                "block_id": block.block_id,
                "feature_loader_called_before_target_loader": True,
                "target_loader_called_after_all_predictions_frozen": True,
                "prior_realised_test_labels_allowed_next_month": True,
                "core_train_end_observed": _datetimes(x_core_train.index)[-1],
                "core_strictly_past_training": bool(
                    _datetimes(x_core_train.index).max() < block.start
                ),
                "core_cache_hit": core_cache_hit,
                "residual_groups": group_audits,
                "prediction_rows": len(part),
            }
        )

    predictions = pd.concat(prediction_parts).sort_index()
    prediction_columns = ["pred_G01"] + [
        f"pred_G01_plus_{group}" for group in selected_groups
    ] + ["final_prediction"]
    for group in selected_groups:
        predictions[f"pred_G01_plus_{group}"] = (
            predictions["pred_G01"] + predictions[f"residual_{group}"]
        )
    overall, monthly, daily = _evaluate_columns(predictions, prediction_columns)
    audit = _common_audit()
    audit.update(
        {
            "stage": "research_walk_forward_test",
            "status": "research_walk_forward_test",
            "test_interval": [start, end],
            "selected_groups_frozen_before_test": selected_groups,
            "selection_mode": promotion.selection_mode,
            "monthly_expanding_refit_from_scratch": True,
            "only_selected_residual_groups_trained_in_test": True,
            "past_test_labels_enter_next_month_only": True,
            "labels_loaded_after_predictions": True,
            "monthly_blocks": monthly_audits,
        }
    )
    prediction_path = root / "predictions" / "research_walk_forward_test.pkl"
    overall_path = root / "metrics" / "research_test_overall.csv"
    monthly_path = root / "metrics" / "research_test_monthly.csv"
    daily_path = root / "metrics" / "research_test_daily.csv"
    audit_path = root / "audit" / "research_test_audit.json"
    _atomic_pickle(predictions, prediction_path)
    _atomic_csv(overall, overall_path)
    _atomic_csv(monthly, monthly_path)
    _atomic_csv(daily, daily_path)
    _atomic_json(audit_path, audit)
    return ResearchTestResult(
        predictions, overall, monthly, daily, audit,
        {
            "predictions": str(prediction_path), "overall_metrics": str(overall_path),
            "monthly_metrics": str(monthly_path), "daily_metrics": str(daily_path),
            "audit": str(audit_path), "latest_models": latest_models,
        },
    )
