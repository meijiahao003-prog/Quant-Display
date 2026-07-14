"""NaN-aware matrix operators shared by the factor implementations."""

from __future__ import annotations

import numpy as np

Array = np.ndarray


def purify(value: Array) -> Array:
    """Convert input to float and replace positive/negative infinity with NaN."""

    out = np.asarray(value, dtype=float).copy()
    out[~np.isfinite(out)] = np.nan
    return out


def safe_divide(numerator: Array, denominator: Array, eps: float = 1e-12) -> Array:
    """Broadcast-aware division that returns NaN for invalid or near-zero denominators."""

    num, den = np.broadcast_arrays(
        np.asarray(numerator, dtype=float), np.asarray(denominator, dtype=float)
    )
    out = np.full(num.shape, np.nan, dtype=float)
    valid = np.isfinite(num) & np.isfinite(den) & (np.abs(den) > eps)
    np.divide(num, den, out=out, where=valid)
    return purify(out)


def intraday_sum(
    value: Array,
    start_bar: int | None = None,
    end_bar: int | None = None,
    min_valid_ratio: float = 0.8,
) -> Array:
    """Aggregate days × bars × assets while enforcing per-day bar coverage."""

    array = purify(value)
    if array.ndim != 3:
        raise ValueError("intraday_sum expects days × bars × assets")
    segment = array[:, start_bar:end_bar, :]
    if segment.shape[1] == 0:
        raise ValueError("The selected intraday segment is empty")
    valid_count = np.sum(np.isfinite(segment), axis=1)
    out = np.sum(np.where(np.isfinite(segment), segment, 0.0), axis=1)
    minimum = int(np.ceil(segment.shape[1] * min_valid_ratio))
    out[valid_count < minimum] = np.nan
    return purify(out)


def ts_shift(value: Array, periods: int = 1) -> Array:
    """Lag a time × assets matrix by a positive number of rows."""

    array = purify(value)
    if periods < 0:
        raise ValueError("periods must be non-negative")
    if periods == 0:
        return array
    out = np.full_like(array, np.nan)
    if periods < array.shape[0]:
        out[periods:] = array[:-periods]
    return out


def _reduce(value: Array, operation: str, min_count: int = 1) -> Array:
    array = purify(value)
    count = np.sum(np.isfinite(array), axis=0, keepdims=True)
    safe = np.where(np.isfinite(array), array, 0.0)
    if operation == "sum":
        out = np.sum(safe, axis=0, keepdims=True)
    elif operation == "mean":
        out = safe_divide(np.sum(safe, axis=0, keepdims=True), count)
    elif operation == "std":
        mean = safe_divide(np.sum(safe, axis=0, keepdims=True), count)
        squared = np.where(np.isfinite(array), (array - mean) ** 2, 0.0)
        out = np.sqrt(safe_divide(np.sum(squared, axis=0, keepdims=True), count))
    else:
        raise ValueError(f"Unknown operation: {operation}")
    out[count < min_count] = np.nan
    return purify(out)


def ts_sum(value: Array, min_count: int = 1) -> Array:
    return _reduce(value, "sum", min_count)


def ts_mean(value: Array, min_count: int = 1) -> Array:
    return _reduce(value, "mean", min_count)


def ts_std(value: Array, min_count: int = 2) -> Array:
    return _reduce(value, "std", min_count)


def ts_skew(value: Array, min_count: int = 5) -> Array:
    """Population skewness along the time axis."""

    array = purify(value)
    count = np.sum(np.isfinite(array), axis=0, keepdims=True)
    mean = ts_mean(array)
    centered = np.where(np.isfinite(array), array - mean, 0.0)
    second = safe_divide(np.sum(centered**2, axis=0, keepdims=True), count)
    third = safe_divide(np.sum(centered**3, axis=0, keepdims=True), count)
    out = safe_divide(third, np.power(second, 1.5))
    out[count < min_count] = np.nan
    return purify(out)


def ts_corr(left: Array, right: Array, min_count: int = 3) -> Array:
    """Pairwise Pearson correlation along the time axis."""

    x, y = np.broadcast_arrays(purify(left), purify(right))
    valid = np.isfinite(x) & np.isfinite(y)
    count = np.sum(valid, axis=0, keepdims=True)
    x_valid = np.where(valid, x, 0.0)
    y_valid = np.where(valid, y, 0.0)
    x_mean = safe_divide(np.sum(x_valid, axis=0, keepdims=True), count)
    y_mean = safe_divide(np.sum(y_valid, axis=0, keepdims=True), count)
    x_centered = np.where(valid, x - x_mean, 0.0)
    y_centered = np.where(valid, y - y_mean, 0.0)
    covariance = np.sum(x_centered * y_centered, axis=0, keepdims=True)
    scale = np.sqrt(
        np.sum(x_centered**2, axis=0, keepdims=True)
        * np.sum(y_centered**2, axis=0, keepdims=True)
    )
    out = safe_divide(covariance, scale)
    out[count < min_count] = np.nan
    return purify(out)


