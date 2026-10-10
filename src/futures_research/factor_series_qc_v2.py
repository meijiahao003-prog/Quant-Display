"""Descriptive factor quality checks and causal prefix regression diagnostics.

Full-sample summaries are retrospective diagnostics: do not select features or
fit transformations from validation/test summaries. A passing prefix check is
evidence for tested inputs/cuts only, not proof of point-in-time source data.
"""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

ArrayLike = Sequence[float] | np.ndarray | pd.Series


@dataclass(frozen=True)
class QCThresholds:
    finite_ratio_warning: float = 0.80
    unique_warning: int = 10
    unique_error: int = 3
    unchanged_ratio_warning: float = 0.20
    autocorr_warning: float = 0.995
    robust_z_threshold: float = 10.0
    robust_outlier_ratio_warning: float = 0.01
    jump_multiple: float = 20.0
    jump_ratio_warning: float = 0.02
    jump_expected_range_fraction: float = 0.05
    support_threshold: float = 0.10
    zero_tol: float = 1e-14
    unchanged_tol: float = 1e-14


DEFAULT_THRESHOLDS = QCThresholds()


def _copy_index(index):
    """Copy an index without collapsing a MultiIndex into object tuples."""
    if index is None:
        return None
    if isinstance(index, pd.MultiIndex):
        return index.copy()
    return pd.Index(index).copy()


def _as_series(x: ArrayLike, index=None, name: str = "factor") -> pd.Series:
    if isinstance(x, pd.Series):
        s = x.astype(float).copy()
        if index is not None:
            if len(index) != len(s):
                raise ValueError("index length must equal factor length")
            s.index = _copy_index(index)
        s.name = s.name or name
        return s
    a = np.asarray(x, dtype=float)
    if a.ndim != 1:
        raise ValueError(f"factor_values must be 1D, got {a.shape}")
    if index is not None and len(index) != len(a):
        raise ValueError("index length must equal factor length")
    return pd.Series(a, index=_copy_index(index), name=name)


def _as_optional_array(x: Optional[ArrayLike], n: int, dtype=float) -> Optional[np.ndarray]:
    if x is None:
        return None
    a = np.asarray(x, dtype=dtype)
    if a.ndim != 1 or len(a) != n:
        raise ValueError("optional array must be 1D and match factor length")
    return a


def _longest_run(mask: np.ndarray, segment: Optional[np.ndarray] = None) -> tuple[int, int, int]:
    best = cur = 0
    best_start = best_end = cur_start = -1
    prev_segment = None
    for i, v in enumerate(mask):
        seg_changed = segment is not None and i > 0 and segment[i] != prev_segment
        if seg_changed:
            cur = 0
            cur_start = -1
        if v:
            if cur == 0:
                cur_start = i
            cur += 1
            if cur > best:
                best, best_start, best_end = cur, cur_start, i
        else:
            cur = 0
            cur_start = -1
        if segment is not None:
            prev_segment = segment[i]
    return best, best_start, best_end


def _longest_unchanged(x: np.ndarray, atol: float, segment: Optional[np.ndarray] = None) -> tuple[int, int, int]:
    best = cur = 0
    best_start = best_end = cur_start = -1
    prev = np.nan
    prev_segment = None
    for i, v in enumerate(x):
        seg_changed = segment is not None and i > 0 and segment[i] != prev_segment
        if seg_changed or not np.isfinite(v):
            cur = 0
            cur_start = -1
            prev = np.nan
        if np.isfinite(v):
            if cur == 0:
                cur, cur_start = 1, i
            elif np.isfinite(prev) and abs(v - prev) <= atol:
                cur += 1
            else:
                cur, cur_start = 1, i
            if cur > best:
                best, best_start, best_end = cur, cur_start, i
            prev = v
        if segment is not None:
            prev_segment = segment[i]
    return best, best_start, best_end


def _pair_mask(x: np.ndarray, lag: int, segment: Optional[np.ndarray], support_mask: Optional[np.ndarray]) -> np.ndarray:
    if lag <= 0 or lag >= len(x):
        return np.zeros(0, dtype=bool)
    mask = np.isfinite(x[:-lag]) & np.isfinite(x[lag:])
    if segment is not None:
        # Endpoint equality alone can bridge intervening session boundaries.
        run_ids = np.r_[0, np.cumsum(segment[1:] != segment[:-1])]
        mask &= run_ids[:-lag] == run_ids[lag:]
    if support_mask is not None:
        mask &= support_mask[:-lag] & support_mask[lag:]
    return mask


