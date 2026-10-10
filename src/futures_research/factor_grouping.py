"""Extract mutually-exclusive factor groups without changing factor values."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd


_FACTOR_COLUMN_CANDIDATES = ("factor", "factor_name", "feature", "column")


def _stable_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FactorGroupingResult:
    """Result of extracting group columns from one factor DataFrame."""

    groups: Mapping[str, pd.DataFrame]
    factor_names: Mapping[str, tuple[str, ...]]
    passthrough: pd.DataFrame | None
    unassigned_features: tuple[str, ...]
    fingerprint: str
    audit: Mapping[str, Any]


def _read_factor_names(
    source: Sequence[str] | str | Path,
    factor_column: str | None,
) -> tuple[str, ...]:
    if isinstance(source, (str, Path)):
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"因子分组文件不存在：{path}")
        frame = pd.read_csv(path)
        if frame.empty:
            raise ValueError(f"因子分组文件为空：{path}")

        selected_column = factor_column
        if selected_column is not None and selected_column not in frame.columns:
            raise KeyError(
                f"{path} 中找不到指定因子列 {selected_column!r}；"
                f"实际列：{frame.columns.tolist()}"
            )
        if selected_column is None:
            selected_column = next(
                (name for name in _FACTOR_COLUMN_CANDIDATES if name in frame.columns),
                None,
            )
        if selected_column is None:
            usable = [
                name for name in frame.columns if not str(name).lower().startswith("unnamed")
            ]
            if len(usable) != 1:
                raise ValueError(
                    f"无法自动判断 {path} 的因子列；请在 grouping_plan 中设置 "
                    "factor_column。"
                )
            selected_column = usable[0]
        values = frame[selected_column].dropna().astype(str).str.strip().tolist()
    else:
        values = [str(value).strip() for value in source]

    values = [value for value in values if value]
    if not values:
        raise ValueError("某个因子组没有有效因子名。")
    if len(values) != len(set(values)):
        duplicated = pd.Series(values)[pd.Series(values).duplicated()].unique().tolist()
        raise ValueError(f"同一分组清单内存在重复因子：{duplicated[:20]}")
    return tuple(values)


def extract_factor_groups(
    x: pd.DataFrame,
    grouping_plan: Mapping[str, Any],
) -> FactorGroupingResult:
    """Extract factor columns into groups, preserving index, order and values.

    ``grouping_plan['groups']`` maps group names to either CSV paths or explicit
    factor-name sequences.  This function performs no imputation, scaling, PCA,
    dtype conversion, or row filtering.
    """

    if not isinstance(x, pd.DataFrame):
        raise TypeError("x 必须是 pandas.DataFrame。")
    if not isinstance(grouping_plan, Mapping):
        raise TypeError("grouping_plan 必须是映射/dict。")
    if x.columns.has_duplicates:
        raise ValueError("X 存在重复列名，无法建立唯一分组归属。")
    if not all(isinstance(column, str) for column in x.columns):
        raise TypeError("factor names must be strings")

    sources = grouping_plan.get("groups")
    if not isinstance(sources, Mapping) or not sources:
        raise ValueError("grouping_plan['groups'] 必须是非空映射。")

    factor_column = grouping_plan.get("factor_column")
    factor_names: dict[str, tuple[str, ...]] = {}
    owner: dict[str, str] = {}
    missing: dict[str, list[str]] = {}

    for raw_name, source in sources.items():
        group_name = str(raw_name)
        if not group_name.strip() or group_name in factor_names:
            raise ValueError("group names must be non-empty and unique after string conversion")
        names = _read_factor_names(source, factor_column)
        absent = [name for name in names if name not in x.columns]
        if absent:
            missing[group_name] = absent
        for name in names:
            if name in owner:
                raise ValueError(
                    f"因子 {name!r} 同时属于 {owner[name]!r} 和 {group_name!r}；"
                    "PCA 分组必须互斥。"
                )
            owner[name] = group_name
        factor_names[group_name] = names

    if missing:
        preview = {name: values[:20] for name, values in missing.items()}
        raise KeyError(f"分组清单包含 X 中不存在的因子：{preview}")

    unassigned = tuple(str(column) for column in x.columns if column not in owner)
    policy = str(grouping_plan.get("unassigned_policy", "error")).lower()
    if policy not in {"error", "drop", "passthrough"}:
        raise ValueError("unassigned_policy 只能是 error、drop 或 passthrough。")
    if unassigned and policy == "error":
        raise ValueError(
            f"X 中有 {len(unassigned)} 个因子没有分组；示例：{list(unassigned[:20])}。"
            "请补充分组，或显式设置 unassigned_policy='drop'/'passthrough'。"
        )

    fingerprint_material = {
        "input_columns": [str(column) for column in x.columns],
        "input_dtypes": [str(dtype) for dtype in x.dtypes],
        "index_names": list(x.index.names),
        "groups": {name: list(names) for name, names in factor_names.items()},
        "unassigned_policy": policy,
        "unassigned_features": list(unassigned),
    }
    fingerprint = _stable_hash(fingerprint_material)

    copy_frames = bool(grouping_plan.get("copy", False))
    group_frames: dict[str, pd.DataFrame] = {}
    values_unchanged = True
    for name, names in factor_names.items():
        frame = x.loc[:, list(names)].copy() if copy_frames else x.loc[:, list(names)]
        frame.attrs.update(x.attrs)
        frame.attrs.update(
            {
                "factor_group_name": name,
                "factor_grouping_fingerprint": fingerprint,
                "factor_group_feature_count": len(names),
            }
        )
        group_frames[name] = frame
        # Column selection must preserve the original values and row index exactly.
        values_unchanged = values_unchanged and frame.index.equals(x.index)

    passthrough = None
    if policy == "passthrough" and unassigned:
        passthrough = x.loc[:, list(unassigned)].copy() if copy_frames else x.loc[:, list(unassigned)]
        passthrough.attrs.update(x.attrs)
        passthrough.attrs["factor_grouping_fingerprint"] = fingerprint

    audit = {
        "input_shape": tuple(x.shape),
        "group_count": len(group_frames),
        "assigned_feature_count": len(owner),
        "unassigned_feature_count": len(unassigned),
        "unassigned_policy": policy,
        "group_feature_counts": {
            name: len(names) for name, names in factor_names.items()
        },
        "row_count_each_group": {
            name: len(frame) for name, frame in group_frames.items()
        },
        "index_preserved": all(frame.index.equals(x.index) for frame in group_frames.values()),
        "values_transformed": False,
        "row_filter_applied": False,
        "dtype_conversion_applied": False,
        "values_unchanged": values_unchanged,
        "fingerprint": fingerprint,
    }
    return FactorGroupingResult(
        groups=group_frames,
        factor_names=factor_names,
        passthrough=passthrough,
        unassigned_features=unassigned,
        fingerprint=fingerprint,
        audit=audit,
    )


__all__ = ["FactorGroupingResult", "extract_factor_groups"]