def cs_sum(value: Array, min_count: int = 1) -> Array:
    array = purify(value)
    count = np.sum(np.isfinite(array), axis=1, keepdims=True)
    out = np.sum(np.where(np.isfinite(array), array, 0.0), axis=1, keepdims=True)
    out[count < min_count] = np.nan
    return out


def cs_mean(value: Array, min_count: int = 1) -> Array:
    array = purify(value)
    count = np.sum(np.isfinite(array), axis=1, keepdims=True)
    out = safe_divide(cs_sum(array), count)
    out[count < min_count] = np.nan
    return out


def _average_rank(vector: Array) -> Array:
    """Average-tie ranks in [0, 1], preserving NaNs."""

    values = purify(vector).reshape(-1)
    out = np.full(values.shape, np.nan)
    valid_index = np.flatnonzero(np.isfinite(values))
    if valid_index.size == 0:
        return out
    valid_values = values[valid_index]
    order = np.argsort(valid_values, kind="mergesort")
    sorted_values = valid_values[order]
    ranks = np.empty(valid_values.size, dtype=float)
    start = 0
    while start < sorted_values.size:
        end = start + 1
        while end < sorted_values.size and sorted_values[end] == sorted_values[start]:
            end += 1
        ranks[order[start:end]] = (start + end - 1) / 2.0
        start = end
    if valid_values.size == 1:
        ranks[:] = 0.5
    else:
        ranks /= valid_values.size - 1
    out[valid_index] = ranks
    return out


def cs_percentile_rank(value: Array) -> Array:
    """Percentile ranks within each date/cross-section."""

    array = purify(value)
    return np.vstack([_average_rank(row) for row in array])


def ts_percentile_rank_full(value: Array) -> Array:
    """Percentile ranks through time for every asset, preserving the original shape."""

    array = purify(value)
    return np.column_stack([_average_rank(array[:, column]) for column in range(array.shape[1])])


def cs_zscore(value: Array) -> Array:
    """Cross-sectional z-score for each row."""

    array = purify(value)
    mean = cs_mean(array)
    count = np.sum(np.isfinite(array), axis=1, keepdims=True)
    centered = np.where(np.isfinite(array), array - mean, 0.0)
    std = np.sqrt(safe_divide(np.sum(centered**2, axis=1, keepdims=True), count))
    return safe_divide(array - mean, std)


def cross_sectional_residual(
    y: Array, exposures: list[Array], intercept: bool = True
) -> Array:
    """Residualize each cross-section with ordinary least squares.

    The function intentionally leaves assets with incomplete inputs as NaN. It is suitable for
    research prototyping, not for large production optimizers.
    """

    target = purify(y)
    if not exposures:
        return target
    xs = [np.broadcast_to(purify(x), target.shape) for x in exposures]
    out = np.full_like(target, np.nan)
    for row in range(target.shape[0]):
        columns = [x[row] for x in xs]
        valid = np.isfinite(target[row])
        for column in columns:
            valid &= np.isfinite(column)
        design_parts = [column[valid] for column in columns]
        if intercept:
            design_parts.insert(0, np.ones(np.sum(valid)))
        n_parameters = len(design_parts)
        if np.sum(valid) <= n_parameters:
            continue
        design = np.column_stack(design_parts)
        coefficients, *_ = np.linalg.lstsq(design, target[row, valid], rcond=None)
        out[row, valid] = target[row, valid] - design @ coefficients
    return purify(out)


def time_series_residual(y: Array, exposures: list[Array], intercept: bool = True) -> Array:
    """Return per-asset time-series regression residuals."""

    target = purify(y)
    if not exposures:
        return target
    xs = [np.broadcast_to(purify(x), target.shape) for x in exposures]
    out = np.full_like(target, np.nan)
    for asset in range(target.shape[1]):
        columns = [x[:, asset] for x in xs]
        valid = np.isfinite(target[:, asset])
        for column in columns:
            valid &= np.isfinite(column)
        design_parts = [column[valid] for column in columns]
        if intercept:
            design_parts.insert(0, np.ones(np.sum(valid)))
        n_parameters = len(design_parts)
        if np.sum(valid) <= n_parameters:
            continue
        design = np.column_stack(design_parts)
        coefficients, *_ = np.linalg.lstsq(design, target[valid, asset], rcond=None)
        out[valid, asset] = target[valid, asset] - design @ coefficients
    return purify(out)