def _autocorr(x: np.ndarray, lag: int, segment: Optional[np.ndarray] = None, support_mask: Optional[np.ndarray] = None) -> float:
    mask = _pair_mask(x, lag, segment, support_mask)
    if mask.size == 0 or mask.sum() < 3:
        return np.nan
    a, b = x[:-lag][mask], x[lag:][mask]
    if a.std() == 0 or b.std() == 0:
        return np.nan
    return float(np.corrcoef(a, b)[0, 1])


def _robust_scale(x: np.ndarray, expected_range: Optional[tuple[float, float]]) -> tuple[float, float, float]:
    if len(x) == 0:
        return np.nan, np.nan, np.nan
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med)))
    q25, q75 = np.quantile(x, [0.25, 0.75])
    iqr_scale = float((q75 - q25) / 1.349) if q75 > q25 else 0.0
    std_floor = float(np.std(x)) * 0.05
    range_floor = 0.0
    if expected_range is not None:
        lo, hi = expected_range
        if np.isfinite(lo) and np.isfinite(hi) and hi > lo:
            range_floor = (hi - lo) * 1e-4
    scale = max(1.4826 * mad, iqr_scale, std_floor, range_floor, 1e-12)
    return med, mad, scale


def _segmented_robust_outlier_ratio(
    x: np.ndarray,
    threshold: float,
    segment: Optional[np.ndarray],
    expected_range: Optional[tuple[float, float]],
) -> float:
    if len(x) == 0:
        return np.nan
    if segment is None:
        med, _, scale = _robust_scale(x, expected_range)
        return float(np.mean(np.abs((x - med) / scale) > threshold))
    total = outliers = 0
    for seg in np.unique(segment):
        values = x[segment == seg]
        values = values[np.isfinite(values)]
        if len(values) < 5:
            continue
        med, _, scale = _robust_scale(values, expected_range)
        outliers += int(np.sum(np.abs((values - med) / scale) > threshold))
        total += len(values)
    return float(outliers / total) if total else np.nan


def _infer_expected_range(expected_range: Optional[tuple[float, float]], expected_lower, expected_upper):
    if expected_range is not None:
        return expected_range
    if expected_lower is None or expected_upper is None:
        return None
    if not np.isfinite(expected_lower) or not np.isfinite(expected_upper):
        return None
    return float(expected_lower), float(expected_upper)


