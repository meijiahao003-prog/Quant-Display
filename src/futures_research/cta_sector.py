"""Same-time commodity-sector preparation and optional Fit-only augmentation.

The public target is a supplied 30-minute return minus its same-time sector
median. ``label_end >= Datetime + 30min`` is necessary but cannot establish that
the upstream return uses the correct prices, contract or horizon. The caller
must verify that definition and provide the complete intended cross-section.
No operation here learns from observations at later decision timestamps.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Mapping

import numpy as np
import pandas as pd

from .factor_runtime import frame_fingerprint
from .pipeline import ResearchDataset


def _validate(ds: ResearchDataset, mapping: Mapping[str, str], *, allow_missing_labels: bool = False) -> np.ndarray:
    if not isinstance(ds, ResearchDataset) or not isinstance(ds.y, pd.Series):
        raise TypeError("ds must be a ResearchDataset with one target Series")
    ds.validate(allow_missing_labels=allow_missing_labels)
    if ds.x.empty:
        raise ValueError("sector preparation requires rows and feature columns")
    if not isinstance(mapping, Mapping) or not mapping:
        raise ValueError("contract_to_sector must be a non-empty mapping")
    if any(not isinstance(k, str) or not k.strip() or
           not isinstance(v, str) or not v.strip() for k, v in mapping.items()):
        raise ValueError("mapping contracts and sectors must be non-empty strings")
    contracts = ds.x.index.get_level_values("Contract")
    if any(not isinstance(c, str) or not c.strip() for c in contracts):
        raise ValueError("Contract must contain non-empty string identifiers")
    if any(c.startswith("SYN_") for c in contracts):
        raise ValueError("SYN_ is reserved: input must contain only real observations")
    unknown = set(contracts) - set(mapping)
    if unknown:
        raise ValueError(f"contract_to_sector has no mapping for {sorted(unknown)}")
    times = pd.DatetimeIndex(ds.x.index.get_level_values("Datetime"))
    dates = pd.DatetimeIndex(ds.x.index.get_level_values("Date"))
    if times.tz is not None or dates.tz is not None or dates.isna().any():
        raise ValueError("Date and Datetime must be finite, timezone-naive exchange times")
    if not dates.equals(dates.normalize()):
        raise ValueError("Date must be a normalized exchange trading date")
    if pd.MultiIndex.from_arrays([contracts, times]).has_duplicates:
        raise ValueError("a Contract/Datetime cannot occur under multiple Date values")
    if any(pd.api.types.is_complex_dtype(dtype) for dtype in ds.x.dtypes) or pd.api.types.is_complex_dtype(ds.y.dtype):
        raise ValueError("features and labels must be real-valued")
    if np.isinf(ds.x.to_numpy(dtype=float)).any():
        raise ValueError("features may contain NaN but not infinity")
    if (pd.DatetimeIndex(ds.label_end) < times + pd.Timedelta(minutes=30)).any():
        raise ValueError("30-minute targets require label_end >= Datetime + 30min")
    sectors = np.asarray([mapping[c] for c in contracts], dtype=object)
    calendar = pd.DataFrame({"Datetime": times, "sector": sectors, "Date": dates})
    if calendar.groupby(["Datetime", "sector"])["Date"].nunique().gt(1).any():
        raise ValueError("each Datetime/sector must declare one exchange trading Date")
    return sectors


def _zscore(values: pd.DataFrame, keys: list) -> pd.DataFrame:
    grouped = values.groupby(keys, sort=False)
    mean = grouped.transform("mean")
    std = grouped.transform("std", ddof=0)
    # A constant observed value maps to zero. Original NaNs remain NaN,
    # including an entirely missing column in a cross-section.
    result = (values - mean).div(std.mask(std.eq(0), 1.0))
    if not np.array_equal(result.isna().to_numpy(), values.isna().to_numpy()):
        raise ValueError("cross-section normalization produced undefined observed values")
    if np.isinf(result.to_numpy(dtype=float)).any():
        raise ValueError("cross-section normalization overflowed")
    return result


def prepare_sector_dataset(
    ds: ResearchDataset,
    contract_to_sector: Mapping[str, str],
    *,
    target_mode: str = "sector_median_excess",
    allow_missing_labels: bool = False,
) -> ResearchDataset:
    """Normalize real features and targets within each Datetime/sector.

    X uses population standard deviation (ddof=0); singleton/constant values
    become zero, and original missing features remain missing. The default y is
    raw 30-minute return minus the sector median. ``sector_zscore`` preserves the
    source notebook's alternative label definition as an explicit opt-in.

    Every output row inherits the group's maximum feature availability and
    label end: cross-sectional statistics depend on the other group members.
    Supply all intended members before splitting by sector or filtering rows.
    For frozen Test inference, ``allow_missing_labels=True`` preserves every
    feature row. If any sector peer has an unknown target, the entire group
    target is unknown; incomplete future outcomes never redefine its median.
    """
    if target_mode not in {"sector_median_excess", "sector_zscore"}:
        raise ValueError("target_mode must be sector_median_excess or sector_zscore")
    sectors = _validate(ds, contract_to_sector, allow_missing_labels=allow_missing_labels)
    keys = [ds.x.index.get_level_values("Datetime"), sectors]
    x = _zscore(ds.x, keys)
    if target_mode == "sector_median_excess":
        y = ds.y - ds.y.groupby(keys, sort=False).transform("median")
    else:
        y = _zscore(ds.y.to_frame(), keys).iloc[:, 0]
    if allow_missing_labels:
        complete = ds.y.notna().groupby(keys, sort=False).transform("all")
        y = y.where(complete)
    y.name = f"{target_mode}_return_30m"
    x.attrs = dict(ds.x.attrs)
    x.attrs.update(sector_feature_normalization="same_datetime_zscore_ddof0",
                   sector_target_mode=target_mode)
    available = ds.feature_available_at.groupby(keys, sort=False).transform("max")
    label_end = ds.label_end.groupby(keys, sort=False).transform("max")
    result = ResearchDataset(x, y, available, label_end)
    result.validate(allow_missing_labels=allow_missing_labels)
    return result


def augment_fit_samples(
    ds: ResearchDataset,
    contract_to_sector: Mapping[str, str],
    *,
    fit_start,
    fit_end,
    n_synthetic_per_sector: int = 1,
    alpha: float = 1.0,
    seed: int = 42,
) -> tuple[ResearchDataset, dict]:
    """Append fixed-weight virtual contracts derived only from eligible Fit rows.

    Use already prepared real cross-sections. Draw K Dirichlet weight vectors
    per sector once, in sorted eligible Fit-member order, and reuse them at every Fit
    timestamp. Absent/ineligible members receive zero weight; the rest are
    renormalized. At least two eligible members are required. A missing feature
    in any participating member makes that synthetic feature missing; nothing
    is imputed. Synthetic labels are convex combinations, not renormalized.

    Eligibility is ``fit_start <= Datetime < fit_end`` and ``label_end < fit_end``.
    Original rows, including Valid/Test and boundary-crossing Fit rows, are kept
    unchanged; normal downstream purging still applies to those original rows.
    The audit records weights, membership and input fingerprints. Synthetic
    rows must be excluded from reported out-of-sample IC/evaluation.
    """
    sectors = _validate(ds, contract_to_sector)
    start, end = pd.Timestamp(fit_start), pd.Timestamp(fit_end)
    if pd.isna(start) or pd.isna(end) or start.tz is not None or end.tz is not None or start >= end:
        raise ValueError("Fit must be a non-empty timezone-naive half-open interval")
    if isinstance(n_synthetic_per_sector, bool) or not isinstance(n_synthetic_per_sector, (int, np.integer)) or n_synthetic_per_sector < 0:
        raise ValueError("n_synthetic_per_sector must be a non-negative integer")
    if isinstance(alpha, bool) or not np.isscalar(alpha) or not np.isfinite(alpha) or alpha <= 0:
        raise ValueError("alpha must be finite and positive")
    if isinstance(seed, bool) or not isinstance(seed, (int, np.integer)) or seed < 0:
        raise ValueError("seed must be a non-negative integer")
    times = pd.DatetimeIndex(ds.x.index.get_level_values("Datetime"))
    in_fit = (times >= start) & (times < end)
    eligible = in_fit & (pd.DatetimeIndex(ds.label_end) < end)
    audit = {
        "fit_start": str(start), "fit_end_exclusive": str(end),
        "seed": int(seed), "alpha": float(alpha),
        "n_synthetic_per_sector": int(n_synthetic_per_sector),
        "input_rows": len(ds.x), "eligible_real_rows": int(eligible.sum()),
        "excluded_fit_rows_label_boundary": int((in_fit & ~eligible).sum()),
        "synthetic_rows": 0, "skipped_groups_fewer_than_two": 0,
        "missing_features": "strict_nan_propagation_no_imputation",
        "original_rows_preserved": True,
        "weight_universe": "eligible_fit_contracts_only",
        "input_fingerprints": {
            "x": frame_fingerprint(ds.x), "y": frame_fingerprint(ds.y.to_frame()),
            "timing": frame_fingerprint(pd.concat([ds.feature_available_at.rename("feature_available_at"),
                                                     ds.label_end.rename("label_end")], axis=1)),
        },
        "sectors": {},
    }
    if n_synthetic_per_sector == 0 or not eligible.any():
        return ds, audit
    feature_blocks, target_blocks, indices, available_values, end_values = [], [], [], [], []
    positions = pd.DataFrame({"Datetime": times, "sector": sectors,
                              "position": np.arange(len(ds.x))})
    for sector in sorted(set(sectors[eligible])):
        # Valid/Test-only contracts must not alter the random weight space
        # or any historical Fit sample, even when included in the run mapping.
        member_mask = eligible & (sectors == sector)
        members = sorted(set(ds.x.index.get_level_values("Contract")[member_mask]))
        # A sector's weights do not change when an unrelated sector is added.
        sector_seed = int.from_bytes(sha256(sector.encode("utf-8")).digest()[:8], "little")
        rng = np.random.default_rng(np.random.SeedSequence([int(seed), sector_seed]))
        weights = rng.dirichlet(np.full(len(members), alpha), size=n_synthetic_per_sector)
        if not np.isfinite(weights).all():
            raise ValueError("Dirichlet weights are undefined; use a numerically stable alpha")
        ids = [f"SYN_{sector}_{i:04d}" for i in range(n_synthetic_per_sector)]
        audit["sectors"][sector] = {"members": members, "synthetic_contracts": ids,
                                     "weights": weights.tolist()}
        groups = positions.loc[eligible & (sectors == sector)].groupby("Datetime", sort=True)
        for timestamp, group in groups:
            row_positions = group["position"].to_numpy(dtype=int)
            if len(row_positions) < 2:
                audit["skipped_groups_fewer_than_two"] += 1
                continue
            by_contract = {ds.x.index[p][1]: p for p in row_positions}
            present = np.asarray([member in by_contract for member in members])
            ordered = [by_contract[member] for member in members if member in by_contract]
            partial = weights[:, present]
            weight_sum = partial.sum(axis=1, keepdims=True)
            if (weight_sum <= 0).any():
                raise ValueError("eligible members have zero Dirichlet weight; adjust alpha")
            partial = partial / weight_sum
            feature_blocks.append(partial @ ds.x.iloc[ordered].to_numpy(dtype=float))
            target_blocks.append(partial @ ds.y.iloc[ordered].to_numpy(dtype=float))
            date = ds.x.index[ordered[0]][0]
            indices.extend((date, contract, timestamp) for contract in ids)
            available_values.extend([ds.feature_available_at.iloc[ordered].max()] * len(ids))
            end_values.extend([ds.label_end.iloc[ordered].max()] * len(ids))
    if not indices:
        return ds, audit
    index = pd.MultiIndex.from_tuples(indices, names=ds.x.index.names)
    sx = pd.DataFrame(np.vstack(feature_blocks), index=index, columns=ds.x.columns)
    sy = pd.Series(np.concatenate(target_blocks), index=index, name=ds.y.name)
    sa = pd.Series(available_values, index=index, name=ds.feature_available_at.name)
    se = pd.Series(end_values, index=index, name=ds.label_end.name)
    original_x = ds.x.copy(deep=False)
    original_x.attrs = {}
    x = pd.concat([original_x, sx])
    x.attrs = dict(ds.x.attrs)
    result = ResearchDataset(x, pd.concat([ds.y, sy]),
                             pd.concat([ds.feature_available_at, sa]),
                             pd.concat([ds.label_end, se]))
    result.validate()
    audit["synthetic_rows"] = len(index)
    audit["synthetic_missing_feature_values"] = int(sx.isna().sum().sum())
    return result, audit
