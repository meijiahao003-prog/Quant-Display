"""Exact-time sampling and explicit point-in-time validation for aligned factors.

Sampling alone does not validate a label horizon or prove causal feature construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from hashlib import sha256
import json
from typing import Any, Iterator, Mapping, Sequence, Union

import numpy as np
import pandas as pd

from .factor_runtime import index_fingerprint as _index_fingerprint


__all__ = [
    "EXPECTED_INDEX_NAMES",
    "SamplingResult",
    "Target",
    "sample_factor_data",
    "validate_sampling_inputs",
    "SamplingSplit",
    "validate_point_in_time",
    "purged_time_split",
]

__version__ = "1.3.0"

EXPECTED_INDEX_NAMES = ("Date", "Contract", "Datetime")

Target = Union[pd.Series, pd.DataFrame]

SamplingTime = Union[str, time]

_NANOSECONDS_PER_SECOND = 1_000_000_000


@dataclass(frozen=True)
class SamplingResult:
    """Output of :func:`sample_factor_data`.

    The result can be unpacked directly as ``x_sampled, y_sampled = result``.
    Keep the object itself when the audit information is also needed.
    """

    x: pd.DataFrame
    y: Target
    sampling_times: tuple[str, ...]
    contracts: tuple[Any, ...]
    executed: bool
    audit: Mapping[str, Any]

    def __iter__(self) -> Iterator[Union[pd.DataFrame, Target]]:
        yield self.x
        yield self.y


@dataclass(frozen=True)
class SamplingSplit:
    """Position masks aligned to the supplied index, with a purge audit."""

    masks: Mapping[str, np.ndarray]
    audit: Mapping[str, Any]


def _decision_times(index: pd.Index, datetime_level: str) -> pd.DatetimeIndex:
    if not isinstance(index, pd.Index) or not index.is_unique:
        raise ValueError("sample index must be a unique pandas Index")
    if isinstance(index, pd.MultiIndex):
        if datetime_level not in index.names:
            raise ValueError(f"sample index must contain {datetime_level!r}")
        values = index.get_level_values(datetime_level)
    elif isinstance(index, pd.DatetimeIndex):
        values = index
    else:
        raise TypeError("sample index must be a DatetimeIndex or time-bearing MultiIndex")
    datetimes = pd.DatetimeIndex(values)
    if datetimes.isna().any():
        raise ValueError("decision timestamps contain NaT")
    return datetimes


def _aligned_times(values: pd.Series, index: pd.Index, name: str) -> pd.DatetimeIndex:
    if not isinstance(values, pd.Series) or not values.index.equals(index):
        raise ValueError(f"{name} must be a Series with exactly the sample index/order")
    timestamps = pd.DatetimeIndex(values)
    if timestamps.isna().any():
        raise ValueError(f"{name} contains NaT")
    return timestamps


def validate_point_in_time(
    index: pd.Index,
    *,
    feature_available_at: pd.Series,
    label_end: pd.Series,
    datetime_level: str = "Datetime",
) -> dict[str, Any]:
    """Check declared availability and target horizons without guessing them.

    ``Datetime`` is the prediction decision timestamp, not necessarily a bar's
    open. ``feature_available_at`` must be the latest availability across every
    source used by that row. ``label_end`` is the time when its entire target is
    observed. Caller-supplied metadata is checked, not independently verified.
    This contract cannot detect centered windows or revised source data by itself.
    """
    decisions = _decision_times(index, datetime_level)
    available = _aligned_times(feature_available_at, index, "feature_available_at")
    ends = _aligned_times(label_end, index, "label_end")
    if str(available.tz) != str(decisions.tz) or str(ends.tz) != str(decisions.tz):
        raise ValueError("decision, availability and label timestamps must share one timezone")
    late = available > decisions
    if late.any():
        raise ValueError(f"{int(late.sum())} rows use features unavailable at the decision time")
    negative = ends < decisions
    if negative.any():
        raise ValueError(f"{int(negative.sum())} label_end timestamps precede their decisions")
    metadata = pd.DataFrame(
        {"feature_available_at": feature_available_at, "label_end": label_end}, index=index
    )
    digest = sha256(pd.util.hash_pandas_object(metadata, index=True).values.tobytes()).hexdigest()
    return {
        "row_count": len(index),
        "feature_availability_checked": True,
        "non_negative_label_horizon_checked": True,
        "metadata_fingerprint": digest,
        "timezone": str(decisions.tz),
        "metadata_origin_verified": False,
    }


def purged_time_split(
    index: pd.Index,
    *,
    intervals: Mapping[str, tuple[Any, Any]],
    label_end: pd.Series,
    feature_available_at: pd.Series,
    embargo: Any = "0s",
    datetime_level: str = "Datetime",
) -> SamplingSplit:
    """Build chronological masks and purge labels that cross a split boundary.

    Intervals are inclusive and must be strictly ordered/non-overlapping in the
    mapping. Every retained label must finish by its own interval end. Before a
    later split, additionally require ``label_end + embargo < next_start``.
    Embargo is an elapsed-time buffer on the earlier label end, not a count of
    rows or trading days. No interval is inferred from observed test outcomes.
    """
    audit = validate_point_in_time(
        index, feature_available_at=feature_available_at, label_end=label_end,
        datetime_level=datetime_level,
    )
    decisions = _decision_times(index, datetime_level)
    ends = _aligned_times(label_end, index, "label_end")
    delay = pd.Timedelta(embargo)
    if pd.isna(delay) or delay < pd.Timedelta(0):
        raise ValueError("embargo must be a non-negative finite elapsed duration")
    if not isinstance(intervals, Mapping) or not intervals:
        raise ValueError("intervals must be a non-empty ordered mapping")
    bounds: list[tuple[str, pd.Timestamp, pd.Timestamp]] = []
    for name, interval in intervals.items():
        if not isinstance(name, str) or not name:
            raise ValueError("split names must be non-empty strings")
        if len(interval) != 2:
            raise ValueError("each interval must contain start and end timestamps")
        start, end = map(pd.Timestamp, interval)
        if pd.isna(start) or pd.isna(end) or start > end:
            raise ValueError("split boundaries must be finite and start <= end")
        if str(start.tz) != str(decisions.tz) or str(end.tz) != str(decisions.tz):
            raise ValueError("split boundaries must share the sample timestamp timezone")
        if bounds and start <= bounds[-1][2]:
            raise ValueError("split intervals must be chronological and strictly non-overlapping")
        bounds.append((name, start, end))
    masks: dict[str, np.ndarray] = {}
    splits: dict[str, dict[str, Any]] = {}
    for position, (name, start, end) in enumerate(bounds):
        candidate = np.asarray((decisions >= start) & (decisions <= end))
        keep = candidate & np.asarray(ends <= end)
        next_start = bounds[position + 1][1] if position + 1 < len(bounds) else None
        if next_start is not None:
            keep &= np.asarray(ends < next_start - delay)
        masks[name] = keep
        splits[name] = {
            "start": start.isoformat(), "end": end.isoformat(),
            "candidate_rows": int(candidate.sum()), "retained_rows": int(keep.sum()),
            "purged_rows": int((candidate & ~keep).sum()),
            "next_split_start": next_start.isoformat() if next_start is not None else None,
            "retained_index_fingerprint": _index_fingerprint(index[keep]),
        }
    covered = np.logical_or.reduce(list(masks.values()))
    return SamplingSplit(masks=masks, audit={
        **audit, "embargo": str(delay), "splits": splits,
        "unselected_rows": int((~covered).sum()),
        "boundary_rule": "label_end <= own_end and label_end + embargo < next_start",
    })


def validate_sampling_inputs(x: pd.DataFrame, y: Target) -> pd.DatetimeIndex:
    """Validate x/y without sorting, intersecting, or silently reindexing."""
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
            "x and y MultiIndex values/order differ. Sampling never takes an "
            "intersection or automatically reindexes because that could hide "
            "feature-label misalignment."
        )

    datetimes = pd.DatetimeIndex(x.index.get_level_values("Datetime"))
    if datetimes.isna().any():
        raise ValueError("Datetime index contains NaT")
    return datetimes


def _parse_sampling_time(value: SamplingTime) -> tuple[int, str]:
    """Return nanoseconds since midnight and a canonical display string."""
    if isinstance(value, time):
        if value.tzinfo is not None:
            raise ValueError(f"sampling time must not contain a timezone: {value!r}")
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("sampling time cannot be empty")

        parsed = None
        for date_format in ("%H:%M", "%H:%M:%S", "%H:%M:%S.%f"):
            try:
                parsed = pd.to_datetime(text, format=date_format).time()
                break
            except (TypeError, ValueError):
                continue
        if parsed is None:
            raise ValueError(
                f"invalid sampling time {value!r}; use HH:MM, HH:MM:SS, "
                "or HH:MM:SS.ffffff"
            )
    else:
        raise TypeError(
            "each sampling time must be a string or datetime.time, "
            f"got {type(value)!r}"
        )

    nanoseconds = (
        (parsed.hour * 3_600 + parsed.minute * 60 + parsed.second)
        * _NANOSECONDS_PER_SECOND
        + parsed.microsecond * 1_000
    )
    if parsed.microsecond:
        canonical = parsed.strftime("%H:%M:%S.%f").rstrip("0")
    elif parsed.second:
        canonical = parsed.strftime("%H:%M:%S")
    else:
        canonical = parsed.strftime("%H:%M")
    return nanoseconds, canonical


def _normalise_sampling_times(
    sampling_times: Sequence[SamplingTime],
) -> tuple[np.ndarray, tuple[str, ...]]:
    """Normalise an ordered sequence of sampling times into nanosecond ints."""
    if isinstance(sampling_times, (str, bytes)):
        raise TypeError(
            "sampling_times must be a sequence such as ['09:30', '10:00'], "
            "not one string"
        )
    if not isinstance(sampling_times, Sequence):
        raise TypeError("sampling_times must be a sequence")
    if len(sampling_times) == 0:
        raise ValueError("sampling_times cannot be empty")

    nanoseconds: list[int] = []
    canonical: list[str] = []
    seen: set[int] = set()
    for value in sampling_times:
        time_ns, time_text = _parse_sampling_time(value)
        if time_ns in seen:
            raise ValueError(f"sampling_times contains a duplicate: {time_text}")
        seen.add(time_ns)
        nanoseconds.append(time_ns)
        canonical.append(time_text)

    return np.asarray(nanoseconds, dtype=np.int64), tuple(canonical)


def _normalise_contracts(
    contracts: Union[Sequence[Any], None],
) -> Union[tuple[Any, ...], None]:
    """Validate an optional ordered contract selection."""
    if contracts is None:
        return None
    if isinstance(contracts, (str, bytes)):
        raise TypeError(
            "contracts must be a sequence such as ['IF01'], not one string"
        )
    if not isinstance(contracts, Sequence):
        raise TypeError("contracts must be a sequence or None")
    if len(contracts) == 0:
        raise ValueError("contracts cannot be empty; use None to select all contracts")

    selected = pd.Index(list(contracts), dtype="object")
    if selected.isna().any():
        raise ValueError("contracts cannot contain missing values")
    if not selected.is_unique:
        duplicates = selected[selected.duplicated()].tolist()
        raise ValueError(f"contracts contains duplicates: {duplicates}")
    return tuple(selected.tolist())


def _time_of_day_nanoseconds(datetimes: pd.DatetimeIndex) -> np.ndarray:
    """Calculate local time-of-day without converting timestamps to strings."""
    return (
        datetimes.hour.to_numpy(dtype=np.int64) * 3_600 * _NANOSECONDS_PER_SECOND
        + datetimes.minute.to_numpy(dtype=np.int64) * 60 * _NANOSECONDS_PER_SECOND
        + datetimes.second.to_numpy(dtype=np.int64) * _NANOSECONDS_PER_SECOND
        + datetimes.microsecond.to_numpy(dtype=np.int64) * 1_000
        + datetimes.nanosecond.to_numpy(dtype=np.int64)
    )


def _configuration_fingerprint(
    sampling_times: tuple[str, ...],
    contracts: Union[tuple[Any, ...], None],
) -> str:
    """SHA256 digest of the sampling configuration (versioned, reproducible)."""
    payload = {
        "module": "factor_sampling",
        "module_version": __version__,
        "matching_rule": "exact Datetime time-of-day",
        "sampling_times": list(sampling_times),
        "contracts": (
            "ALL"
            if contracts is None
            else [
                {"type": type(contract).__name__, "value": str(contract)}
                for contract in contracts
            ]
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def _grid_audit(
    source_index: pd.MultiIndex,
    sampled_index: pd.MultiIndex,
    expected_count: int,
    *,
    example_limit: int,
) -> dict[str, Any]:
    """Audit requested-point completeness for every Date + Contract group."""
    source_groups = source_index.droplevel("Datetime").unique()
    sampled_groups = sampled_index.droplevel("Datetime")

    if len(sampled_groups):
        sampled_count = (
            pd.Series(1, index=sampled_groups, dtype=np.int64)
            .groupby(level=["Date", "Contract"], sort=False)
            .sum()
            .reindex(source_groups, fill_value=0)
        )
        unique_time_count = (
            pd.Series(
                _time_of_day_nanoseconds(pd.DatetimeIndex(sampled_index.get_level_values("Datetime"))),
                index=sampled_groups,
            )
            .groupby(level=["Date", "Contract"], sort=False)
            .nunique()
            .reindex(source_groups, fill_value=0)
        )
    else:
        sampled_count = pd.Series(0, index=source_groups, dtype=np.int64)
        unique_time_count = sampled_count.copy()

    complete = (sampled_count == expected_count) & (unique_time_count == expected_count)
    incomplete = sampled_count[~complete]
    zero_sample = sampled_count[sampled_count == 0]

    examples = []
    for group_key, actual_count in incomplete.iloc[:example_limit].items():
        date_value, contract_value = group_key
        examples.append(
            {
                "Date": str(date_value),
                "Contract": str(contract_value),
                "actual_count": int(actual_count),
                "expected_count": int(expected_count),
            }
        )

    return {
        "date_contract_group_count": int(len(source_groups)),
        "complete_group_count": int(complete.sum()),
        "duplicate_time_group_count": int((sampled_count > unique_time_count).sum()),
        "incomplete_group_count": int(len(incomplete)),
        "zero_sample_group_count": int(len(zero_sample)),
        "incomplete_group_examples": examples,
    }


def sample_factor_data(
    x: pd.DataFrame,
    y: Target,
    sampling_times: Sequence[SamplingTime],
    contracts: Union[Sequence[Any], None] = None,
    *,
    execute: bool = True,
    require_each_requested_contract: bool = True,
    require_each_requested_time: bool = True,
    strict_complete_grid: bool = False,
    grid_example_limit: int = 20,
    copy_on_noop: bool = True,
) -> SamplingResult:
    """Sample aligned x/y rows at exact intraday times.

    Parameters
    ----------
    x, y:
        Aligned factor and label objects. Usually these are ``x_processed`` and
        ``y_processed`` returned by the preprocessing module.
    sampling_times:
        Ordered time strings such as ``['09:30', '10:00', '13:01']``. The
        order defines the configuration record; source row order is preserved.
    contracts:
        Optional exact values from the ``Contract`` index level, for example
        ``['IF01']`` or ``['IF01', 'IF02']``. ``None`` selects every contract.
    execute:
        If False, validate inputs and configuration but return the original x/y
        object references without filtering.
    require_each_requested_contract:
        If True, raise when any requested contract is absent from the input.
    require_each_requested_time:
        If True, raise when any requested time has zero matches over the entire
        input. This catches errors such as requesting 13:00 when only 13:01 is
        present.
    strict_complete_grid:
        If True, every Date + Contract group present in the source must contain
        exactly one row for every requested time. If False, incomplete groups
        remain in the output but are explicitly reported in ``audit``.
    grid_example_limit:
        Maximum number of incomplete Date + Contract examples stored in audit.
    copy_on_noop:
        Preserve the historical independent-output behaviour when every input
        row is retained.  Set False for immutable large inputs to share value
        blocks through a shallow wrapper and avoid a multi-gigabyte copy.

    Returns
    -------
    SamplingResult
        ``result.x`` and ``result.y`` are the sampled outputs. The object can
        also be unpacked directly as ``x_sampled, y_sampled = result``.
    """
    datetimes = validate_sampling_inputs(x, y)
    requested_ns, canonical_times = _normalise_sampling_times(sampling_times)
    selected_contracts = _normalise_contracts(contracts)

    if not isinstance(execute, (bool, np.bool_)):
        raise TypeError("execute must be boolean")
    if not isinstance(require_each_requested_contract, (bool, np.bool_)):
        raise TypeError("require_each_requested_contract must be boolean")
    if not isinstance(require_each_requested_time, (bool, np.bool_)):
        raise TypeError("require_each_requested_time must be boolean")
    if not isinstance(strict_complete_grid, (bool, np.bool_)):
        raise TypeError("strict_complete_grid must be boolean")
    if not isinstance(copy_on_noop, (bool, np.bool_)):
        raise TypeError("copy_on_noop must be boolean")
    if grid_example_limit < 0:
        raise ValueError("grid_example_limit must be non-negative")

    configuration_fingerprint = _configuration_fingerprint(
        canonical_times,
        selected_contracts,
    )

    contract_values = x.index.get_level_values("Contract")
    available_contracts = pd.Index(contract_values.unique())
    if selected_contracts is None:
        requested_contracts = tuple(available_contracts.tolist())
        missing_requested_contracts: list[Any] = []
        contract_mask = np.ones(len(x), dtype=bool)
    else:
        requested_contracts = selected_contracts
        missing_requested_contracts = [
            contract
            for contract in requested_contracts
            if contract not in available_contracts
        ]
        contract_mask = np.asarray(contract_values.isin(requested_contracts))

    if missing_requested_contracts and require_each_requested_contract:
        raise ValueError(
            "requested contracts absent from the Contract index: "
            f"{missing_requested_contracts}. Contract matching is exact."
        )

    base_audit: dict[str, Any] = {
        "library_version": __version__,
        "executed": bool(execute),
        "matching_rule": "exact Datetime time-of-day",
        "sampling_times": list(canonical_times),
        "requested_time_count": len(canonical_times),
        "contracts_selection": (
            "all" if selected_contracts is None else "explicit"
        ),
        "requested_contracts": [str(value) for value in requested_contracts],
        "missing_requested_contracts": [
            str(value) for value in missing_requested_contracts
        ],
        "require_each_requested_contract": bool(require_each_requested_contract),
        "source_row_count": int(len(x)),
        "selected_contract_source_row_count": int(contract_mask.sum()),
        "factor_count": int(x.shape[1]),
        "x_y_index_exactly_equal_before_sampling": True,
        "values_transformed": False,
        "copy_on_noop": bool(copy_on_noop),
        "source_order_preserved": True,
        "configuration_fingerprint": configuration_fingerprint,
    }

    if not execute:
        return SamplingResult(
            x=x,
            y=y,
            sampling_times=canonical_times,
            contracts=requested_contracts,
            executed=False,
            audit=base_audit,
        )

    actual_ns = _time_of_day_nanoseconds(datetimes)
    time_mask = np.isin(actual_ns, requested_ns, assume_unique=False)
    sample_mask = contract_mask & time_mask

    unique_ns, unique_counts = np.unique(actual_ns[sample_mask], return_counts=True)
    count_lookup = {
        int(time_ns): int(count)
        for time_ns, count in zip(unique_ns, unique_counts)
    }
    requested_time_counts = {
        time_text: count_lookup.get(int(time_ns), 0)
        for time_ns, time_text in zip(requested_ns, canonical_times)
    }
    missing_requested_times = [
        time_text
        for time_text, count in requested_time_counts.items()
        if count == 0
    ]

    if missing_requested_times and require_each_requested_time:
        raise ValueError(
            "requested sampling times with zero matches: "
            f"{missing_requested_times}. Matching is exact; inspect the raw "
            "Datetime values before changing the configuration."
        )


    source_count_series = pd.Series(
        np.asarray(contract_values)[contract_mask],
        copy=False,
    ).value_counts(sort=False)

    sampled_contract_values = np.asarray(contract_values)[sample_mask]
    if len(sampled_contract_values):
        contract_time_table = (
            pd.DataFrame(
                {
                    "Contract": sampled_contract_values,
                    "time_ns": actual_ns[sample_mask],
                }
            )
            .value_counts(sort=False)
            .unstack("time_ns", fill_value=0)
        )
        sampled_count_series = contract_time_table.sum(axis=1)
    else:
        contract_time_table = pd.DataFrame()
        sampled_count_series = pd.Series(dtype=np.int64)

    source_contract_counts: dict[str, int] = {}
    sampled_contract_counts: dict[str, int] = {}
    contract_time_counts: dict[str, dict[str, int]] = {}
    for contract in requested_contracts:
        if contract not in available_contracts:
            continue
        contract_key = str(contract)
        source_contract_counts[contract_key] = int(source_count_series.get(contract, 0))
        sampled_contract_counts[contract_key] = int(
            sampled_count_series.get(contract, 0)
        )
        contract_time_counts[contract_key] = {
            time_text: (
                int(contract_time_table.at[contract, int(time_ns)])
                if contract in contract_time_table.index
                and int(time_ns) in contract_time_table.columns
                else 0
            )
            for time_ns, time_text in zip(requested_ns, canonical_times)
        }

    if bool(sample_mask.all()):
        if copy_on_noop:
            x_sampled = x.copy(deep=True)
            y_sampled = y.copy(deep=True)
        else:
            x_sampled = x.copy(deep=False)
            y_sampled = y.copy(deep=False)
    else:
        x_sampled = x.loc[sample_mask]
        y_sampled = y.loc[sample_mask]

    if not x_sampled.index.equals(y_sampled.index):
        raise RuntimeError("internal error: sampled x/y indices are not identical")

    grid = _grid_audit(
        x.index[contract_mask],       # 源侧按合同掩码过滤后的索引
        x_sampled.index,              # 采样后的索引
        len(canonical_times),         # 期望每组点数 = 请求时刻数
        example_limit=grid_example_limit,
    )
    if strict_complete_grid and grid["incomplete_group_count"]:
        raise ValueError(
            "strict_complete_grid=True but "
            f"{grid['incomplete_group_count']} Date + Contract groups do not "
            f"contain exactly {len(canonical_times)} requested rows. Examples: "
            f"{grid['incomplete_group_examples'][:5]}"
        )

    output_index_fingerprint = _index_fingerprint(x_sampled.index)
    result_fingerprint = sha256(
        f"{configuration_fingerprint}|{output_index_fingerprint}".encode()
    ).hexdigest()

    sampled_datetimes = pd.DatetimeIndex(
        x_sampled.index.get_level_values("Datetime")
    )
    actual_start = (
        sampled_datetimes.min().isoformat() if len(sampled_datetimes) else None
    )
    actual_end = (
        sampled_datetimes.max().isoformat() if len(sampled_datetimes) else None
    )

    audit = {
        **base_audit,
        "sampled_row_count": int(len(x_sampled)),
        "dropped_row_count": int(len(x) - len(x_sampled)),
        "sampled_fraction": float(len(x_sampled) / len(x)) if len(x) else 0.0,
        "actual_sampled_start": actual_start,
        "actual_sampled_end": actual_end,
        "requested_time_counts": requested_time_counts,
        "missing_requested_times": missing_requested_times,
        "source_contract_counts": source_contract_counts,
        "sampled_contract_counts": sampled_contract_counts,
        "contract_time_counts": contract_time_counts,
        "require_each_requested_time": bool(require_each_requested_time),
        "strict_complete_grid": bool(strict_complete_grid),
        "x_y_index_exactly_equal_after_sampling": True,
        "output_index_fingerprint": output_index_fingerprint,
        "result_fingerprint": result_fingerprint,
        **grid,
    }

    x_sampled.attrs = x.attrs.copy()
    x_sampled.attrs.update(
        sampling_times=canonical_times,
        sampled_contracts=requested_contracts,
        sampling_configuration_fingerprint=configuration_fingerprint,
        sampling_result_fingerprint=result_fingerprint,
    )
    y_sampled.attrs = y.attrs.copy()
    y_sampled.attrs.update(
        sampling_times=canonical_times,
        sampled_contracts=requested_contracts,
        sampling_configuration_fingerprint=configuration_fingerprint,
        sampling_result_fingerprint=result_fingerprint,
    )

    return SamplingResult(
        x=x_sampled,
        y=y_sampled,
        sampling_times=canonical_times,
        contracts=requested_contracts,
        executed=True,
        audit=audit,
    )
