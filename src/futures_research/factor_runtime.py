"""Shared runtime policy and reproducibility helpers for factor models.

This module deliberately controls *library parallelism*, not CPU affinity.
The operating system may schedule the configured worker threads on any CPU.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from contextlib import contextmanager
from functools import wraps
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import numpy as np
import pandas as pd


CPU_THREADS = max(1, min(4, int(os.environ.get("FUTURES_RESEARCH_THREADS", "1"))))
TORCH_INTEROP_THREADS = 1


_THREAD_PARAMETER = {
    "xgboost": "nthread",
    "lightgbm": "num_threads",
    "catboost": "thread_count",
    "itransformer": "thread_count",
    "transformer": "thread_count",
}


def require_threadpoolctl() -> None:
    """Fail fast when production-wide native pool control is unavailable."""
    try:
        import threadpoolctl  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "threadpoolctl is required in the production kernel so NumPy/BLAS "
            "callbacks obey CPU_THREADS. Install it before training."
        ) from exc


@contextmanager
def numeric_thread_limit():
    """Soft-limit already-loaded BLAS/OpenMP pools for one public operation."""
    try:
        from threadpoolctl import threadpool_limits
    except ImportError:
        # Unit tests and lightweight validation environments may omit the
        # package.  The production notebook calls require_threadpoolctl first.
        yield
    else:
        with threadpool_limits(limits=CPU_THREADS):
            yield


def limit_numeric_threads(function):
    """Decorator preserving the public signature via ``functools.wraps``."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with numeric_thread_limit():
            return function(*args, **kwargs)

    return wrapped


def enforce_model_thread_config(
    method: str,
    params: Mapping[str, Any],
) -> dict[str, Any]:
    """Return independent params with the one project-wide thread limit.

    Missing backend parameters are filled.  A conflicting explicit value is
    rejected instead of being silently overwritten, so the effective resource
    policy remains traceable from the saved configuration.
    """
    canonical = str(method).lower().strip()
    if canonical not in _THREAD_PARAMETER:
        return dict(params)
    key = _THREAD_PARAMETER[canonical]
    result = dict(params)
    configured = result.get(key)
    if configured is not None:
        if isinstance(configured, bool) or not isinstance(configured, (int, np.integer)):
            raise TypeError(f"{canonical} {key} must be an integer")
        if int(configured) != CPU_THREADS:
            raise ValueError(
                f"{canonical} {key}={configured} conflicts with the project "
                f"CPU_THREADS={CPU_THREADS}"
            )
    result[key] = CPU_THREADS
    return result


def configure_torch_threads(torch: Any) -> dict[str, int]:
    """Apply the CPU policy before PyTorch creates either native thread pool."""
    torch.set_num_threads(CPU_THREADS)
    current_interop = int(torch.get_num_interop_threads())
    if current_interop != TORCH_INTEROP_THREADS:
        try:
            torch.set_num_interop_threads(TORCH_INTEROP_THREADS)
        except RuntimeError as exc:
            raise RuntimeError(
                "PyTorch inter-op threads were initialised before the factor "
                "runtime policy. Restart the kernel and import the training "
                "module before running PyTorch work."
            ) from exc
    return {
        "intra_op_threads": int(torch.get_num_threads()),
        "inter_op_threads": int(torch.get_num_interop_threads()),
    }


def jsonable(value: Any) -> Any:
    """Convert common scientific Python values to strict JSON values."""
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, pd.Series):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if np.isfinite(number) else None
    if isinstance(value, np.ndarray):
        return jsonable(value.tolist())
    return value


