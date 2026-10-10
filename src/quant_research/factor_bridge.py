"""Bridge single-date factor computation to the aligned research panel contract.

The adapter audits explicit snapshot declarations and actual reads. It cannot
prove that a vendor supplied a historically unrevised, point-in-time snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import inspect
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from ashare_factor_lab.data import DataProvider
from ashare_factor_lab.factors.base import Factor


def _timestamp(value: Any, name: str) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if pd.isna(result):
        raise ValueError(f"{name} must be a finite timestamp")
    return result


def _digest(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _factor_provenance(factor: Factor) -> dict[str, Any]:
    """Record scalar research parameters without serializing arbitrary objects."""
    parameters: dict[str, Any] = {}
    for cls in reversed(type(factor).__mro__):
        parameters.update({
            name: value for name, value in vars(cls).items()
            if not name.startswith("_") and isinstance(value, (str, int, float, bool, type(None)))
        })
    parameters.update({
        name: value for name, value in vars(factor).items()
        if not name.startswith("_") and isinstance(value, (str, int, float, bool, type(None)))
    })
    try:
        source_hash = sha256(inspect.getsource(type(factor)).encode()).hexdigest()
    except (OSError, TypeError):
        source_hash = None
    return {
        "implementation": f"{type(factor).__module__}.{type(factor).__qualname__}",
        "class_source_sha256": source_hash,
        "public_scalar_parameters": parameters,
        "external_state_verified": False,
    }


class SnapshotDataProvider(DataProvider):
    """Audited wrapper around an already time-restricted provider snapshot.

    The wrapped provider must expose only data known by ``available_at``, with
    columns in exactly ``asset_ids`` order. This wrapper does not turn a full
    history provider into an as-of provider or infer fiscal disclosure dates.
    Use a factory that selects a distinct snapshot for each decision timestamp.
    """

    def __init__(
        self,
        provider: DataProvider,
        *,
        as_of: Any,
        available_at: Any,
        asset_ids: Sequence[str],
        asset_class: str,
        snapshot_id: str,
    ) -> None:
        if not isinstance(provider, DataProvider):
            raise TypeError("provider must implement the DataProvider interface")
        decision = _timestamp(as_of, "as_of")
        available = _timestamp(available_at, "available_at")
        if str(decision.tz) != str(available.tz):
            raise ValueError("as_of and available_at must share one timezone")
        if available > decision:
            raise ValueError("snapshot contains data unavailable at as_of")
        if isinstance(asset_ids, (str, bytes)):
            raise TypeError("asset_ids must be a sequence of asset identifiers")
        identifiers = tuple(asset_ids)
        if not identifiers or any(not isinstance(x, str) or not x.strip() for x in identifiers):
            raise ValueError("asset_ids must contain non-empty strings")
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("asset_ids must be unique within each snapshot")
        if not isinstance(asset_class, str) or not asset_class.strip():
            raise ValueError("asset_class must be explicitly provided")
        if not isinstance(snapshot_id, str) or not snapshot_id.strip():
            raise ValueError("snapshot_id must be a non-empty reproducible identifier")
        self._provider = provider
        self._as_of = decision
        self._available_at = available
        self._asset_ids = identifiers
        self._asset_class = asset_class
        self._snapshot_id = snapshot_id
        self._reads: list[dict[str, Any]] = []

    @property
    def as_of(self) -> pd.Timestamp:
        return self._as_of

    @property
    def available_at(self) -> pd.Timestamp:
        return self._available_at

    @property
    def asset_ids(self) -> tuple[str, ...]:
        return self._asset_ids

    @property
    def asset_class(self) -> str:
        return self._asset_class

    @property
    def snapshot_id(self) -> str:
        return self._snapshot_id

    def _record(self, value: Any, *, kind: str, fields: Sequence[str], ndim: int,
                requested_rows: int | None = None) -> np.ndarray:
        array = np.asarray(value, dtype=np.float64)
        if array.ndim != ndim or array.shape[-1] != len(self.asset_ids):
            raise ValueError(
                f"{kind} array must be {ndim}D with {len(self.asset_ids)} asset columns; "
                f"received {array.shape}"
            )
        if requested_rows is not None and array.shape[0] != requested_rows:
            raise ValueError(f"{kind} provider returned a row count different from the requested lookback")
        if array.shape[0] == 0:
            raise ValueError(f"{kind} snapshot returned an empty history")
        material = np.ascontiguousarray(array)
        value_hash = sha256(material.tobytes()).hexdigest()
        self._reads.append({
            "kind": kind, "fields": list(fields), "shape": list(array.shape),
            "dtype": str(array.dtype), "values_sha256": value_hash,
            "requested_rows": requested_rows,
        })
        return array.copy()

    def daily(self, name: str, lookback: int) -> np.ndarray:
        return self._record(self._provider.daily(name, lookback), kind="daily",
                            fields=[name], ndim=2, requested_rows=lookback)

    def intraday(self, name: str, lookback_days: int) -> np.ndarray:
        return self._record(self._provider.intraday(name, lookback_days), kind="intraday",
                            fields=[name], ndim=3, requested_rows=lookback_days)

    def fundamental(self, statement: str, field_name: str) -> np.ndarray:
        return self._record(self._provider.fundamental(statement, field_name),
                            kind="fundamental", fields=[statement, field_name], ndim=2)

    def audit(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(), "available_at": self.available_at.isoformat(),
            "asset_ids": list(self.asset_ids), "asset_class": self.asset_class,
            "snapshot_id": self.snapshot_id,
            "source_reads": [dict(read) for read in self._reads],
            "vendor_point_in_time_verified": False,
        }


@dataclass(frozen=True)
class FactorPanel:
    """Feature panel and explicit metadata; labels must be supplied separately."""

    x: pd.DataFrame
    feature_available_at: pd.Series
    audit: Mapping[str, Any]


def build_factor_panel(
    factors: Mapping[str, Factor],
    *,
    decision_times: Sequence[Any],
    provider_factory: Callable[[pd.Timestamp], SnapshotDataProvider],
) -> FactorPanel:
    """Compute a factor cross-section from each independently declared snapshot.

    Factor results must have exactly shape ``(1, n_assets)``. No flattening,
    broadcasting, automatic look-ahead truncation, target construction, or
    interpretation of asset identifiers as futures contracts is performed.
    """
    if not isinstance(factors, Mapping) or not factors:
        raise ValueError("factors must be a non-empty name-to-Factor mapping")
    if any(not isinstance(name, str) or not name.strip() for name in factors):
        raise ValueError("factor column names must be non-empty strings")
    if any(not isinstance(factor, Factor) for factor in factors.values()):
        raise TypeError("factor values must implement the Factor interface")
    if isinstance(decision_times, (str, bytes)):
        raise TypeError("decision_times must be a sequence")
    times = pd.DatetimeIndex(decision_times)
    if times.empty or times.isna().any() or not times.is_unique or not times.is_monotonic_increasing:
        raise ValueError("decision_times must be non-empty, unique, finite and chronological")
    frames: list[pd.DataFrame] = []
    availability: list[pd.Series] = []
    audits: list[dict[str, Any]] = []
    snapshot_ids: set[str] = set()
    asset_class: str | None = None
    for decision in times:
        snapshot = provider_factory(decision)
        if not isinstance(snapshot, SnapshotDataProvider):
            raise TypeError("provider_factory must return a SnapshotDataProvider")
        if snapshot.as_of != decision or str(snapshot.as_of.tz) != str(decision.tz):
            raise ValueError("provider snapshot as_of must exactly match the requested decision time")
        if snapshot.snapshot_id in snapshot_ids:
            raise ValueError("provider_factory must supply distinct snapshot_ids for different decisions")
        snapshot_ids.add(snapshot.snapshot_id)
        if asset_class is None:
            asset_class = snapshot.asset_class
        if snapshot.asset_class != asset_class:
            raise ValueError("one panel must declare one asset_class; combine asset classes explicitly")
        data: dict[str, np.ndarray] = {}
        factor_audits: dict[str, Any] = {}
        for name, factor in factors.items():
            before = len(snapshot._reads)
            values = np.asarray(factor.compute(snapshot), dtype=np.float64)
            required = (1, len(snapshot.asset_ids))
            if values.shape != required:
                raise ValueError(f"factor {name!r} returned {values.shape}; expected exactly {required}")
            if len(snapshot._reads) == before:
                raise ValueError(f"factor {name!r} did not read through the audited snapshot provider")
            if np.isinf(values).any():
                raise ValueError(f"factor {name!r} returned infinite values")
            data[name] = values[0].copy()
            factor_audits[name] = {
                **_factor_provenance(factor),
                "source_read_positions": list(range(before, len(snapshot._reads))),
            }
        index = pd.MultiIndex.from_arrays(
            [[decision.normalize()] * len(snapshot.asset_ids), snapshot.asset_ids,
             [decision] * len(snapshot.asset_ids)],
            names=["Date", "Contract", "Datetime"],
        )
        frames.append(pd.DataFrame(data, index=index))
        availability.append(pd.Series(snapshot.available_at, index=index, name="feature_available_at"))
        audits.append({**snapshot.audit(), "factors": factor_audits})
    x = pd.concat(frames, axis=0)
    available = pd.concat(availability, axis=0)
    values_hash = sha256(pd.util.hash_pandas_object(x, index=True).values.tobytes()).hexdigest()
    audit = {
        "adapter": "single_date_factor_to_panel_v1", "asset_class": asset_class,
        "factor_columns": list(factors), "snapshot_count": len(audits), "row_count": len(x),
        "snapshots": audits, "values_sha256": values_hash,
        "vendor_point_in_time_verified": False,
        "contract_level_semantics": "asset identifier compatibility field; not an inferred futures contract",
    }
    fingerprint = _digest(audit)
    audit["fingerprint"] = fingerprint
    x.attrs.update(asset_class=asset_class, factor_bridge_fingerprint=fingerprint,
                   asset_id_level="Contract", point_in_time_source_verified=False)
    return FactorPanel(x=x, feature_available_at=available, audit=audit)


__all__ = ["FactorPanel", "SnapshotDataProvider", "build_factor_panel"]
