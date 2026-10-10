"""Leakage-safe frozen Test evaluation for factor models.

The public entry point is :func:`evaluate_factor_model`.  It accepts a frozen
stage-2 ``TrainingResult`` and never calls a
training, fitting, updating, calibration, or early-stopping method.

Metrics
-------
* pooled Pearson and Spearman IC on pairwise-finite Test observations;
* monthly Pearson/Spearman IC, followed by an equal-weight mean across valid
  calendar months;
* RMSE and MAE as scale diagnostics;
* the complete single-series report returned by ``factor_series_qc_v2`` for
  the model prediction series: summary, warnings, rolling, blocks, sessions.

The already-aligned target is used as-is.  This module never shifts ``y``.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import tempfile
import time
from typing import Any, Iterator, Mapping, Optional, Sequence, Union

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype

from .factor_runtime import (
    temporal_operation, temporal_metadata_for, validate_preprocessing_boundary,
    CPU_THREADS,
    configure_torch_threads,
    index_fingerprint as _index_fingerprint,
    jsonable as _jsonable,
    limit_numeric_threads,
    pairwise_correlation as _correlation,
    runtime_thread_snapshot,
    series_fingerprint as _series_fingerprint,
    sha256_json as _sha256_json,
)

try:
    from .factor_series_qc_v2 import (
        DEFAULT_THRESHOLDS,
        QCThresholds,
        run_factor_qc,
    )
except ImportError as exc:  # pragma: no cover - depends on Notebook layout
    raise ImportError(
        "factor_series_qc_v2.py must be in the same directory as "
        "factor_model_testing.py (or otherwise importable)"
    ) from exc


__all__ = [
    "EXPECTED_INDEX_NAMES",
    "TestingResult",
    "evaluate_factor_model",
    "validate_test_inputs",
]

__version__ = "1.2.0"
EXPECTED_INDEX_NAMES = ("Date", "Contract", "Datetime")
Target = Union[pd.Series, pd.DataFrame]


@dataclass(frozen=True)
class TestingResult:
    """Outputs from an evaluation that does not fit or update the supplied model.

    The final-stage identity/config/time protocol is checked, but this container
    does not make a caller-owned native model or pandas payload immutable.
    """

    model: Any
    method: str
    target: str
    predictions: pd.DataFrame
    overall_metrics: pd.Series
    monthly_metrics: pd.DataFrame
    prediction_qc: Mapping[str, Any]
    audit: Mapping[str, Any]
    artifact_paths: Mapping[str, str]

    @property
    def pooled_ic(self) -> float:
        return float(self.overall_metrics["pooled_pearson_ic"])

    @property
    def mean_monthly_ic(self) -> float:
        return float(self.overall_metrics["mean_monthly_pearson_ic"])
    @property
    def pooled_spearman_ic(self) -> float:
        return float(self.overall_metrics["pooled_spearman_ic"])

    @property
    def mean_monthly_spearman_ic(self) -> float:
        return float(self.overall_metrics["mean_monthly_spearman_ic"])
    @property
    def qc_summary(self) -> pd.Series:
        return self.prediction_qc["summary"].copy()

    @property
    def qc_warnings(self) -> list[str]:
        return list(self.prediction_qc["warnings"])

    def __iter__(self) -> Iterator[Any]:
        """Allow ``pooled_ic, monthly_ic, qc = result``."""
        yield self.pooled_ic
        yield self.mean_monthly_ic
        yield self.prediction_qc


def _safe_component(value: str) -> str:
    cleaned = "".join(
        char if char.isalnum() or char in {"-", "_"} else "_" for char in value
    ).strip("_")
    return cleaned or "artifact"


def _canonical_method(method: str) -> str:
    aliases = {
        "xgb": "xgboost",
        "xgboost": "xgboost",
        "lgb": "lightgbm",
        "lgbm": "lightgbm",
        "lightgbm": "lightgbm",
        "cat": "catboost",
        "catboost": "catboost",
        "itransformer": "itransformer",
        "i-transformer": "itransformer",
        "transformer": "transformer",
    }
    key = str(method).strip().lower()
    if key not in aliases:
        raise ValueError(
            "method must be xgboost, lightgbm, catboost, itransformer, or transformer"
        )
    return aliases[key]


def _infer_method(model: Any) -> Optional[str]:
    module = type(model).__module__.lower()
    name = type(model).__name__.lower()
    joined = f"{module}.{name}"
    if "xgboost" in joined:
        return "xgboost"
    if "lightgbm" in joined:
        return "lightgbm"
    if "catboost" in joined:
        return "catboost"
    if "torch" in module or hasattr(model, "state_dict"):
        return "itransformer"
    return None


def _extract_training_binding(
    model: Any,
    training_result: Optional[Any],
) -> tuple[Any, Optional[Any]]:
    """Accept either ``model=stage2_result`` or a raw model."""
    result = training_result
    if (
        hasattr(model, "training_stage")
        and hasattr(model, "model")
        and hasattr(model, "feature_names")
    ):
        if result is not None and result is not model:
            raise ValueError(
                "model is already a TrainingResult; do not pass a different "
                "training_result"
            )
        result = model
        model = result.model

    if result is not None:
        if getattr(result, "training_stage", None) != "final":
            raise ValueError(
                "frozen Test evaluation requires the stage-2 result with "
                "training_stage='final'; a stage-1 selection model is rejected"
            )
        if not bool(getattr(result, "refit_full", False)):
            raise ValueError("training_result is not marked as Fit+Valid final training")
        if getattr(result, "model", None) is not model:
            raise ValueError(
                "the raw model is not the exact model stored in training_result.model"
            )
    if model is None:
        raise ValueError("model cannot be None")
    return model, result


def validate_test_inputs(
    x_test: pd.DataFrame,
    y_test: Target,
    target: Optional[str],
    *,
    require_single_contract: bool = False,
) -> tuple[pd.Series, pd.DatetimeIndex, tuple[str, ...], tuple[str, ...]]:
    """Validate the aligned Test pair and return the selected label."""
    if not isinstance(x_test, pd.DataFrame):
        raise TypeError("x_test must be a pandas DataFrame")
    if not isinstance(y_test, (pd.Series, pd.DataFrame)):
        raise TypeError("y_test must be a pandas Series or DataFrame")
    if not isinstance(x_test.index, pd.MultiIndex):
        raise TypeError("x_test must use a MultiIndex")
    if tuple(x_test.index.names) != EXPECTED_INDEX_NAMES:
        raise ValueError(
            f"x_test index names must be exactly {EXPECTED_INDEX_NAMES}; "
            f"got {tuple(x_test.index.names)}"
        )
    if not x_test.index.is_unique:
        raise ValueError("x_test index contains duplicate Date+Contract+Datetime keys")
    if not x_test.index.equals(y_test.index):
        raise ValueError("x_test and y_test indexes must be exactly equal and ordered")
    if x_test.empty:
        raise ValueError("x_test is empty")
    if x_test.columns.has_duplicates:
        raise ValueError("x_test contains duplicate feature names")
    if any(not isinstance(column, str) for column in x_test.columns):
        raise TypeError("all x_test feature names must be strings")
    nonnumeric = [str(c) for c in x_test.columns if not is_numeric_dtype(x_test[c])]
    if nonnumeric:
        raise TypeError(f"all Test features must be numeric; nonnumeric={nonnumeric[:10]}")
    # Scan in bounded column batches.  A single float64 conversion of a wide
    # multi-million-row factor frame can otherwise double peak memory before
    # prediction even starts.
    for left in range(0, x_test.shape[1], 64):
        values = x_test.iloc[:, left : left + 64].to_numpy(
            dtype=np.float64, copy=False
        )
        if np.isinf(values).any():
            raise ValueError(
                "x_test contains +/-inf; preprocessing should convert them to NaN"
            )

    if isinstance(y_test, pd.DataFrame):
        if target is None:
            raise ValueError(
                "target is required because y_test contains multiple label columns"
            )
        if target not in y_test.columns:
            raise KeyError(
                f"target {target!r} is absent from y_test; "
                f"available={list(map(str, y_test.columns))}"
            )
        selected = y_test[target].copy()
    else:
        if target is not None and y_test.name is not None and str(y_test.name) != str(target):
            raise ValueError(
                f"target={target!r} conflicts with y_test.name={y_test.name!r}"
            )
        selected = y_test.copy()
        if selected.name is None:
            selected.name = target or "target"

    if not is_numeric_dtype(selected):
        raise TypeError("selected Test target must be numeric")
    selected = selected.astype(np.float64, copy=False)
    if np.isinf(selected.to_numpy()).any():
        raise ValueError("selected Test target contains +/-inf")

    datetimes = pd.DatetimeIndex(x_test.index.get_level_values("Datetime"))
    if datetimes.isna().any():
        raise ValueError("Datetime index level contains NaT")
    contracts = tuple(
        str(value)
        for value in pd.unique(x_test.index.get_level_values("Contract"))
    )
    # Kept only as a backward-compatible keyword.  Contract count does not
    # change prediction or pooled/monthly metric algorithms.
    _ = require_single_contract
    return selected, datetimes, tuple(map(str, x_test.columns)), contracts


def _extract_ic_series(ic_y: Any, x_test: pd.DataFrame) -> pd.Series:
    """Extract an independent IC-evaluation label aligned exactly with x_test."""
    if isinstance(ic_y, pd.DataFrame):
        if ic_y.shape[1] != 1:
            raise ValueError("ic_y DataFrame must have exactly one column")
        series = ic_y.iloc[:, 0]
    elif isinstance(ic_y, pd.Series):
        series = ic_y
    else:
        raise TypeError("ic_y must be a pandas Series or single-column DataFrame")
    if not series.index.equals(x_test.index):
        raise ValueError(
            "ic_y index must exactly match x_test index (same sampling and row mask)"
        )
    if not is_numeric_dtype(series):
        raise TypeError("ic_y must be numeric")
    return series


def _check_training_contract(
    result: Optional[Any],
    target: str,
    method: str,
    feature_names: Sequence[str],
) -> None:
    if result is None:
        return
    if str(getattr(result, "target", "")) != str(target):
        raise ValueError(
            f"Test target {target!r} differs from trained target "
            f"{getattr(result, 'target', None)!r}"
        )
    trained_method = _canonical_method(str(getattr(result, "method", "")))
    if trained_method != method:
        raise ValueError(
            f"method={method!r} differs from training_result.method={trained_method!r}"
        )
    trained_features = tuple(map(str, getattr(result, "feature_names", ())))
    if trained_features != tuple(feature_names):
        missing = [name for name in trained_features if name not in feature_names]
        extra = [name for name in feature_names if name not in trained_features]
        raise ValueError(
            "x_test feature names/order differ from final training; "
            f"missing={missing[:10]}, extra={extra[:10]}"
        )


def _verify_test_is_after_training(
    result: Optional[Any],
    datetimes: pd.DatetimeIndex,
) -> Optional[pd.Timestamp]:
    """Reject temporal overlap when stage-2 split evidence is available."""
    if result is None:
        return None
    split_audit = getattr(result, "audit", {}).get("split_audit", {})
    observed_ends: list[pd.Timestamp] = []
    for split_name in ("fit", "valid"):
        raw_end = split_audit.get(split_name, {}).get("actual_end")
        if raw_end is not None:
            observed_ends.append(pd.Timestamp(raw_end))
    if not observed_ends:
        raise ValueError(
            "training_result does not contain Fit/Valid actual_end timestamps; "
            "the Test-versus-training temporal boundary cannot be audited"
        )
    temporal_audit = getattr(result, "audit", {}).get("temporal_audit", {})
    label_end = temporal_audit.get("max_training_label_end")
    if not label_end or not temporal_audit.get("purge_check_executed"):
        raise ValueError("training result lacks verified label-end boundary metadata")
    training_end = max(max(observed_ends), pd.Timestamp(label_end))
    test_start = pd.Timestamp(datetimes.min())
    if test_start <= training_end:
        raise ValueError(
            "Test data overlaps or precedes the final training period: "
            f"test_start={test_start}, training_end={training_end}"
        )
    frozen_at = temporal_audit.get("fit_as_of")
    if frozen_at is None or pd.isna(pd.Timestamp(frozen_at)):
        raise ValueError("training result lacks a declared final model freeze time")
    if test_start < pd.Timestamp(frozen_at):
        raise ValueError(
            "Test starts before the final model freeze time: "
            f"test_start={test_start}, frozen_at={frozen_at}"
        )
    return training_end


def _predict_tree_model(
    model: Any,
    method: str,
    x_test: pd.DataFrame,
    feature_names: Sequence[str],
) -> np.ndarray:
    X = x_test.to_numpy(dtype=np.float32, copy=False)
    if method == "xgboost":
        try:
            import xgboost as xgb
        except ImportError as exc:
            raise ImportError("xgboost is required to evaluate this model") from exc
        matrix = xgb.DMatrix(
            X,
            feature_names=list(feature_names),
            missing=np.nan,
            nthread=CPU_THREADS,
        )
        prediction = model.predict(matrix)
    elif method == "lightgbm":
        prediction = model.predict(X, num_threads=CPU_THREADS)
    elif method == "catboost":
        prediction = model.predict(X, thread_count=CPU_THREADS)
    else:  # pragma: no cover - guarded by caller
        raise AssertionError(method)
    prediction = np.asarray(prediction, dtype=np.float64).reshape(-1)
    if len(prediction) != len(x_test):
        raise RuntimeError(
            f"model returned {len(prediction)} predictions for {len(x_test)} rows"
        )
    return prediction


def _session_code(datetimes: pd.DatetimeIndex) -> np.ndarray:
    minutes = datetimes.hour * 60 + datetimes.minute
    return np.where(minutes < 12 * 60, "AM", "PM")


def _predict_itransformer(
    model: Any,
    x_test: pd.DataFrame,
    config: Mapping[str, Any],
) -> tuple[np.ndarray, dict[str, Any]]:
    """Reproduce training window semantics without consulting Test labels."""
    try:
        import torch
    except ImportError as exc:
        raise ImportError("torch is required to evaluate iTransformer") from exc

    params = config.get("params")
    if not isinstance(params, Mapping):
        raise ValueError("iTransformer evaluation requires config['params']")
    required = {
        "window_size",
        "allow_cross_day",
        "allow_cross_session",
        "missing_value_strategy",
        "batch_size",
        "num_workers",
        "device",
    }
    missing = sorted(required.difference(params))
    if missing:
        raise ValueError(f"iTransformer config is missing parameters: {missing}")
    window = int(params["window_size"])
    if window <= 0:
        raise ValueError("iTransformer window_size must be positive")

    frame = x_test.index.to_frame(index=False)
    frame["_position"] = np.arange(len(frame), dtype=np.int64)
    order = frame.sort_values(
        ["Contract", "Datetime", "_position"], kind="mergesort"
    )["_position"].to_numpy(np.int64)
    sorted_index = x_test.index[order]
    sorted_frame = sorted_index.to_frame(index=False)
    sorted_datetimes = pd.DatetimeIndex(sorted_frame["Datetime"])
    duplicate = pd.MultiIndex.from_arrays(
        [sorted_frame["Contract"], sorted_frame["Datetime"]]
    ).duplicated().any()
    if duplicate:
        raise ValueError(
            "x_test contains duplicate Contract+Datetime rows; sequence order is ambiguous"
        )

    group_arrays: list[np.ndarray] = [sorted_frame["Contract"].to_numpy()]
    if not bool(params["allow_cross_day"]):
        group_arrays.append(sorted_frame["Date"].to_numpy())
    if not bool(params["allow_cross_session"]):
        group_arrays.append(_session_code(sorted_datetimes))
    groups = pd.MultiIndex.from_arrays(group_arrays)
    codes, _ = pd.factorize(groups, sort=False)
    endpoints: list[np.ndarray] = []
    if len(codes):
        boundaries = np.flatnonzero(np.r_[True, codes[1:] != codes[:-1], True])
        for left, right in zip(boundaries[:-1], boundaries[1:]):
            if right - left >= window:
                endpoints.append(np.arange(left + window - 1, right, dtype=np.int64))
    end_positions = (
        np.concatenate(endpoints) if endpoints else np.empty(0, dtype=np.int64)
    )
    if len(end_positions) == 0:
        raise ValueError(
            "x_test creates zero iTransformer windows; inspect window_size and grouping"
        )

    # Advanced indexing by ``order`` creates the required independent buffer.
    X = x_test.to_numpy(dtype=np.float32, copy=False)[order]
    missing_count = int(np.isnan(X).sum())
    strategy = str(params["missing_value_strategy"]).lower()
    if strategy == "raise" and missing_count:
        raise ValueError(
            f"x_test contains {missing_count} missing factor values but the trained "
            "iTransformer uses missing_value_strategy='raise'"
        )
    if strategy == "zero":
        np.nan_to_num(X, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
    elif strategy != "raise":
        raise ValueError("missing_value_strategy must be 'zero' or 'raise'")

    thread_policy = configure_torch_threads(torch)
    requested_device = str(params["device"]).lower().strip()
    if requested_device == "auto":
        if torch.cuda.is_available():
            device = torch.device("cuda")
        elif getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
            device = torch.device("mps")
        else:
            device = torch.device("cpu")
    else:
        device = torch.device(requested_device)
    model.to(device)
    model.eval()

    class _WindowDataset(torch.utils.data.Dataset):
        def __len__(self):
            return int(len(end_positions))

        def __getitem__(self, item):
            end = int(end_positions[item])
            return torch.from_numpy(X[end - window + 1 : end + 1])

    loader = torch.utils.data.DataLoader(
        _WindowDataset(),
        batch_size=int(params["batch_size"]),
        shuffle=False,
        num_workers=int(params["num_workers"]),
        drop_last=False,
    )
    pieces: list[np.ndarray] = []
    with torch.no_grad():
        for values in loader:
            pieces.append(
                model(values.to(device)).detach().cpu().numpy().astype(np.float64)
            )
    endpoint_prediction = np.concatenate(pieces).reshape(-1)
    if len(endpoint_prediction) != len(end_positions):
        raise RuntimeError("iTransformer prediction count differs from window count")
    prediction = np.full(len(x_test), np.nan, dtype=np.float64)
    original_end_positions = order[end_positions]
    prediction[original_end_positions] = endpoint_prediction
    return prediction, {
        "source_rows": int(len(x_test)),
        "window_count": int(len(end_positions)),
        "window_size": window,
        "structural_warmup_rows": int(len(x_test) - len(end_positions)),
        "missing_factor_values": missing_count,
        "allow_cross_day": bool(params["allow_cross_day"]),
        "allow_cross_session": bool(params["allow_cross_session"]),
        "device": str(device),
        "cpu_threads": thread_policy["intra_op_threads"],
        "cpu_interop_threads": thread_policy["inter_op_threads"],
        "labels_used_to_construct_windows": False,
    }


def _error_metrics(prediction: np.ndarray, actual: np.ndarray) -> tuple[float, float, int]:
    finite = np.isfinite(prediction) & np.isfinite(actual)
    error = prediction[finite] - actual[finite]
    if not len(error):
        return float("nan"), float("nan"), 0
    rmse = float(np.sqrt(np.mean(np.square(error), dtype=np.float64)))
    mae = float(np.mean(np.abs(error), dtype=np.float64))
    return rmse, mae, int(len(error))


def _monthly_metrics(predictions: pd.DataFrame) -> pd.DataFrame:
    datetimes = pd.DatetimeIndex(predictions.index.get_level_values("Datetime"))
    months = datetimes.to_period("M").astype(str)
    rows: list[dict[str, Any]] = []
    for month in pd.unique(months):
        block = predictions.loc[np.asarray(months == month)]
        pred = block["prediction"].to_numpy(np.float64)
        actual = block["actual"].to_numpy(np.float64)
        metric_target = block["target"].to_numpy(np.float64) if "target" in block else actual
        rmse, mae, error_pair_count = _error_metrics(pred, metric_target)
        pair_count = int((np.isfinite(pred) & np.isfinite(actual)).sum())
        finite = np.isfinite(pred) & np.isfinite(actual)
        rows.append(
            {
                "month": str(month),
                "n_total": int(len(block)),
                "n_pairwise_finite": pair_count,
                "n_error_pairwise_finite": error_pair_count,
                "pairwise_finite_ratio": float(pair_count / len(block)),
                "pearson_ic": _correlation(pred, actual),
                "spearman_ic": _correlation(pred, actual, rank=True),
                "rmse": rmse,
                "mae": mae,
                "prediction_mean": float(np.mean(pred[finite])) if finite.any() else np.nan,
                "prediction_std": float(np.std(pred[finite])) if finite.any() else np.nan,
                "actual_mean": float(np.mean(actual[finite])) if finite.any() else np.nan,
                "actual_std": float(np.std(actual[finite])) if finite.any() else np.nan,
            }
        )
    return pd.DataFrame(rows).set_index("month")


def _mean_and_ir(values: pd.Series) -> tuple[float, float, int, float]:
    finite = values[np.isfinite(values.to_numpy(np.float64))].astype(np.float64)
    if finite.empty:
        return float("nan"), float("nan"), 0, float("nan")
    mean = float(finite.mean())
    std = float(finite.std(ddof=1)) if len(finite) > 1 else float("nan")
    icir = (
        float(mean / std * np.sqrt(12.0))
        if np.isfinite(std) and std > 0
        else float("nan")
    )
    return mean, std, int(len(finite)), icir


def _qc_order_and_segments(predictions: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    frame = predictions.index.to_frame(index=False)
    frame["_position"] = np.arange(len(frame), dtype=np.int64)
    order = frame.sort_values(
        ["Contract", "Datetime", "_position"], kind="mergesort"
    )["_position"].to_numpy(np.int64)
    ordered = predictions.iloc[order]
    ordered_frame = ordered.index.to_frame(index=False)
    groups = pd.MultiIndex.from_arrays(
        [ordered_frame["Contract"], ordered_frame["Date"]]
    )
    segments, uniques = pd.factorize(groups, sort=False)
    names = np.asarray(
        [f"{contract}|{date}" for contract, date in groups], dtype=object
    )
    return ordered, segments.astype(np.int64), names


def _write_artifacts(
    *,
    predictions: pd.DataFrame,
    overall: pd.Series,
    monthly: pd.DataFrame,
    prediction_qc: Mapping[str, Any],
    audit: Mapping[str, Any],
    method: str,
    target: str,
    run_id: str,
    output_dir: Union[str, os.PathLike[str]],
) -> dict[str, str]:
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    final_dir = root / f"{_safe_component(method)}_{_safe_component(target)}_test_{run_id}_{stamp}"
    temporary = Path(tempfile.mkdtemp(prefix=".test_tmp_", dir=str(root)))

    def save_table_prefer_parquet(
        frame: pd.DataFrame,
        stem: str,
        *,
        csv_index: bool,
    ) -> str:
        parquet_name = f"{stem}.parquet"
        try:
            frame.to_parquet(temporary / parquet_name)
            return parquet_name
        except (ImportError, TypeError, ValueError, NotImplementedError):
            # Missing engines and Arrow schema-conversion failures should not
            # discard an otherwise complete frozen-Test evaluation.
            csv_name = f"{stem}.csv"
            frame.to_csv(temporary / csv_name, index=csv_index)
            return csv_name

    try:
        prediction_name = save_table_prefer_parquet(
            predictions, "predictions", csv_index=True
        )
        monthly.to_csv(temporary / "monthly_metrics.csv", index=True)
        pd.DataFrame([overall]).to_csv(temporary / "overall_metrics.csv", index=False)
        rolling_name = save_table_prefer_parquet(
            prediction_qc["rolling"], "prediction_qc_rolling", csv_index=True
        )
        prediction_qc["blocks"].to_csv(temporary / "prediction_qc_blocks.csv", index=False)
        prediction_qc.get("sessions", pd.DataFrame()).to_csv(
            temporary / "prediction_qc_sessions.csv", index=False
        )
        (temporary / "prediction_qc_summary.json").write_text(
            json.dumps(
                {
                    "summary": _jsonable(prediction_qc["summary"]),
                    "warnings": _jsonable(prediction_qc["warnings"]),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (temporary / "audit.json").write_text(
            json.dumps(_jsonable(audit), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.rename(final_dir)
    except Exception:
        for child in temporary.glob("*"):
            child.unlink(missing_ok=True)
        temporary.rmdir()
        raise
    return {
        "directory": str(final_dir),
        "predictions": str(final_dir / prediction_name),
        "overall_metrics": str(final_dir / "overall_metrics.csv"),
        "monthly_metrics": str(final_dir / "monthly_metrics.csv"),
        "qc_summary": str(final_dir / "prediction_qc_summary.json"),
        "qc_rolling": str(final_dir / rolling_name),
        "qc_blocks": str(final_dir / "prediction_qc_blocks.csv"),
        "qc_sessions": str(final_dir / "prediction_qc_sessions.csv"),
        "audit": str(final_dir / "audit.json"),
    }


@limit_numeric_threads
@temporal_operation
def evaluate_factor_model(
    model: Any,
    x_test: pd.DataFrame,
    y_test: Target,
    target: Optional[str],
    *,
    method: Optional[str] = None,
    training_result: Optional[Any] = None,
    feature_names: Optional[Sequence[str]] = None,
    model_config: Optional[Mapping[str, Any]] = None,
    require_single_contract: bool = False,
    rolling_window: int = 240,
    block_size: int = 240,
    qc_thresholds: QCThresholds = DEFAULT_THRESHOLDS,
    output_dir: Optional[Union[str, os.PathLike[str]]] = None,
    ic_y: Optional[Target] = None,
) -> TestingResult:
    """Evaluate one already-frozen model on an untouched Test set.

    Preferred usage is ``model=stage2_result``.  A raw model is accepted only
    when the matching ``training_result=stage2_result`` is supplied, so the
    feature schema, final-stage identity and temporal boundary remain bound.
    """
    if not isinstance(rolling_window, int) or isinstance(rolling_window, bool) or rolling_window < 3:
        raise ValueError(
            "rolling_window must be an integer >= 3, as required by "
            "factor_series_qc_v2 rolling QC"
        )
    if not isinstance(block_size, int) or isinstance(block_size, bool) or block_size <= 0:
        raise ValueError("block_size must be a positive integer")
    started = time.perf_counter()
    raw_model, result = _extract_training_binding(model, training_result)
    if result is None:
        raise ValueError(
            "frozen Test evaluation requires the stage-2 TrainingResult; "
            "an unbound raw model cannot prove feature or temporal identity"
        )
    selected_y, datetimes, x_features, contracts = validate_test_inputs(
        x_test,
        y_test,
        target,
        require_single_contract=require_single_contract,
    )
    _, temporal_audit = temporal_metadata_for(x_test.index)
    target_name = str(selected_y.name)
    # 独立的 IC 评估标签：提供 ic_y 时用它算 IC，否则用 target 列。
    ic_series = _extract_ic_series(ic_y, x_test) if ic_y is not None else selected_y

    result_method = getattr(result, "method", None) if result is not None else None
    inferred = _infer_method(raw_model)
    chosen_method = _canonical_method(method or result_method or inferred or "")
    expected_features = (
        tuple(map(str, getattr(result, "feature_names", ())))
        if result is not None
        else tuple(map(str, feature_names or x_features))
    )
    if expected_features != x_features:
        raise ValueError(
            "x_test columns must exactly match the trained feature names and order"
        )
    _check_training_contract(result, target_name, chosen_method, x_features)
    training_end = _verify_test_is_after_training(result, datetimes)
    validate_preprocessing_boundary(x_test, datetimes.min())

    config = deepcopy(
        dict(
            model_config
            or (
                getattr(result, "audit", {}).get("config", {})
                if result is not None
                else {}
            )
        )
    )
    if result is not None and model_config is not None:
        trained_config = getattr(result, "audit", {}).get("config")
        if trained_config is not None and _sha256_json(config) != _sha256_json(trained_config):
            raise ValueError(
                "model_config differs from the frozen config in training_result.audit"
            )

    prediction_started = time.perf_counter()
    transformer_metadata: dict[str, Any] = {}
    if chosen_method in {"itransformer", "transformer"}:
        prediction_values, transformer_metadata = _predict_itransformer(
            raw_model, x_test, config
        )
        expected_warmup = int(config["params"]["window_size"]) - 1
    else:
        prediction_values = _predict_tree_model(
            raw_model, chosen_method, x_test, x_features
        )
        expected_warmup = 0
    prediction_seconds = time.perf_counter() - prediction_started

    predictions = pd.DataFrame(
        {
            "actual": ic_series.to_numpy(np.float64),
            "target": selected_y.to_numpy(np.float64),
            "prediction": prediction_values,
        },
        index=x_test.index.copy(),
    )
    predictions["pairwise_finite"] = (
        np.isfinite(predictions["actual"].to_numpy())
        & np.isfinite(predictions["prediction"].to_numpy())
    )
    pred = predictions["prediction"].to_numpy(np.float64)
    actual = predictions["actual"].to_numpy(np.float64)
    rmse, mae, error_pair_count = _error_metrics(pred, selected_y.to_numpy(np.float64))
    pair_count = int((np.isfinite(pred) & np.isfinite(actual)).sum())
    pooled_pearson = _correlation(pred, actual)
    pooled_spearman = _correlation(pred, actual, rank=True)

    monthly = _monthly_metrics(predictions)
    mean_monthly_pearson, std_monthly_pearson, pearson_months, pearson_icir = _mean_and_ir(
        monthly["pearson_ic"]
    )
    mean_monthly_spearman, std_monthly_spearman, spearman_months, spearman_icir = _mean_and_ir(
        monthly["spearman_ic"]
    )

    ordered, session_segments, session_names = _qc_order_and_segments(predictions)
    prediction_qc = run_factor_qc(
        ordered["prediction"],
        index=ordered.index,
        factor_name="model_prediction",
        expected_warmup=expected_warmup,
        rolling_window=rolling_window,
        block_size=block_size,
        factor_type="response",
        session_segment=session_segments,
        session_name=session_names,
        thresholds=qc_thresholds,
    )

    overall = pd.Series(
        {
            "n_test_rows": int(len(predictions)),
            "n_prediction_finite": int(np.isfinite(pred).sum()),
            "n_target_finite": int(np.isfinite(selected_y.to_numpy(np.float64)).sum()),
            "n_ic_target_finite": int(np.isfinite(actual).sum()),
            "n_pairwise_finite": pair_count,
            "n_error_pairwise_finite": error_pair_count,
            "pairwise_finite_ratio": float(pair_count / len(predictions)),
            "pooled_pearson_ic": pooled_pearson,
            "pooled_spearman_ic": pooled_spearman,
            "test_rmse": rmse,
            "test_mae": mae,
            "calendar_month_count": int(len(monthly)),
            "valid_pearson_month_count": pearson_months,
            "mean_monthly_pearson_ic": mean_monthly_pearson,
            "std_monthly_pearson_ic": std_monthly_pearson,
            "annualized_monthly_pearson_icir": pearson_icir,
            "positive_monthly_pearson_ratio": float(
                (monthly["pearson_ic"].dropna() > 0).mean()
            ),
            "valid_spearman_month_count": spearman_months,
            "mean_monthly_spearman_ic": mean_monthly_spearman,
            "std_monthly_spearman_ic": std_monthly_spearman,
            "annualized_monthly_spearman_icir": spearman_icir,
            "positive_monthly_spearman_ratio": float(
                (monthly["spearman_ic"].dropna() > 0).mean()
            ),
        },
        name="test_metrics",
        dtype=object,
    )

    index_hash = _index_fingerprint(x_test.index)
    target_hash = _series_fingerprint(selected_y)
    prediction_hash = _series_fingerprint(predictions["prediction"])
    training_run_id = (
        getattr(result, "audit", {}).get("run_id") if result is not None else None
    )
    binding = {
        "method": chosen_method,
        "target": target_name,
        "feature_schema_sha256": _sha256_json(x_features),
        "test_index_sha256": index_hash,
        "test_target_sha256": target_hash,
        "test_ic_target_sha256": _series_fingerprint(ic_series),
        "prediction_sha256": prediction_hash,
        "training_run_id": training_run_id,
    }
    run_id = _sha256_json(binding)[:16]
    audit: dict[str, Any] = {
        "library_version": __version__,
        "temporal_audit": temporal_audit,
        "run_id": run_id,
        "method": chosen_method,
        "target": target_name,
        "contracts": list(contracts),
        "require_single_contract": False,
        "test_start": datetimes.min().isoformat(),
        "test_end": datetimes.max().isoformat(),
        "final_model_frozen_at": result.audit["temporal_audit"]["fit_as_of"],
        "final_training_actual_end": (
            training_end.isoformat() if training_end is not None else None
        ),
        "test_starts_strictly_after_final_training": (
            True if training_end is not None else None
        ),
        "test_rows": int(len(x_test)),
        "feature_count": int(len(x_features)),
        "feature_names": list(x_features),
        "feature_schema_sha256": binding["feature_schema_sha256"],
        "test_index_sha256": index_hash,
        "test_target_sha256": target_hash,
        "test_ic_target_sha256": _series_fingerprint(ic_series),
        "prediction_sha256": prediction_hash,
        "training_run_id": training_run_id,
        "training_stage_verified_final": result is not None,
        "final_model_identity_verified": result is not None,
        "native_model_state_integrity_verified": False,
        "x_y_index_exactly_equal": True,
        "target_shift_applied": False,
        "test_rows_used_for_fit": 0,
        "fit_or_update_called": False,
        "test_used_for_early_stopping": False,
        "test_used_for_hyperparameter_selection": False,
        "pooled_ic_defined": bool(np.isfinite(pooled_pearson)),
        "monthly_ic_defined": pearson_months > 0,
        "undefined_metrics_preserve_predictions": True,
        "error_metrics_target": target_name,
        "ic_target_name": str(ic_series.name),
        "pooled_ic_definition": (
            "Pearson correlation across all pairwise-finite Test prediction/target rows"
        ),
        "monthly_ic_definition": (
            "Pearson correlation within each calendar month across pairwise-finite "
            "rows; reported mean is equal-weight across finite monthly IC values"
        ),
        "qc_series": "model_prediction",
        "qc_order": "Contract then Datetime; stable ties",
        "qc_session_segment": "Contract + Date",
        "processing_fingerprint": x_test.attrs.get("processing_fingerprint"),
        "sampling_result_fingerprint": x_test.attrs.get("sampling_result_fingerprint"),
        "transformer_prediction_metadata": transformer_metadata,
        "runtime_threads": runtime_thread_snapshot(),
        "prediction_seconds": prediction_seconds,
        "total_seconds_before_artifact_write": time.perf_counter() - started,
        "versions": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
    }
    paths: dict[str, str] = {}
    if output_dir is not None:
        paths = _write_artifacts(
            predictions=predictions,
            overall=overall,
            monthly=monthly,
            prediction_qc=prediction_qc,
            audit=audit,
            method=chosen_method,
            target=target_name,
            run_id=run_id,
            output_dir=output_dir,
        )
        audit["artifact_paths"] = deepcopy(paths)
    else:
        audit["artifact_paths"] = {}

    print(
        "[frozen Test complete] "
        f"pooled_pearson_ic={pooled_pearson:.10g}; "
        f"mean_monthly_pearson_ic={mean_monthly_pearson:.10g}; "
        f"pairwise_finite_rows={pair_count}"
    )
    return TestingResult(
        model=raw_model,
        method=chosen_method,
        target=target_name,
        predictions=predictions,
        overall_metrics=overall,
        monthly_metrics=monthly,
        prediction_qc=prediction_qc,
        audit=audit,
        artifact_paths=paths,
    )