def sha256_json(value: Any) -> str:
    payload = json.dumps(
        jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def sha256_file(path: os.PathLike[str] | str) -> str:
    """Hash one source artifact without loading it into memory."""
    digest = sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def index_fingerprint(index: pd.Index) -> str:
    hashes = pd.util.hash_pandas_object(index, index=True).to_numpy(
        dtype=np.uint64,
        copy=False,
    )
    return sha256(np.ascontiguousarray(hashes).tobytes()).hexdigest()


def series_fingerprint(series: pd.Series) -> str:
    hashes = pd.util.hash_pandas_object(series, index=True).to_numpy(
        dtype=np.uint64,
        copy=False,
    )
    return sha256(np.ascontiguousarray(hashes).tobytes()).hexdigest()


def pairwise_correlation(a: Any, b: Any, *, rank: bool = False) -> float:
    """Pearson/Spearman correlation with one shared finite/constant policy."""
    left = np.asarray(a, dtype=np.float64).reshape(-1)
    right = np.asarray(b, dtype=np.float64).reshape(-1)
    if left.shape != right.shape:
        raise ValueError(f"correlation shapes differ: {left.shape} != {right.shape}")
    finite = np.isfinite(left) & np.isfinite(right)
    left, right = left[finite], right[finite]
    if left.size < 2:
        return float("nan")
    if rank:
        left = pd.Series(left).rank(method="average").to_numpy(np.float64)
        right = pd.Series(right).rank(method="average").to_numpy(np.float64)
    left -= left.mean(dtype=np.float64)
    right -= right.mean(dtype=np.float64)
    denominator = float(np.sqrt(np.dot(left, left) * np.dot(right, right)))
    if not np.isfinite(denominator) or denominator <= 0.0:
        return float("nan")
    return float(np.dot(left, right) / denominator)


def frame_fingerprint(
    frame: pd.DataFrame,
    *,
    positions: Optional[Sequence[int] | np.ndarray] = None,
    chunk_rows: int = 8_192,
) -> str:
    """Hash index, columns, dtypes and values in bounded row chunks.

    ``positions`` binds an exact training subset without first materialising
    the entire subset.  The fingerprint is intentionally versioned because
    pandas' stable hashing implementation is part of the protocol.
    """
    if chunk_rows <= 0:
        raise ValueError("chunk_rows must be positive")
    selected = (
        np.arange(len(frame), dtype=np.int64)
        if positions is None
        else np.asarray(positions, dtype=np.int64)
    )
    if selected.ndim != 1:
        raise ValueError("positions must be one-dimensional")
    digest = sha256()
    digest.update(b"factor-frame-v1\0")
    digest.update(sha256_json(list(map(str, frame.columns))).encode("ascii"))
    digest.update(sha256_json([str(dtype) for dtype in frame.dtypes]).encode("ascii"))
    for start in range(0, len(selected), chunk_rows):
        block = frame.iloc[selected[start : start + chunk_rows]]
        hashes = pd.util.hash_pandas_object(
            block,
            index=True,
            categorize=True,
        ).to_numpy(dtype=np.uint64, copy=False)
        digest.update(np.ascontiguousarray(hashes).tobytes())
    return digest.hexdigest()


def runtime_thread_snapshot() -> dict[str, Any]:
    """Return portable thread diagnostics without changing runtime state."""
    snapshot: dict[str, Any] = {
        "configured_cpu_threads": CPU_THREADS,
        "torch_interop_threads": TORCH_INTEROP_THREADS,
        "process_id": os.getpid(),
        "calling_thread_affinity": None,
        "native_threadpools": [],
    }
    get_affinity = getattr(os, "sched_getaffinity", None)
    if get_affinity is not None:
        snapshot["calling_thread_affinity"] = sorted(get_affinity(0))
    try:
        from threadpoolctl import threadpool_info
    except ImportError:
        snapshot["threadpoolctl_available"] = False
    else:
        snapshot["threadpoolctl_available"] = True
        snapshot["native_threadpools"] = threadpool_info()
    return snapshot


# Temporal metadata is scoped to one public operation, never process-global.


@dataclass(frozen=True)
class TemporalMetadata:
    """Row-level times, supplied by the caller from auditable source lineage.

    The feature timestamp must be the latest availability of every input to
    that row. Label-end is also the label's availability time; if publication
    is delayed, supply the later availability time. Metadata may cover a
    superset of the rows used by nested walk-forward operations.
    """
    label_end: pd.Series
    feature_available_at: pd.Series


_TEMPORAL_METADATA = ContextVar("futures_research_temporal_metadata", default=None)
_FIT_AS_OF = ContextVar("futures_research_fit_as_of", default=None)


def temporal_operation(function):
    """Require label_end= and feature_available_at= Series on outer calls.

    Nested calls inherit the same metadata. This validates declared timestamps;
    it cannot establish that the caller's timestamps or feature lineage are true.
    """
    @wraps(function)
    def wrapped(*args, **kwargs):
        label_end = kwargs.pop("label_end", None)
        available = kwargs.pop("feature_available_at", None)
        inherited = _TEMPORAL_METADATA.get()
        if (label_end is None) != (available is None):
            raise ValueError("provide both label_end and feature_available_at")
        if label_end is None and inherited is None:
            raise ValueError("label_end and feature_available_at are required; temporal checks fail closed")
        if label_end is not None:
            for name, values in (("label_end", label_end), ("feature_available_at", available)):
                if not isinstance(values, pd.Series) or not values.index.is_unique:
                    raise ValueError(f"{name} must be a uniquely indexed pandas Series")
            token = _TEMPORAL_METADATA.set(TemporalMetadata(label_end, available))
            try:
                return function(*args, **kwargs)
            finally:
                _TEMPORAL_METADATA.reset(token)
        return function(*args, **kwargs)
    return wrapped


def temporal_metadata_for(index):
    """Validate and return exact-aligned row metadata for an input slice."""
    from .factor_sampling import validate_point_in_time
    metadata = _TEMPORAL_METADATA.get()
    if metadata is None:
        raise ValueError("temporal metadata missing; use the public temporal_operation entry point")
    for name, series in (("label_end", metadata.label_end), ("feature_available_at", metadata.feature_available_at)):
        if not index.isin(series.index).all():
            raise ValueError(f"{name} does not cover every requested row")
    ends = metadata.label_end.loc[index]
    availability = metadata.feature_available_at.loc[index]
    validate_point_in_time(index, feature_available_at=availability, label_end=ends)
    return pd.DatetimeIndex(ends), {
        "label_end_sha256": series_fingerprint(ends),
        "feature_available_at_sha256": series_fingerprint(availability),
        "point_in_time_metadata_checked": True,
        "metadata_truth_externally_verified": False,
    }


def temporal_fit_cutoff(default):
    return pd.Timestamp(_FIT_AS_OF.get() or default)


@contextmanager
def fit_as_of(timestamp):
    token = _FIT_AS_OF.set(pd.Timestamp(timestamp))
    try:
        yield
    finally:
        _FIT_AS_OF.reset(token)


def require_plan_frozen_before(plans_frozen_at, prediction_start):
    """Check a caller-declared selection cutoff; this is not provenance proof."""
    if plans_frozen_at is None or pd.isna(pd.Timestamp(plans_frozen_at)):
        raise ValueError("plans_frozen_at is required to bound hyperparameter selection")
    if pd.Timestamp(plans_frozen_at) >= pd.Timestamp(prediction_start):
        raise ValueError("plans_frozen_at must precede the first prediction; future-informed plans are forbidden")


def validate_preprocessing_boundary(frame, cutoff):
    """Reject known transforms fitted using data at/after a prediction boundary.

    Absence of attrs is not evidence of raw/PIT data; source lineage remains a
    caller responsibility. Published processing/PCA modules attach these attrs.
    """
    cutoff = pd.Timestamp(cutoff)
    for prefix in ("processing", "compression"):
        method = frame.attrs.get(f"{prefix}_method")
        if method not in (None, False, "none", "raw"):
            fit_end = frame.attrs.get(f"{prefix}_fit_end")
            if fit_end is None or pd.isna(pd.Timestamp(fit_end)):
                raise ValueError(f"{prefix} transform lacks fit-end provenance")
            if pd.Timestamp(fit_end) >= cutoff:
                raise ValueError(f"{prefix} was fitted at/after the prediction cutoff; fit transforms within each fold")
