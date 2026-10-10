"""Leakage-safe factor preprocessing utilities for Jupyter model training.

The public entry point is :func:`process_factor_data`.

Current method
--------------
``zscore``
    Estimate one mean and standard deviation per factor using rows in the
    configured Fit interval, then apply those frozen parameters to every row
    in ``x`` (including Validation and Test). ``y`` is never transformed.

Expected data contract
----------------------
* ``x`` is a pandas DataFrame; every column is a numeric factor.
* ``y`` is a pandas Series or DataFrame.
* Both objects have the same unique MultiIndex, in the exact order
  ``Date, Contract, Datetime``.
* The original ``x`` and ``y`` objects are not modified.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Callable, Mapping, Optional, Union

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype


__all__ = [
    "EXPECTED_INDEX_NAMES",
    "ProcessingResult",
    "ZScoreState",
    "available_processing_methods",
    "fit_zscore_state",
    "process_factor_data",
    "register_processing_method",
    "transform_zscore",
    "validate_processing_inputs",
]

__version__ = "1.1.0"

EXPECTED_INDEX_NAMES = ("Date", "Contract", "Datetime")
Target = Union[pd.Series, pd.DataFrame]


@dataclass(frozen=True)
class ZScoreState:
    """Frozen Fit-period parameters used by the Z-Score transformation."""

    fit_start: pd.Timestamp
    fit_end: pd.Timestamp
    ddof: int
    mean: pd.Series
    std: pd.Series
    valid_count: pd.Series
    invalid_factors: tuple[Any, ...]
    fingerprint: str

    def as_frame(self) -> pd.DataFrame:
        """Return the per-factor Fit statistics as an audit table."""
        table = pd.concat(
            [self.mean, self.std, self.valid_count],
            axis=1,
        )
        table["is_valid"] = ~table.index.isin(self.invalid_factors)
        return table


@dataclass(frozen=True)
class ProcessingResult:
    """Standard output returned by every registered processing method."""

    x: pd.DataFrame
    y: Target
    method: Optional[str]
    executed: bool
    state: Optional[Any]
    audit: Mapping[str, Any]


def validate_processing_inputs(x: pd.DataFrame, y: Target) -> pd.DatetimeIndex:
    """Validate the x/y data contract without silently repairing misalignment."""
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
            raise ValueError(
                f"{name} contains duplicate Date + Contract + Datetime keys"
            )

    if not x.index.equals(y.index):
        raise ValueError(
            "x and y MultiIndex values/order differ. This library never takes an "
            "intersection or reindexes automatically because that could hide label "
            "misalignment."
        )
    if not x.columns.is_unique:
        duplicated = x.columns[x.columns.duplicated()].tolist()
        raise ValueError(f"x contains duplicate factor columns: {duplicated[:20]}")

    non_numeric = [column for column in x.columns if not is_numeric_dtype(x[column])]
    if non_numeric:
        raise TypeError(f"x contains non-numeric factor columns: {non_numeric[:20]}")

    datetimes = pd.DatetimeIndex(x.index.get_level_values("Datetime"))
    if datetimes.isna().any():
        raise ValueError("Datetime index contains NaT")
    return datetimes


def _normalise_interval(
    fit_start: Union[str, pd.Timestamp],
    fit_end: Union[str, pd.Timestamp],
) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(fit_start)
    end = pd.Timestamp(fit_end)
    if pd.isna(start) or pd.isna(end):
        raise ValueError("fit_start and fit_end must be finite timestamps")
    if start > end:
        raise ValueError(f"fit_start must not be after fit_end: {start} > {end}")
    return start, end


def _state_fingerprint(
    statistics: pd.DataFrame,
    fit_start: pd.Timestamp,
    fit_end: pd.Timestamp,
    ddof: int,
) -> str:
    payload = pd.util.hash_pandas_object(statistics, index=True).values.tobytes()
    payload += f"|{fit_start.isoformat()}|{fit_end.isoformat()}|{ddof}".encode()
    return sha256(payload).hexdigest()


def fit_zscore_state(
    x: pd.DataFrame,
    *,
    fit_start: Union[str, pd.Timestamp],
    fit_end: Union[str, pd.Timestamp],
    ddof: int = 0,
) -> ZScoreState:
    """Estimate per-factor mean/std using only rows in the Fit interval.

    ``+/-inf`` is treated as missing for estimation. Pandas/NumPy's ordinary
    missing-value convention is preserved; missing values do not contribute to
    the statistics.
    """
    # Direct fitting must enforce the same schema as the combined entry point.
    datetimes = validate_processing_inputs(x, x)
    if isinstance(ddof, bool) or not isinstance(ddof, (int, np.integer)) or ddof < 0:
        raise ValueError("ddof must be a non-negative integer")

    start, end = _normalise_interval(fit_start, fit_end)
    fit_mask = (datetimes >= start) & (datetimes <= end)
    if not bool(fit_mask.any()):
        raise ValueError(f"x has no rows inside the Fit interval {start} -> {end}")

    columns = x.columns
    factor_count = len(columns)
    means = np.full(factor_count, np.nan, dtype=np.float64)
    stds = np.full(factor_count, np.nan, dtype=np.float64)
    counts = np.zeros(factor_count, dtype=np.int64)

    # Work one column at a time so the large factor matrix is not copied merely
    # to replace infinities before calculating Fit statistics.
    for position, factor in enumerate(columns):
        values = x[factor].to_numpy(dtype=np.float64, na_value=np.nan, copy=True)
        values[~np.isfinite(values)] = np.nan
        fit_values = values[fit_mask]
        finite = fit_values[np.isfinite(fit_values)]
        counts[position] = finite.size
        if finite.size > ddof:
            means[position] = np.mean(finite, dtype=np.float64)
            stds[position] = np.std(finite, ddof=ddof, dtype=np.float64)

    mean = pd.Series(means, index=columns, name="fit_mean")
    std = pd.Series(stds, index=columns, name="fit_std")
    valid_count = pd.Series(counts, index=columns, name="fit_valid_count")
    valid = mean.notna() & std.notna() & np.isfinite(mean) & np.isfinite(std) & (std > 0)
    invalid_factors = tuple(columns[~valid])

    statistics = pd.concat([mean, std, valid_count], axis=1)
    fingerprint = _state_fingerprint(statistics, start, end, ddof)
    return ZScoreState(
        fit_start=start,
        fit_end=end,
        ddof=ddof,
        mean=mean,
        std=std,
        valid_count=valid_count,
        invalid_factors=invalid_factors,
        fingerprint=fingerprint,
    )


def transform_zscore(
    x: pd.DataFrame,
    state: ZScoreState,
    *,
    output_dtype: Union[str, np.dtype] = "float32",
    invalid_policy: str = "set_nan",
    column_batch_size: int = 64,
) -> pd.DataFrame:
    """Apply frozen Fit statistics to all rows of x without refitting.

    Parameters
    ----------
    invalid_policy:
        ``"set_nan"`` makes a factor that could not be estimated from Fit an
        all-NaN output column. ``"raise"`` stops before transformation.
    column_batch_size:
        Number of columns transformed at a time. This controls temporary-memory
        use; it does not change the result.
    """
    if not isinstance(state, ZScoreState):
        raise TypeError("state must be a ZScoreState")
    validate_processing_inputs(x, x)
    current_fingerprint = _state_fingerprint(
        pd.concat([state.mean, state.std, state.valid_count], axis=1),
        state.fit_start, state.fit_end, state.ddof,
    )
    if current_fingerprint != state.fingerprint:
        raise ValueError("ZScoreState statistics were mutated after fitting")
    if not x.columns.equals(state.mean.index):
        raise ValueError("x factor columns/order do not match the frozen ZScoreState")
    if invalid_policy not in {"set_nan", "raise"}:
        raise ValueError("invalid_policy must be 'set_nan' or 'raise'")
    if column_batch_size <= 0:
        raise ValueError("column_batch_size must be positive")

    dtype = np.dtype(output_dtype)
    if dtype.kind != "f":
        raise TypeError("output_dtype must be a floating-point dtype")
    if state.invalid_factors and invalid_policy == "raise":
        raise ValueError(
            f"{len(state.invalid_factors)} factors cannot be standardized from Fit: "
            f"{list(state.invalid_factors[:20])}"
        )

    row_count, factor_count = x.shape
    output = np.empty((row_count, factor_count), dtype=dtype)
    means = state.mean.to_numpy(dtype=np.float64)
    stds = state.std.to_numpy(dtype=np.float64)
    valid = np.isfinite(means) & np.isfinite(stds) & (stds > 0)

    for start in range(0, factor_count, column_batch_size):
        stop = min(start + column_batch_size, factor_count)
        # Subtract in float64 before casting: float32 can erase small changes
        # around a large raw level and turn a variable factor into a constant.
        values = x.iloc[:, start:stop].to_numpy(dtype=np.float64, na_value=np.nan, copy=True)
        values[~np.isfinite(values)] = np.nan

        batch_valid = valid[start:stop]
        if batch_valid.any():
            values[:, batch_valid] -= means[start:stop][batch_valid]
            values[:, batch_valid] /= stds[start:stop][batch_valid]
        if (~batch_valid).any():
            values[:, ~batch_valid] = np.nan
        output[:, start:stop] = values

    transformed = pd.DataFrame(output, index=x.index, columns=x.columns, copy=False)
    transformed.attrs = x.attrs.copy()
    transformed.attrs.update(
        processing_method="zscore",
        processing_fingerprint=state.fingerprint,
        processing_fit_start=state.fit_start.isoformat(),
        processing_fit_end=state.fit_end.isoformat(),
    )
    return transformed


Processor = Callable[..., ProcessingResult]
_PROCESSING_METHODS: dict[str, Processor] = {}


def register_processing_method(name: str) -> Callable[[Processor], Processor]:
    """Decorator used to add another method to the unified public entry point."""
    normalised = name.strip().lower()
    if not normalised:
        raise ValueError("processing method name cannot be empty")

    def decorator(function: Processor) -> Processor:
        if normalised in _PROCESSING_METHODS:
            raise KeyError(f"processing method is already registered: {normalised}")
        _PROCESSING_METHODS[normalised] = function
        return function

    return decorator


def available_processing_methods() -> tuple[str, ...]:
    """Return currently registered method names."""
    return tuple(sorted(_PROCESSING_METHODS))


@register_processing_method("zscore")
def _process_zscore(
    x: pd.DataFrame,
    y: Target,
    *,
    fit_start: Union[str, pd.Timestamp],
    fit_end: Union[str, pd.Timestamp],
    ddof: int = 0,
    output_dtype: Union[str, np.dtype] = "float32",
    invalid_policy: str = "set_nan",
    column_batch_size: int = 64,
) -> ProcessingResult:
    datetimes = validate_processing_inputs(x, y)
    state = fit_zscore_state(
        x,
        fit_start=fit_start,
        fit_end=fit_end,
        ddof=ddof,
    )
    transformed = transform_zscore(
        x,
        state,
        output_dtype=output_dtype,
        invalid_policy=invalid_policy,
        column_batch_size=column_batch_size,
    )

    fit_mask = (datetimes >= state.fit_start) & (datetimes <= state.fit_end)
    actual_fit = datetimes[fit_mask]
    audit = {
        "library_version": __version__,
        "method": "zscore",
        "executed": True,
        "all_row_count": int(len(x)),
        "fit_row_count": int(fit_mask.sum()),
        "factor_count": int(x.shape[1]),
        "configured_fit_start": state.fit_start.isoformat(),
        "configured_fit_end": state.fit_end.isoformat(),
        "actual_fit_start": actual_fit.min().isoformat(),
        "actual_fit_end": actual_fit.max().isoformat(),
        "ddof": state.ddof,
        "output_dtype": str(np.dtype(output_dtype)),
        "invalid_policy": invalid_policy,
        "invalid_factor_count": len(state.invalid_factors),
        "invalid_factors": [str(factor) for factor in state.invalid_factors],
        "state_fingerprint": state.fingerprint,
        "statistics_scope": "one mean/std per factor over all Fit rows",
        "transformation_scope": "all rows in x",
        "grouped_by_contract": False,
        "x_y_index_exactly_equal": True,
        "y_was_transformed": False,
    }
    return ProcessingResult(
        x=transformed,
        y=y,
        method="zscore",
        executed=True,
        state=state,
        audit=audit,
    )


def process_factor_data(
    x: pd.DataFrame,
    y: Target,
    *,
    method: Optional[str] = "zscore",
    execute: bool = True,
    fit_start: Optional[Union[str, pd.Timestamp]] = None,
    fit_end: Optional[Union[str, pd.Timestamp]] = None,
    method_params: Optional[Mapping[str, Any]] = None,
) -> ProcessingResult:
    """Unified preprocessing entry point intended for Notebook use.

    Examples
    --------
    >>> result = process_factor_data(
    ...     x, y,
    ...     method="zscore",
    ...     execute=True,
    ...     fit_start="2010-05-18",
    ...     fit_end="2020-12-31 23:59:59",
    ...     method_params={"ddof": 0, "output_dtype": "float32"},
    ... )
    >>> x_processed, y_processed = result.x, result.y

    With ``execute=False``, the original object references are returned after
    validation and no Fit statistics are estimated.
    """
    validate_processing_inputs(x, y)

    normalised_method = None if method is None else method.strip().lower()
    if not execute:
        return ProcessingResult(
            x=x,
            y=y,
            method=normalised_method,
            executed=False,
            state=None,
            audit={
                "library_version": __version__,
                "method": normalised_method,
                "executed": False,
                "all_row_count": int(len(x)),
                "factor_count": int(x.shape[1]),
                "x_y_index_exactly_equal": True,
                "y_was_transformed": False,
            },
        )

    if not normalised_method:
        raise ValueError("method must be supplied when execute=True")
    if normalised_method not in _PROCESSING_METHODS:
        raise KeyError(
            f"unknown method {normalised_method!r}; "
            f"available methods: {available_processing_methods()}"
        )
    if fit_start is None or fit_end is None:
        raise ValueError("fit_start and fit_end are required when execute=True")

    parameters = dict(method_params or {})
    forbidden = {"x", "y", "fit_start", "fit_end"}.intersection(parameters)
    if forbidden:
        raise ValueError(
            f"method_params must not override core arguments: {sorted(forbidden)}"
        )

    return _PROCESSING_METHODS[normalised_method](
        x,
        y,
        fit_start=fit_start,
        fit_end=fit_end,
        **parameters,
    )