def factor_qc_summary(
    factor_values: ArrayLike,
    index=None,
    factor_name: str = "factor",
    expected_warmup: Optional[int] = None,
    zero_tol: float = 1e-14,
    unchanged_tol: float = 1e-14,
    autocorr_lags: Sequence[int] = (1, 2, 5, 10, 15, 30, 60),
    *,
    factor_type: str = "event_flow",
    session_segment: Optional[ArrayLike] = None,
    session_name: Optional[Sequence[Any]] = None,
    support_values: Optional[ArrayLike] = None,
    support_threshold: float = 0.10,
    expected_range: Optional[tuple[float, float]] = None,
    robust_z_threshold: float = 10.0,
    jump_multiple: float = 20.0,
    jump_expected_range_fraction: float = 0.05,
) -> Dict[str, Any]:
    s = _as_series(factor_values, index, factor_name)
    x = s.to_numpy(dtype=float)
    n = len(x)
    segment = _as_optional_array(session_segment, n, dtype=np.int64)
    support = _as_optional_array(support_values, n, dtype=float)
    support_mask = None if support is None else np.isfinite(support) & (support >= support_threshold)

    finite_mask = np.isfinite(x)
    finite = x[finite_mask]
    analysis_mask = finite_mask.copy()
    if support_mask is not None:
        analysis_mask &= support_mask
    analyzed = x[analysis_mask]

    miss_run = _longest_run(~finite_mask, segment)
    finite_run = _longest_run(finite_mask, segment)
    unchanged_values = x.copy()
    if support_mask is not None:
        unchanged_values[~support_mask] = np.nan
    unchanged_run = _longest_unchanged(unchanged_values, unchanged_tol, segment)

    out: Dict[str, Any] = {
        "factor_name": factor_name,
        "factor_type": factor_type,
        "n_total": n,
        "n_finite": int(finite.size),
        "n_analyzed": int(analyzed.size),
        "n_supported": int(support_mask.sum()) if support_mask is not None else n,
        "support_ratio": float(support_mask.mean()) if support_mask is not None and n else 1.0,
        "support_threshold": support_threshold,
        "n_nan": int(np.isnan(x).sum()),
        "n_posinf": int(np.isposinf(x).sum()),
        "n_neginf": int(np.isneginf(x).sum()),
        "finite_ratio": float(finite.size / n) if n else np.nan,
        "missing_ratio": float(1 - finite.size / n) if n else np.nan,
        "n_unique_finite": int(np.unique(finite).size) if finite.size else 0,
        "n_unique_analyzed": int(np.unique(analyzed).size) if analyzed.size else 0,
        "zero_ratio_finite": float(np.mean(np.abs(finite) <= zero_tol)) if finite.size else np.nan,
        "zero_ratio_analyzed": float(np.mean(np.abs(analyzed) <= zero_tol)) if analyzed.size else np.nan,
        "positive_ratio_analyzed": float(np.mean(analyzed > zero_tol)) if analyzed.size else np.nan,
        "negative_ratio_analyzed": float(np.mean(analyzed < -zero_tol)) if analyzed.size else np.nan,
        "longest_missing_run": miss_run[0],
        "longest_missing_start": miss_run[1],
        "longest_missing_end": miss_run[2],
        "longest_finite_run": finite_run[0],
        "longest_unchanged_run": unchanged_run[0],
        "longest_unchanged_start": unchanged_run[1],
        "longest_unchanged_end": unchanged_run[2],
        "expected_warmup": expected_warmup,
    }

    target = analyzed if support_mask is not None else finite
    if target.size:
        q = np.quantile(target, [0, .001, .01, .05, .25, .5, .75, .95, .99, .999, 1])
        med, mad, robust_scale = _robust_scale(target, expected_range)
        std = float(np.std(target))
        z = (target - target.mean()) / std if std > 0 else np.zeros_like(target)
        robust_z = (target - med) / robust_scale
        target_segment = segment[analysis_mask] if segment is not None and analyzed.size else (segment[finite_mask] if segment is not None else None)
        global_ratio_z5 = float(np.mean(np.abs(robust_z) > 5))
        global_ratio_z10 = float(np.mean(np.abs(robust_z) > 10))
        segmented_ratio_z5 = _segmented_robust_outlier_ratio(target, 5.0, target_segment, expected_range)
        segmented_ratio_z10 = _segmented_robust_outlier_ratio(target, 10.0, target_segment, expected_range)
        segmented_ratio_threshold = _segmented_robust_outlier_ratio(target, robust_z_threshold, target_segment, expected_range)
        out.update({
            "mean": float(np.mean(target)), "std": std,
            "min": float(q[0]), "q001": float(q[1]), "q01": float(q[2]),
            "q05": float(q[3]), "q25": float(q[4]), "median": float(q[5]),
            "q75": float(q[6]), "q95": float(q[7]), "q99": float(q[8]),
            "q999": float(q[9]), "max": float(q[10]), "mad": mad,
            "robust_scale": robust_scale,
            "skew": float(np.mean(z ** 3)) if std > 0 else 0.0,
            "excess_kurtosis": float(np.mean(z ** 4) - 3) if std > 0 else 0.0,
            "global_robust_outlier_ratio_z5": global_ratio_z5,
            "global_robust_outlier_ratio_z10": global_ratio_z10,
            "robust_outlier_ratio_z5": segmented_ratio_z5,
            "robust_outlier_ratio_z10": segmented_ratio_z10,
            "robust_outlier_ratio_threshold": segmented_ratio_threshold,
            "robust_z_threshold": robust_z_threshold,
        })
    else:
        for k in ("mean", "std", "min", "q001", "q01", "q05", "q25", "median", "q75", "q95", "q99", "q999", "max", "mad", "robust_scale", "skew", "excess_kurtosis", "global_robust_outlier_ratio_z5", "global_robust_outlier_ratio_z10", "robust_outlier_ratio_z5", "robust_outlier_ratio_z10", "robust_outlier_ratio_threshold", "robust_z_threshold"):
            out[k] = np.nan

    for lag in autocorr_lags:
        out[f"autocorr_lag_{lag}"] = _autocorr(x, int(lag), segment, support_mask)

    if len(x) >= 2:
        pair = _pair_mask(x, 1, segment, support_mask)
        all_finite_pair = np.isfinite(x[:-1]) & np.isfinite(x[1:])
        boundary_pairs = all_finite_pair.copy()
        if segment is not None:
            boundary_pairs &= segment[:-1] != segment[1:]
        else:
            boundary_pairs[:] = False
        diff = np.abs(x[1:] - x[:-1])
        valid_diff = diff[pair]
        if valid_diff.size:
            med_ad = float(np.median(valid_diff))
            q01, q99 = np.quantile(target, [0.01, 0.99]) if len(target) >= 3 else (0.0, 0.0)
            data_scale = max(float(q99 - q01), float(out.get("robust_scale", 0.0)), 1e-12)
            absolute_floor = jump_expected_range_fraction * data_scale
            if expected_range is not None:
                lo, hi = expected_range
                if np.isfinite(lo) and np.isfinite(hi) and hi > lo:
                    absolute_floor = max(absolute_floor, jump_expected_range_fraction * (hi - lo))
            threshold = max(jump_multiple * med_ad, absolute_floor)
            out["median_abs_diff"] = med_ad
            out["max_abs_diff"] = float(valid_diff.max())
            out["jump_absolute_threshold"] = threshold
            out["jump_ratio"] = float(np.mean(valid_diff > threshold))
            out["n_valid_diff"] = int(valid_diff.size)
        else:
            out["median_abs_diff"] = out["max_abs_diff"] = out["jump_absolute_threshold"] = out["jump_ratio"] = np.nan
            out["n_valid_diff"] = 0
        out["excluded_session_boundary_pairs"] = int(boundary_pairs.sum())
    else:
        out["median_abs_diff"] = out["max_abs_diff"] = out["jump_absolute_threshold"] = out["jump_ratio"] = np.nan
        out["n_valid_diff"] = out["excluded_session_boundary_pairs"] = 0

    if expected_range is not None and finite.size:
        lo, hi = expected_range
        tol = max(abs(hi - lo), 1.0) * 1e-10
        out["below_expected_count"] = int(np.sum(finite < lo - tol))
        out["above_expected_count"] = int(np.sum(finite > hi + tol))
    else:
        out["below_expected_count"] = out["above_expected_count"] = 0

    return out


