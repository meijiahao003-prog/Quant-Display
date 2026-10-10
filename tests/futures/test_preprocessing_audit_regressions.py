"""Upstream transform information times cannot be reset by another transform."""

import numpy as np
import pandas as pd
import pytest

from futures_research.factor_compression import compress_factor_group, transform_factor_group
from futures_research.factor_processing import fit_zscore_state, process_factor_data, transform_zscore
from futures_research.factor_runtime import validate_preprocessing_boundary


EARLY = {"fit_start": "2030-01-01", "fit_end": "2030-01-04"}
LATE = {"fit_start": "2030-01-01", "fit_end": "2030-01-08"}


def fixture(future=None):
    times = pd.date_range("2030-01-01", periods=8)
    index = pd.MultiIndex.from_arrays(
        [times, ["SYNTH"] * 8, times], names=["Date", "Contract", "Datetime"]
    )
    future = future or [[100., 0.], [200., 0.], [300., 0.], [400., 0.]]
    x = pd.DataFrame([[0., 0.], [1., 0.], [2., 1.], [3., 1.], *future], index=index, columns=["a", "b"])
    y = pd.Series(np.arange(8, dtype=float), index=index, name="target")
    return x, y


def plan(bounds):
    return {"method": "pca", "group_name": "g", "component_rule": {"mode": "fixed", "value": 1},
            "svd_solver": "full", **bounds}


def test_future_pca_cannot_be_hidden_by_an_earlier_pca_refit():
    x, _ = fixture()
    alternative, _ = fixture([[0., 100.], [0., 200.], [0., 300.], [0., 400.]])
    future = compress_factor_group(x, plan(LATE))
    changed_future = compress_factor_group(alternative, plan(LATE))
    # The initially leaky feature genuinely depends on the held-out rows.
    assert not np.allclose(future.x.iloc[:4], changed_future.x.iloc[:4])
    with pytest.raises(ValueError, match="prediction cutoff"):
        compress_factor_group(future.x, plan(EARLY))
    assert future.x.attrs["compression_fit_end"] == str(pd.Timestamp(LATE["fit_end"]))


def test_future_pca_cannot_be_hidden_by_transforming_with_early_pca_state():
    x, _ = fixture()
    early = compress_factor_group(x, plan(EARLY))
    early_second = compress_factor_group(early.x, plan(EARLY))
    future = compress_factor_group(x, plan(LATE))
    with pytest.raises(ValueError, match="prediction cutoff"):
        transform_factor_group(future.x, early_second.state)


@pytest.mark.parametrize("entrypoint", ["fit", "process", "transform"])
def test_future_zscore_cannot_be_hidden_by_earlier_standardization(entrypoint):
    x, y = fixture()
    future = process_factor_data(x, y, **LATE)
    state = fit_zscore_state(x, **EARLY)
    with pytest.raises(ValueError, match="prediction cutoff"):
        if entrypoint == "fit":
            fit_zscore_state(future.x, **EARLY)
        elif entrypoint == "process":
            process_factor_data(future.x, y, **EARLY)
        else:
            transform_zscore(future.x, state)


@pytest.mark.parametrize("upstream", ["pca", "zscore"])
def test_different_transform_types_preserve_upstream_information_time(upstream):
    x, y = fixture()
    if upstream == "pca":
        future = compress_factor_group(x, plan(LATE)).x
        with pytest.raises(ValueError, match="prediction cutoff"):
            process_factor_data(future, y, **EARLY)
    else:
        future = process_factor_data(x, y, **LATE).x
        with pytest.raises(ValueError, match="prediction cutoff"):
            compress_factor_group(future, plan(EARLY))


def test_same_inclusive_fit_standardization_then_pca_remains_valid():
    x, y = fixture()
    zscore = process_factor_data(x, y, **EARLY)
    pca = compress_factor_group(zscore.x, plan(EARLY))
    repeated = compress_factor_group(pca.x, plan(EARLY))
    assert np.isfinite(repeated.x.to_numpy()).all()
    assert pd.Timestamp(repeated.x.attrs["processing_fit_end"]) == pd.Timestamp(EARLY["fit_end"])
    assert pd.Timestamp(repeated.x.attrs["compression_fit_end"]) == pd.Timestamp(EARLY["fit_end"])
    validate_preprocessing_boundary(repeated.x, "2030-01-05")
    pd.testing.assert_frame_equal(transform_factor_group(zscore.x, pca.state), pca.x)


@pytest.mark.parametrize("prefix", ["processing", "compression"])
def test_known_transform_without_fit_provenance_cannot_be_overwritten(prefix):
    x, y = fixture()
    state = fit_zscore_state(x, **EARLY)
    pstate = compress_factor_group(x, plan(EARLY)).state
    x.attrs[f"{prefix}_method"] = "upstream_transform"
    for call in (
        lambda: fit_zscore_state(x, **EARLY),
        lambda: process_factor_data(x, y, **EARLY),
        lambda: transform_zscore(x, state),
        lambda: compress_factor_group(x, plan(EARLY)),
        lambda: transform_factor_group(x, pstate),
    ):
        with pytest.raises(ValueError, match="fit-end provenance"):
            call()


def test_disabled_preprocessing_preserves_existing_provenance():
    x, y = fixture()
    late = compress_factor_group(process_factor_data(x, y, **LATE).x, plan(LATE)).x
    attrs = dict(late.attrs)
    unchecked = process_factor_data(late, y, execute=False)
    disabled = compress_factor_group(unchecked.x, {"execute": False})
    identity = compress_factor_group(disabled.x, {"method": "identity"})
    assert identity.x is late
    assert identity.x.attrs == attrs
    with pytest.raises(ValueError, match="prediction cutoff"):
        validate_preprocessing_boundary(identity.x, "2030-01-05")
