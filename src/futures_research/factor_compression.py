"""Fit-only PCA compression for one factor-group DataFrame per call.

This module knows nothing about how many groups exist and does not read group
assignment files.  Call ``compress_factor_group`` once for each group, then
combine the returned DataFrames with ``pd.concat(..., axis=1)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
import warnings

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from pandas.api.types import is_numeric_dtype

from .factor_runtime import validate_preprocessing_boundary


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _hash_array(hasher: Any, array: np.ndarray) -> None:
    contiguous = np.ascontiguousarray(array)
    hasher.update(str(contiguous.dtype).encode("ascii"))
    hasher.update(str(contiguous.shape).encode("ascii"))
    hasher.update(contiguous.tobytes())


def _file_sha256(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


@dataclass(frozen=True)
class PCACompressionState:
    """Frozen PCA state for exactly one input DataFrame."""

    group_name: str
    fit_start: pd.Timestamp
    fit_end: pd.Timestamp
    datetime_level: str
    input_features: tuple[str, ...]
    used_features: tuple[str, ...]
    dropped_features: tuple[str, ...]
    impute_values: np.ndarray
    mean: np.ndarray
    components: np.ndarray
    explained_variance: np.ndarray
    explained_variance_ratio: np.ndarray
    singular_values: np.ndarray
    output_features: tuple[str, ...]
    candidate_components: int
    selected_components: int
    retained_variance_ratio: float
    target_variance_ratio: float | None
    target_reached: bool | None
    component_rule: Mapping[str, Any]
    output_dtype: str
    plan_fingerprint: str
    input_schema_fingerprint: str
    grouping_fingerprint: str | None
    fingerprint: str

    def __post_init__(self) -> None:
        # A frozen dataclass alone does not protect nested NumPy arrays.
        # Immutable byte-backed arrays preserve the fitted numeric state.
        for name in (
            "impute_values", "mean", "components", "explained_variance",
            "explained_variance_ratio", "singular_values",
        ):
            array = np.asarray(getattr(self, name))
            frozen = np.frombuffer(array.tobytes(), dtype=array.dtype).reshape(array.shape)
            object.__setattr__(self, name, frozen)
        object.__setattr__(self, "component_rule", MappingProxyType(dict(self.component_rule)))

    def manifest(self) -> dict[str, Any]:
        return {
            "method": "pca",
            "group_name": self.group_name,
            "fit_start": str(self.fit_start),
            "fit_end": str(self.fit_end),
            "datetime_level": self.datetime_level,
            "input_features": list(self.input_features),
            "used_features": list(self.used_features),
            "dropped_features": list(self.dropped_features),
            "output_features": list(self.output_features),
            "candidate_components": self.candidate_components,
            "selected_components": self.selected_components,
            "retained_variance_ratio": self.retained_variance_ratio,
            "target_variance_ratio": self.target_variance_ratio,
            "target_reached": self.target_reached,
            "component_rule": dict(self.component_rule),
            "output_dtype": self.output_dtype,
            "plan_fingerprint": self.plan_fingerprint,
            "input_schema_fingerprint": self.input_schema_fingerprint,
            "grouping_fingerprint": self.grouping_fingerprint,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class PCACompressionResult:
    """Return value of :func:`compress_factor_group`."""

    x: pd.DataFrame
    state: PCACompressionState | None
    executed: bool
    method: str
    audit: Mapping[str, Any]
    artifact_paths: Mapping[str, str]


def _resolve_component_rule(plan: Mapping[str, Any]) -> dict[str, Any]:
    rule = dict(plan.get("component_rule", {}))
    mode = str(rule.get("mode", "variance_ratio")).lower()
    if mode not in {"variance_ratio", "fixed"}:
        raise ValueError("component_rule.mode 只能是 variance_ratio 或 fixed。")

    if mode == "variance_ratio":
        value: int | float = float(rule.get("value", 0.90))
        if not 0.0 < value <= 1.0:
            raise ValueError("variance_ratio 的 value 必须位于 (0, 1]。")
    else:
        value = int(rule.get("value", 1))
        if value < 1:
            raise ValueError("fixed 的 value 必须至少为 1。")

    minimum = int(rule.get("min_components", 1))
    maximum_raw = rule.get("max_components")
    maximum = None if maximum_raw is None else int(maximum_raw)
    if minimum < 1 or (maximum is not None and maximum < minimum):
        raise ValueError("min_components/max_components 设置无效。")
    return {
        "mode": mode,
        "value": value,
        "min_components": minimum,
        "max_components": maximum,
    }


def _get_fit_mask(
    x: pd.DataFrame,
    fit_start: pd.Timestamp,
    fit_end: pd.Timestamp,
    datetime_level: str,
) -> np.ndarray:
    if datetime_level in x.index.names:
        datetimes = pd.DatetimeIndex(x.index.get_level_values(datetime_level))
    elif datetime_level in x.columns:
        datetimes = pd.DatetimeIndex(x[datetime_level])
    else:
        raise KeyError(
            f"X 的索引或列中找不到时间字段 {datetime_level!r}；"
            f"index.names={x.index.names}"
        )
    if datetimes.isna().any():
        raise ValueError("PCA time index contains NaT")
    mask = np.asarray((datetimes >= fit_start) & (datetimes <= fit_end))
    if mask.sum() < 2:
        raise ValueError("PCA Fit 时间范围内至少需要 2 行数据。")
    return mask


def _state_fingerprint(state_material: Mapping[str, Any], arrays: list[np.ndarray]) -> str:
    hasher = sha256(_stable_json(state_material).encode("utf-8"))
    for array in arrays:
        _hash_array(hasher, array)
    return hasher.hexdigest()


def transform_factor_group(
    x: pd.DataFrame,
    state: PCACompressionState,
    *,
    batch_size: int = 20_000,
) -> pd.DataFrame:
    """Transform one DataFrame using one already-fitted PCA state."""

    if not isinstance(x, pd.DataFrame):
        raise TypeError("x 必须是 pandas.DataFrame。")
    if not isinstance(state, PCACompressionState):
        raise TypeError("state must be a PCACompressionState")
    # Preserve the latest upstream information boundary before overwriting
    # compression attrs. A second PCA cannot undo an earlier future-fitted PCA.
    validate_preprocessing_boundary(x, state.fit_end + pd.Timedelta(nanoseconds=1))
    if tuple(map(str, x.columns)) != state.input_features:
        raise ValueError("待转换 X 的因子名称或顺序与 PCA state 不一致。")
    grouping_fingerprint = x.attrs.get("factor_grouping_fingerprint")
    if grouping_fingerprint != state.grouping_fingerprint:
        raise ValueError("待转换 X 的分组指纹与 PCA state 不一致。")
    if batch_size < 1:
        raise ValueError("batch_size 必须大于 0。")

    output = np.empty((len(x), state.selected_components), dtype=state.output_dtype)
    for start in range(0, len(x), batch_size):
        stop = min(start + batch_size, len(x))
        values = x.iloc[start:stop][list(state.used_features)].to_numpy(
            dtype=np.float64,
            copy=True,
        )
        values[~np.isfinite(values)] = np.nan
        values = np.where(np.isfinite(values), values, state.impute_values)
        transformed = (values - state.mean) @ state.components.T
        output[start:stop] = transformed.astype(state.output_dtype, copy=False)

    compressed = pd.DataFrame(output, index=x.index, columns=state.output_features)
    compressed.attrs.update(x.attrs)
    compressed.attrs.update(
        {
            "compression_method": "pca",
            "compression_group_name": state.group_name,
            "compression_fingerprint": state.fingerprint,
            "compression_plan_fingerprint": state.plan_fingerprint,
            "compression_input_schema_fingerprint": state.input_schema_fingerprint,
            "compression_fit_start": str(state.fit_start),
            "compression_fit_end": str(state.fit_end),
            "compression_input_feature_count": len(state.input_features),
            "compression_output_feature_count": len(state.output_features),
        }
    )
    return compressed


def save_pca_state(
    state: PCACompressionState,
    output_dir: str | Path,
) -> dict[str, str]:
    """Save JSON/NPZ plus SHA-256 checksums for non-pickle restoration.

    Checksums detect corruption or changes against the stored sidecar. They
    are not a cryptographic signature proving who produced the artifacts.
    """

    safe_group = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in state.group_name
    )
    root = Path(output_dir) / f"{safe_group}_pca_{state.fingerprint[:16]}"
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "compression_manifest.json"
    arrays_path = root / "compression_state.npz"

    manifest_text = json.dumps(state.manifest(), ensure_ascii=False, indent=2)
    if manifest_path.exists() and manifest_path.read_text(encoding="utf-8") != manifest_text:
        raise FileExistsError(f"同一指纹目录中的 manifest 内容冲突：{manifest_path}")
    manifest_path.write_text(manifest_text, encoding="utf-8")
    np.savez_compressed(
        arrays_path,
        impute_values=state.impute_values,
        mean=state.mean,
        components=state.components,
        explained_variance=state.explained_variance,
        explained_variance_ratio=state.explained_variance_ratio,
        singular_values=state.singular_values,
    )
    checksums_path = root / "compression_checksums.json"
    checksums = {
        "schema_version": 1,
        "algorithm": "sha256",
        "files": {
            manifest_path.name: _file_sha256(manifest_path),
            arrays_path.name: _file_sha256(arrays_path),
        },
    }
    checksums_path.write_text(json.dumps(checksums, sort_keys=True, indent=2), encoding="utf-8")
    return {
        "directory": str(root),
        "manifest": str(manifest_path),
        "state_arrays": str(arrays_path),
        "checksums": str(checksums_path),
    }


def load_pca_state(output_dir: str | Path) -> PCACompressionState:
    """Restore the exact directory returned by ``save_pca_state()['directory']``.

    Both manifest and NPZ must match their sidecar hashes. Legacy directories
    without checksums are rejected; regenerate them from the trusted fitted
    state rather than silently accepting unverified artifacts.
    """
    root = Path(output_dir)
    checksums_path = root / "compression_checksums.json"
    if not checksums_path.is_file():
        raise ValueError("PCA checkpoint is missing its required SHA-256 checksums")
    try:
        checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("PCA checkpoint checksums are not readable JSON") from exc
    expected_files = {"compression_manifest.json", "compression_state.npz"}
    if (not isinstance(checksums, dict)
            or checksums.get("schema_version") != 1 or checksums.get("algorithm") != "sha256"
            or not isinstance(checksums.get("files"), dict)
            or set(checksums["files"]) != expected_files):
        raise ValueError("PCA checkpoint checksums have an unsupported schema")
    for name, digest in checksums["files"].items():
        path = root / name
        if not path.is_file() or not isinstance(digest, str) or _file_sha256(path) != digest:
            raise ValueError(f"PCA checkpoint SHA-256 mismatch: {name}")
    try:
        manifest = json.loads((root / "compression_manifest.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("method") != "pca":
            raise ValueError("PCA checkpoint manifest has a different method")
        names = ("impute_values", "mean", "components", "explained_variance",
                 "explained_variance_ratio", "singular_values")
        with np.load(root / "compression_state.npz", allow_pickle=False) as archive:
            if set(archive.files) != set(names):
                raise ValueError("PCA checkpoint array names do not match the required state")
            arrays = {name: archive[name].copy() for name in names}
        selected = int(manifest["selected_components"])
        used = tuple(manifest["used_features"])
        input_features = tuple(manifest["input_features"])
        dropped = tuple(manifest["dropped_features"])
        output_features = tuple(manifest["output_features"])
        if (selected < 1 or len(output_features) != selected or not used
                or int(manifest["candidate_components"]) < selected
                or len(set(input_features)) != len(input_features)
                or len(set(used)) != len(used) or len(set(dropped)) != len(dropped)
                or set(used) & set(dropped) or set(used) | set(dropped) != set(input_features)
                or len(set(output_features)) != len(output_features)
                or manifest["output_dtype"] not in {"float32", "float64"}):
            raise ValueError("PCA checkpoint has an inconsistent feature/component schema")
        expected_shapes = {
            "impute_values": (len(used),), "mean": (len(used),),
            "components": (selected, len(used)), "explained_variance": (selected,),
            "explained_variance_ratio": (selected,), "singular_values": (selected,),
        }
        for name, array in arrays.items():
            if (array.shape != expected_shapes[name] or array.dtype.kind != "f"
                    or not np.isfinite(array).all()):
                raise ValueError(f"PCA checkpoint has invalid numeric array {name!r}")
        fit_start, fit_end = pd.Timestamp(manifest["fit_start"]), pd.Timestamp(manifest["fit_end"])
        if pd.isna(fit_start) or pd.isna(fit_end) or fit_start > fit_end:
            raise ValueError("PCA checkpoint has invalid Fit boundaries")
        return PCACompressionState(
            group_name=str(manifest["group_name"]), fit_start=fit_start, fit_end=fit_end,
            datetime_level=str(manifest["datetime_level"]), input_features=input_features,
            used_features=used, dropped_features=dropped, **arrays,
            output_features=output_features, candidate_components=int(manifest["candidate_components"]),
            selected_components=selected, retained_variance_ratio=float(manifest["retained_variance_ratio"]),
            target_variance_ratio=manifest["target_variance_ratio"], target_reached=manifest["target_reached"],
            component_rule=manifest["component_rule"], output_dtype=manifest["output_dtype"],
            plan_fingerprint=manifest["plan_fingerprint"],
            input_schema_fingerprint=manifest["input_schema_fingerprint"],
            grouping_fingerprint=manifest["grouping_fingerprint"], fingerprint=manifest["fingerprint"],
        )
    except (KeyError, TypeError, json.JSONDecodeError, OSError) as exc:
        raise ValueError("PCA checkpoint contains invalid state metadata or arrays") from exc


def compress_factor_group(
    x: pd.DataFrame,
    compression_plan: Mapping[str, Any],
) -> PCACompressionResult:
    """Fit PCA on one factor group and return that group's compressed X.

    The only rows used to fit imputation values and PCA directions are those
    between ``fit_start`` and ``fit_end`` in ``compression_plan``.  The fitted
    state is then frozen and used to transform every row in ``x``.
    """

    if not isinstance(x, pd.DataFrame):
        raise TypeError("x 必须是 pandas.DataFrame。")
    if not isinstance(compression_plan, Mapping):
        raise TypeError("compression_plan 必须是映射/dict。")
    if x.empty or x.shape[1] == 0:
        raise ValueError("x 不能为空。")
    if x.columns.has_duplicates:
        raise ValueError("x 存在重复列名。")
    if not x.index.is_unique:
        raise ValueError("x contains duplicate sample keys")
    if not all(isinstance(column, str) for column in x.columns):
        raise TypeError("PCA feature names must be strings")
    if not all(is_numeric_dtype(dtype) for dtype in x.dtypes):
        raise TypeError("PCA input must contain only numeric factor columns")

    execute = bool(compression_plan.get("execute", True))
    method = str(compression_plan.get("method", "pca")).lower()
    if not execute or method in {"none", "identity"}:
        return PCACompressionResult(
            x=x,
            state=None,
            executed=False,
            method="none",
            audit={
                "executed": False,
                "method": "none",
                "input_shape": tuple(x.shape),
                "output_shape": tuple(x.shape),
                "values_transformed": False,
            },
            artifact_paths={},
        )
    if method != "pca":
        raise ValueError("当前模块仅支持 method='pca' 或 method='none'。")

    group_name = str(
        compression_plan.get(
            "group_name",
            x.attrs.get("factor_group_name", "GROUP"),
        )
    )
    output_prefix = str(compression_plan.get("output_prefix", group_name))
    fit_start = pd.Timestamp(compression_plan["fit_start"])
    fit_end = pd.Timestamp(compression_plan["fit_end"])
    if pd.isna(fit_start) or pd.isna(fit_end):
        raise ValueError("fit_start and fit_end must be finite timestamps")
    if fit_start > fit_end:
        raise ValueError("fit_start 不能晚于 fit_end。")
    validate_preprocessing_boundary(x, fit_end + pd.Timedelta(nanoseconds=1))
    datetime_level = str(compression_plan.get("datetime_level", "Datetime"))
    fit_mask = _get_fit_mask(x, fit_start, fit_end, datetime_level)
    rule = _resolve_component_rule(compression_plan)

    input_features = tuple(map(str, x.columns))
    fit_values = x.loc[fit_mask].to_numpy(dtype=np.float64, copy=True)
    fit_values[~np.isfinite(fit_values)] = np.nan
    finite_counts = np.isfinite(fit_values).sum(axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        medians = np.nanmedian(fit_values, axis=0)
    filled = np.where(np.isfinite(fit_values), fit_values, medians)
    variances = np.var(filled, axis=0)
    tolerance = float(compression_plan.get("variance_tolerance", 1e-12))
    minimum_rows = int(compression_plan.get("min_finite_fit_rows", 2))
    if not np.isfinite(tolerance) or tolerance < 0 or minimum_rows < 2:
        raise ValueError("variance_tolerance must be finite/non-negative; min_finite_fit_rows >= 2")
    valid_mask = (
        (finite_counts >= minimum_rows)
        & np.isfinite(medians)
        & np.isfinite(variances)
        & (variances > tolerance)
    )

    dropped_features = tuple(
        np.asarray(input_features, dtype=object)[~valid_mask].tolist()
    )
    invalid_policy = str(
        compression_plan.get("invalid_feature_policy", "drop")
    ).lower()
    if invalid_policy not in {"drop", "error"}:
        raise ValueError("invalid_feature_policy 只能是 drop 或 error。")
    if dropped_features and invalid_policy == "error":
        raise ValueError(
            f"{group_name} 有无法在 Fit 内估计的因子：{list(dropped_features[:20])}"
        )
    used_features = tuple(
        np.asarray(input_features, dtype=object)[valid_mask].tolist()
    )
    if not used_features:
        raise ValueError(f"{group_name} 在 Fit 内没有可用于 PCA 的有效因子。")

    medians = medians[valid_mask]
    fit_values = fit_values[:, valid_mask]
    fit_values = np.where(np.isfinite(fit_values), fit_values, medians)

    min_dimension = min(fit_values.shape)
    maximum = rule["max_components"]
    candidate = min_dimension if maximum is None else min(min_dimension, maximum)
    if rule["mode"] == "fixed":
        candidate = min(candidate, int(rule["value"]))
    candidate = max(1, int(candidate))
    if rule["min_components"] > candidate:
        raise ValueError("min_components exceeds available Fit dimensions")
    if rule["mode"] == "fixed" and int(rule["value"]) > candidate:
        raise ValueError("fixed component count exceeds available Fit dimensions or max_components")

    solver = str(compression_plan.get("svd_solver", "randomized")).lower()
    if solver not in {"auto", "full", "randomized"}:
        raise ValueError("svd_solver 只能是 auto、full 或 randomized。")
    if solver == "randomized" and candidate >= min_dimension:
        solver = "full"
    pca = PCA(
        n_components=candidate,
        svd_solver=solver,
        random_state=int(compression_plan.get("random_state", 42)),
    )
    pca.fit(fit_values)

    if rule["mode"] == "fixed":
        selected = min(candidate, int(rule["value"]))
        target = None
        target_reached = None
    else:
        target = float(rule["value"])
        cumulative = np.cumsum(pca.explained_variance_ratio_)
        reached = np.flatnonzero(cumulative >= target)
        selected = int(reached[0] + 1) if reached.size else candidate
        selected = min(candidate, max(int(rule["min_components"]), selected))
        target_reached = bool(cumulative[selected - 1] >= target)

    output_features = tuple(
        f"{output_prefix}_PC{number:03d}" for number in range(1, selected + 1)
    )
    output_dtype = str(compression_plan.get("output_dtype", "float32"))
    if output_dtype not in {"float32", "float64"}:
        raise ValueError("output_dtype 只能是 float32 或 float64。")

    plan_for_hash = dict(compression_plan)
    plan_for_hash.pop("output_dir", None)
    plan_fingerprint = sha256(_stable_json(plan_for_hash).encode("utf-8")).hexdigest()
    schema_material = {
        "columns": list(input_features),
        "dtypes": [str(dtype) for dtype in x.dtypes],
        "index_names": list(x.index.names),
    }
    schema_fingerprint = sha256(_stable_json(schema_material).encode("utf-8")).hexdigest()
    grouping_fingerprint = x.attrs.get("factor_grouping_fingerprint")
    retained_ratio = float(pca.explained_variance_ratio_[:selected].sum())
    state_material = {
        "group_name": group_name,
        "fit_start": str(fit_start),
        "fit_end": str(fit_end),
        "fit_row_count": int(fit_mask.sum()),
        "input_features": input_features,
        "used_features": used_features,
        "dropped_features": dropped_features,
        "output_features": output_features,
        "candidate_components": candidate,
        "selected_components": selected,
        "retained_variance_ratio": retained_ratio,
        "target_variance_ratio": target,
        "target_reached": target_reached,
        "component_rule": rule,
        "output_dtype": output_dtype,
        "plan_fingerprint": plan_fingerprint,
        "input_schema_fingerprint": schema_fingerprint,
        "grouping_fingerprint": grouping_fingerprint,
    }
    arrays = [
        medians,
        pca.mean_,
        pca.components_[:selected],
        pca.explained_variance_[:selected],
        pca.explained_variance_ratio_[:selected],
        pca.singular_values_[:selected],
    ]
    fingerprint = _state_fingerprint(state_material, arrays)
    state = PCACompressionState(
        group_name=group_name,
        fit_start=fit_start,
        fit_end=fit_end,
        datetime_level=datetime_level,
        input_features=input_features,
        used_features=used_features,
        dropped_features=dropped_features,
        impute_values=medians.astype(np.float64, copy=True),
        mean=pca.mean_.astype(np.float64, copy=True),
        components=pca.components_[:selected].astype(np.float64, copy=True),
        explained_variance=pca.explained_variance_[:selected].astype(
            np.float64, copy=True
        ),
        explained_variance_ratio=pca.explained_variance_ratio_[:selected].astype(
            np.float64, copy=True
        ),
        singular_values=pca.singular_values_[:selected].astype(np.float64, copy=True),
        output_features=output_features,
        candidate_components=candidate,
        selected_components=selected,
        retained_variance_ratio=retained_ratio,
        target_variance_ratio=target,
        target_reached=target_reached,
        component_rule=rule,
        output_dtype=output_dtype,
        plan_fingerprint=plan_fingerprint,
        input_schema_fingerprint=schema_fingerprint,
        grouping_fingerprint=grouping_fingerprint,
        fingerprint=fingerprint,
    )

    compressed = transform_factor_group(
        x,
        state,
        batch_size=int(compression_plan.get("transform_batch_size", 20_000)),
    )
    artifact_paths: dict[str, str] = {}
    if compression_plan.get("output_dir") is not None:
        artifact_paths = save_pca_state(state, compression_plan["output_dir"])

    audit = {
        "executed": True,
        "method": "pca",
        "group_name": group_name,
        "input_shape": tuple(x.shape),
        "output_shape": tuple(compressed.shape),
        "fit_start": str(fit_start),
        "fit_end": str(fit_end),
        "fit_row_count": int(fit_mask.sum()),
        "input_feature_count": len(input_features),
        "used_feature_count": len(used_features),
        "dropped_feature_count": len(dropped_features),
        "selected_components": selected,
        "retained_variance_ratio": retained_ratio,
        "target_variance_ratio": target,
        "target_reached": target_reached,
        "fit_uses_rows_after_fit_end": False,
        "index_preserved": compressed.index.equals(x.index),
        "plan_fingerprint": plan_fingerprint,
        "input_schema_fingerprint": schema_fingerprint,
        "grouping_fingerprint": grouping_fingerprint,
        "compression_fingerprint": fingerprint,
    }
    return PCACompressionResult(
        x=compressed,
        state=state,
        executed=True,
        method="pca",
        audit=audit,
        artifact_paths=artifact_paths,
    )


__all__ = [
    "PCACompressionResult",
    "PCACompressionState",
    "compress_factor_group",
    "save_pca_state",
    "load_pca_state",
    "transform_factor_group",
]