def factor_qc_warnings(summary: Mapping[str, Any], thresholds: QCThresholds = DEFAULT_THRESHOLDS) -> list[str]:
    w: list[str] = []
    n = int(summary.get("n_total", 0))
    factor_type = str(summary.get("factor_type", "event_flow"))
    if n == 0:
        return ["ERROR: empty factor series"]
    if summary.get("n_finite", 0) == 0:
        return ["ERROR: no finite values"]
    if summary.get("n_posinf", 0) or summary.get("n_neginf", 0):
        w.append("ERROR: factor contains +/-inf")
    if summary.get("finite_ratio", 0.0) < thresholds.finite_ratio_warning:
        w.append(f"WARNING: finite coverage is low ({summary['finite_ratio']:.2%})")
    if summary.get("below_expected_count", 0) or summary.get("above_expected_count", 0):
        w.append("ERROR: values exceed the declared expected range")

    unique = int(summary.get("n_unique_analyzed", summary.get("n_unique_finite", 0)))
    if factor_type not in {"purity", "support"}:
        if unique < thresholds.unique_error:
            w.append(f"ERROR: only {unique} unique analyzed values")
        elif unique < thresholds.unique_warning:
            w.append(f"WARNING: only {unique} unique analyzed values")
    elif unique == 1:
        # Constant purity/support factors are still caught by the variance rule below.
        pass

    std = summary.get("std", np.nan)
    if np.isfinite(std) and std <= 1e-15:
        w.append("ERROR: factor is constant or numerically degenerate")

    zero_ratio = summary.get("zero_ratio_analyzed", summary.get("zero_ratio_finite", np.nan))
    if np.isfinite(zero_ratio):
        if factor_type in {"state_level", "lifecycle", "response"} and zero_ratio >= 0.95:
            w.append("WARNING: at least 95% of supported values are zero")
        elif factor_type not in {"support", "purity", "interaction", "dynamic"} and zero_ratio >= 0.99:
            w.append("WARNING: at least 99% of supported values are zero")

    unchanged_ratio = summary.get("longest_unchanged_run", 0) / max(n, 1)
    if unchanged_ratio >= thresholds.unchanged_ratio_warning:
        if factor_type == "state_level":
            if unique < max(20, n // 10):
                w.append("WARNING: persistent state has a long unchanged segment and low value diversity")
        elif factor_type not in {"support", "purity"}:
            w.append("WARNING: a long near-constant segment exists inside a session segment")

    warmup = summary.get("expected_warmup")
    if warmup is not None and summary.get("longest_missing_start") == 0 and summary.get("longest_missing_run", 0) > warmup:
        w.append(f"WARNING: leading NaN run {summary['longest_missing_run']} exceeds expected warmup {warmup}")

    ac1 = summary.get("autocorr_lag_1", np.nan)
    if np.isfinite(ac1) and abs(ac1) >= thresholds.autocorr_warning:
        if factor_type in {"event_flow", "dynamic", "response"}:
            w.append(f"WARNING: extreme within-segment lag-1 autocorrelation ({ac1:.6f})")
        elif factor_type == "state_level" and unchanged_ratio >= thresholds.unchanged_ratio_warning:
            w.append(f"WARNING: persistent state is both highly autocorrelated ({ac1:.6f}) and unusually unchanged")

    outlier_ratio = summary.get("robust_outlier_ratio_threshold", summary.get("robust_outlier_ratio_z10", 0.0))
    if np.isfinite(outlier_ratio) and outlier_ratio > thresholds.robust_outlier_ratio_warning:
        if factor_type in {"event_flow", "dynamic", "lifecycle", "response"}:
            w.append(f"WARNING: more than 1% of supported within-segment values exceed robust |z| > {summary.get('robust_z_threshold', 10):g}")
        elif factor_type == "state_level" and outlier_ratio > 0.05:
            w.append(f"WARNING: more than 5% of supported within-segment state values exceed robust |z| > {summary.get('robust_z_threshold', 10):g}")

    jump_ratio = summary.get("jump_ratio", 0.0)
    if np.isfinite(jump_ratio) and jump_ratio > thresholds.jump_ratio_warning:
        if factor_type in {"event_flow", "dynamic", "lifecycle", "response"}:
            w.append("WARNING: frequent within-segment jumps exceed both relative and absolute thresholds")
        elif factor_type == "interaction" and jump_ratio > 0.10:
            w.append("WARNING: interaction factor jumps frequently even after support filtering")

    support_ratio = summary.get("support_ratio", 1.0)
    if factor_type not in {"support", "state_level"} and np.isfinite(support_ratio) and support_ratio < 0.05:
        w.append(f"INFO: factor has limited effective support ({support_ratio:.2%}); evaluate conditionally")

    return w or ["PASS: no major structural problems detected"]


def rolling_factor_qc(
    factor_values: ArrayLike,
    index=None,
    factor_name: str = "factor",
    window: int = 240,
    min_periods: Optional[int] = None,
    zero_tol: float = 1e-14,
    support_values: Optional[ArrayLike] = None,
    support_threshold: float = 0.10,
) -> pd.DataFrame:
    s = _as_series(factor_values, index, factor_name).replace([np.inf, -np.inf], np.nan)
    if min_periods is None:
        min_periods = max(3, window // 3)
    r = s.rolling(window, min_periods=min_periods)
    out = pd.DataFrame(index=s.index)
    out["count"] = r.count()
    out["mean"] = r.mean()
    out["std"] = r.std(ddof=0)
    out["min"] = r.min()
    out["median"] = r.median()
    out["max"] = r.max()
    out["finite_ratio"] = np.isfinite(s).astype(float).rolling(window, min_periods=min_periods).mean()
    out["zero_ratio"] = ((s.abs() <= zero_tol) & s.notna()).astype(float).rolling(window, min_periods=min_periods).mean()
    if support_values is not None:
        support = _as_series(support_values, s.index, "support")
        out["support_ratio"] = (support >= support_threshold).astype(float).rolling(window, min_periods=min_periods).mean()
    return out


def block_factor_qc(
    factor_values: ArrayLike,
    index=None,
    factor_name: str = "factor",
    block_size: int = 240,
    zero_tol: float = 1e-14,
) -> pd.DataFrame:
    s = _as_series(factor_values, index, factor_name)
    rows = []
    for block_id, start in enumerate(range(0, len(s), block_size)):
        block = s.iloc[start:start + block_size]
        f = block[np.isfinite(block)]
        rows.append({
            "block_id": block_id,
            "start": block.index[0] if len(block) else None,
            "end": block.index[-1] if len(block) else None,
            "n_total": len(block),
            "n_finite": len(f),
            "finite_ratio": len(f) / len(block) if len(block) else np.nan,
            "mean": float(f.mean()) if len(f) else np.nan,
            "std": float(f.std(ddof=0)) if len(f) else np.nan,
            "median": float(f.median()) if len(f) else np.nan,
            "zero_ratio": float((f.abs() <= zero_tol).mean()) if len(f) else np.nan,
            "positive_ratio": float((f > zero_tol).mean()) if len(f) else np.nan,
            "negative_ratio": float((f < -zero_tol).mean()) if len(f) else np.nan,
        })
    return pd.DataFrame(rows)


def session_factor_qc(
    factor_values: ArrayLike,
    session_segment: ArrayLike,
    session_name: Optional[Sequence[Any]] = None,
    index=None,
    factor_name: str = "factor",
    zero_tol: float = 1e-14,
) -> pd.DataFrame:
    s = _as_series(factor_values, index, factor_name)
    segment = _as_optional_array(session_segment, len(s), dtype=np.int64)
    names = np.asarray(session_name, dtype=object) if session_name is not None else None
    if names is not None and len(names) != len(s):
        raise ValueError("session_name length must equal factor length")
    rows = []
    for seg in pd.unique(segment):
        mask = segment == seg
        values = s.to_numpy(float)[mask]
        finite = values[np.isfinite(values)]
        rows.append({
            "factor_name": factor_name,
            "session_segment_id": int(seg),
            "session_name": str(names[np.flatnonzero(mask)[0]]) if names is not None and mask.any() else str(seg),
            "n_total": int(mask.sum()),
            "n_finite": int(len(finite)),
            "unique_count": int(np.unique(finite).size) if len(finite) else 0,
            "mean": float(np.mean(finite)) if len(finite) else np.nan,
            "std": float(np.std(finite)) if len(finite) else np.nan,
            "median": float(np.median(finite)) if len(finite) else np.nan,
            "zero_ratio": float(np.mean(np.abs(finite) <= zero_tol)) if len(finite) else np.nan,
            "autocorr_lag_1": _autocorr(values, 1),
        })
    return pd.DataFrame(rows)


def test_prefix_invariance(
    factor_func: Callable[..., np.ndarray],
    factor_args: Sequence[Any],
    cut_positions: Sequence[int],
    time_axis_arg_positions: Optional[Sequence[int]] = None,
    atol: float = 1e-12,
    rtol: float = 1e-10,
) -> pd.DataFrame:
    """Recompute truncated histories and detect changes to past factor values.

    Temporal ndarray/Series/DataFrame/list/tuple arguments are inferred by their
    first-axis length unless positions are explicit. Inputs are copied for each
    call so mutation by a factor implementation cannot contaminate other trials.
    Constant vectors that happen to have that length require explicit positions.
    This test cannot verify hidden external data or untested cut positions.
    """
    full = np.asarray(factor_func(*deepcopy(factor_args)), dtype=float).copy()
    if full.ndim != 1:
        raise ValueError("factor function output must be 1D")
    if time_axis_arg_positions is None:
        time_axis_arg_positions = [
            i for i, a in enumerate(factor_args)
            if isinstance(a, (np.ndarray, pd.Series, pd.DataFrame, list, tuple))
            and np.ndim(a) >= 1 and len(a) == len(full)
        ]
    pos_set = set(time_axis_arg_positions)
    if not pos_set:
        raise ValueError("at least one time-axis argument must be identified")
    for position in pos_set:
        if (isinstance(position, bool) or not isinstance(position, (int, np.integer))
                or not 0 <= position < len(factor_args)):
            raise ValueError("time-axis argument positions must be valid integer positions")
        if len(factor_args[position]) != len(full):
            raise ValueError("time-axis argument lengths must match the full factor output")
    cuts = list(cut_positions)
    if not cuts:
        raise ValueError("cut_positions must contain at least one non-empty strict prefix")
    rows = []
    for cut in cuts:
        if (isinstance(cut, bool) or not isinstance(cut, (int, np.integer))
                or not 0 < cut < len(full)):
            raise ValueError("cut positions must be integers strictly between 0 and output length")
        args = deepcopy(factor_args)
        args = list(args)
        for position in pos_set:
            value = args[position]
            args[position] = value.iloc[:cut].copy() if isinstance(
                value, (pd.Series, pd.DataFrame)
            ) else value[:cut]
        prefix = np.asarray(factor_func(*args), dtype=float)
        expected = full[:cut]
        if prefix.shape != expected.shape:
            raise ValueError(f"factor prefix output shape {prefix.shape} must equal {(cut,)}")
        same = np.isclose(prefix, expected, atol=atol, rtol=rtol, equal_nan=True)
        finite = np.isfinite(prefix) & np.isfinite(expected)
        max_err = float(np.max(np.abs(prefix[finite] - expected[finite]))) if finite.any() else 0.0
        rows.append({"cut": int(cut), "passed": bool(same.all()),
                     "n_mismatch": int((~same).sum()), "max_abs_error": max_err})
    return pd.DataFrame(rows)


# This public diagnostic is not a pytest test function.
test_prefix_invariance.__test__ = False


def run_factor_qc(
    factor_values: ArrayLike,
    index=None,
    factor_name: str = "factor",
    expected_warmup: Optional[int] = None,
    rolling_window: int = 240,
    block_size: int = 240,
    zero_tol: float = 1e-14,
    *,
    factor_type: str = "event_flow",
    session_segment: Optional[ArrayLike] = None,
    session_name: Optional[Sequence[Any]] = None,
    support_values: Optional[ArrayLike] = None,
    support_threshold: float = 0.10,
    expected_range: Optional[tuple[float, float]] = None,
    thresholds: QCThresholds = DEFAULT_THRESHOLDS,
) -> Dict[str, Any]:
    summary = factor_qc_summary(
        factor_values, index, factor_name, expected_warmup, zero_tol,
        session_segment=session_segment, session_name=session_name,
        support_values=support_values, support_threshold=support_threshold,
        factor_type=factor_type, expected_range=expected_range,
        robust_z_threshold=thresholds.robust_z_threshold,
        jump_multiple=thresholds.jump_multiple,
        jump_expected_range_fraction=thresholds.jump_expected_range_fraction,
    )
    result = {
        "summary": pd.Series(summary, name=factor_name),
        "warnings": factor_qc_warnings(summary, thresholds),
        "rolling": rolling_factor_qc(factor_values, index, factor_name, rolling_window, zero_tol=zero_tol, support_values=support_values, support_threshold=support_threshold),
        "blocks": block_factor_qc(factor_values, index, factor_name, block_size, zero_tol),
    }
    if session_segment is not None:
        result["sessions"] = session_factor_qc(factor_values, session_segment, session_name, index, factor_name, zero_tol)
    return result


def run_factor_frame_qc(
    factors: pd.DataFrame,
    manifest: pd.DataFrame,
    *,
    base: Optional[pd.DataFrame] = None,
    identity_columns: Sequence[str] = ("security_id", "time_ms", "time", "session_id", "session_name"),
    support_threshold: float = 0.10,
    thresholds: QCThresholds = DEFAULT_THRESHOLDS,
) -> Dict[str, Any]:
    if "factor" not in manifest.columns:
        raise KeyError("manifest must contain a 'factor' column")
    if factors.columns.has_duplicates or manifest["factor"].isna().any() or manifest["factor"].duplicated().any():
        raise ValueError("factor columns and manifest factor names must be unique/non-missing")
    if base is not None and not base.index.equals(factors.index):
        raise ValueError("base and factors must have exactly equal indices/order")
    segment = None
    session_names = None
    if base is not None and "session_segment_id" in base:
        segment = base["session_segment_id"].to_numpy()
        session_names = base["session_name"].to_numpy() if "session_name" in base else None
    elif "session_id" in factors:
        segment = factors["session_id"].to_numpy()
        session_names = factors["session_name"].to_numpy() if "session_name" in factors else None

    summaries = []
    warning_rows = []
    session_rows = []
    manifest_index = manifest.set_index("factor", drop=False)
    for name in manifest["factor"]:
        if name not in factors:
            warning_rows.append({"factor": name, "severity": "ERROR", "message": "factor missing from frame"})
            continue
        row = manifest_index.loc[name]
        factor_type = str(row.get("qc_type", "event_flow"))
        support_name = str(row.get("support_factor", "") or "")
        support_values = factors[support_name] if support_name and support_name in factors else None
        expected = _infer_expected_range(None, row.get("expected_lower"), row.get("expected_upper"))
        report = run_factor_qc(
            factors[name], index=factors.index, factor_name=name,
            factor_type=factor_type, session_segment=segment, session_name=session_names,
            support_values=support_values, support_threshold=support_threshold,
            expected_range=expected, thresholds=thresholds,
        )
        summary = report["summary"].to_dict()
        summary["category"] = row.get("category", "")
        summary["support_factor"] = support_name
        summaries.append(summary)
        for message in report["warnings"]:
            severity = message.split(":", 1)[0] if ":" in message else "PASS"
            warning_rows.append({"factor": name, "severity": severity, "message": message})
        if "sessions" in report:
            session_rows.append(report["sessions"])

    summary_df = pd.DataFrame(summaries)
    warnings_df = pd.DataFrame(warning_rows)
    sessions_df = pd.concat(session_rows, ignore_index=True) if session_rows else pd.DataFrame()

    factor_names = [c for c in manifest["factor"] if c in factors]
    duplicate_rows = []
    values = factors[factor_names].to_numpy(float)
    for i in range(len(factor_names)):
        for j in range(i + 1, len(factor_names)):
            a, b = values[:, i], values[:, j]
            finite = np.isfinite(a) & np.isfinite(b)
            if finite.sum() == 0:
                continue
            if not np.array_equal(np.isfinite(a), np.isfinite(b)):
                # Agreement on the finite intersection is not equality when
                # the two factors have different support/missingness patterns.
                continue
            if np.max(np.abs(a[finite] - b[finite])) <= 1e-12:
                duplicate_rows.append({"relation": "identical", "factor_a": factor_names[i], "factor_b": factor_names[j]})
            elif np.max(np.abs(a[finite] + b[finite])) <= 1e-12:
                duplicate_rows.append({"relation": "exact_negative", "factor_a": factor_names[i], "factor_b": factor_names[j]})
    return {
        "summary": summary_df,
        "warnings": warnings_df,
        "sessions": sessions_df,
        "duplicates": pd.DataFrame(duplicate_rows),
    }


def print_factor_qc(report: Dict[str, Any]) -> None:
    print(report["summary"].to_string())
    print("\nWarnings:")
    for msg in report["warnings"]:
        print("-", msg)


if __name__ == "__main__":
    rng = np.random.default_rng(42)
    x = np.full(3000, np.nan)
    x[30:] = rng.normal(size=2970)
    x[800:820] = np.nan
    x[1500:1600] = 0.0
    x[2200] = 50.0
    segment = np.repeat(np.arange(10), 300)
    support = np.ones(3000)
    support[:30] = 0.0
    report = run_factor_qc(
        x, factor_name="synthetic_factor", expected_warmup=30,
        factor_type="event_flow", session_segment=segment,
        support_values=support,
    )
    print_factor_qc(report)
