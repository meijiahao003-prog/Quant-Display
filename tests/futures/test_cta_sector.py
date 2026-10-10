import numpy as np
import pandas as pd
import pytest

from futures_research.cta_sector import augment_fit_samples, prepare_sector_dataset
from futures_research.pipeline import ResearchDataset


MAPPING = {"AA": "alpha", "AB": "alpha", "BA": "beta", "BB": "beta"}


def dataset():
    rows = []
    for t in pd.date_range("2024-01-02 09:00", periods=3, freq="h"):
        for j, contract in enumerate(MAPPING):
            rows.append({
                "Date": t.normalize(), "Datetime": t, "Contract": contract,
                "feature": [1., 3., 10., 20.][j] + t.hour,
                "constant": [5., 5., 7., 7.][j],
                "missing": [np.nan, 2., np.nan, np.nan][j],
                "return_30m": [0.01, 0.05, 0.2, 0.2][j],
                "feature_available_at": t - pd.Timedelta(minutes=[5, 1, 7, 2][j]),
                "label_end": t + pd.Timedelta(minutes=[30, 35, 31, 32][j]),
            })
    return ResearchDataset.from_frame(pd.DataFrame(rows),
                                      features=["feature", "constant", "missing"],
                                      target="return_30m")


def test_preparation_uses_same_timestamp_sector_and_preserves_missing_features():
    ds = dataset()
    result = prepare_sector_dataset(ds, MAPPING)
    assert result.y.name == "sector_median_excess_return_30m"
    for contract, value in {"AA": -1., "AB": 1., "BA": -1., "BB": 1.}.items():
        np.testing.assert_allclose(result.x.xs(contract, level="Contract").feature, value)
    assert result.x.constant.eq(0).all()
    pd.testing.assert_frame_equal(result.x.isna(), ds.x.isna())
    assert result.x.xs("AB", level="Contract")["missing"].eq(0).all()
    np.testing.assert_allclose(result.y.xs("AA", level="Contract"), -.02)
    np.testing.assert_allclose(result.y.xs("AB", level="Contract"), .02)
    np.testing.assert_allclose(result.y.xs("BA", level="Contract"), 0)
    pd.testing.assert_series_equal(ds.y, dataset().y)


def test_optional_notebook_target_and_singleton_constant_semantics():
    ds = dataset()
    result = prepare_sector_dataset(ds, MAPPING, target_mode="sector_zscore")
    np.testing.assert_allclose(result.y.xs("AA", level="Contract"), -1)
    np.testing.assert_allclose(result.y.xs("AB", level="Contract"), 1)
    np.testing.assert_allclose(result.y.xs("BA", level="Contract"), 0)
    single = ds.subset(ds.x.index[ds.x.index.get_level_values("Contract") == "AB"])
    result = prepare_sector_dataset(single, MAPPING)
    assert result.x.eq(0).all().all()
    assert result.y.eq(0).all()


def test_cross_section_dependency_times_propagate_within_sector_only():
    ds = dataset()
    result = prepare_sector_dataset(ds, MAPPING)
    for contract in MAPPING:
        observed = result.subset(result.x.index[result.x.index.get_level_values("Contract") == contract])
        times = observed.x.index.get_level_values("Datetime")
        expected_delay = 1 if MAPPING[contract] == "alpha" else 2
        expected_horizon = 35 if MAPPING[contract] == "alpha" else 32
        assert (pd.DatetimeIndex(observed.feature_available_at) == times - pd.Timedelta(minutes=expected_delay)).all()
        assert (pd.DatetimeIndex(observed.label_end) == times + pd.Timedelta(minutes=expected_horizon)).all()


def test_future_changes_leave_historical_cross_sections_unchanged():
    ds = dataset()
    cutoff = pd.Timestamp("2024-01-02 11:00")
    later = ds.x.index.get_level_values("Datetime") >= cutoff
    changed_x, changed_y = ds.x.copy(), ds.y.copy()
    changed_x.loc[later] *= 1000
    changed_y.loc[later] *= -800
    changed_ends = ds.label_end.copy()
    changed_ends.loc[later] += pd.Timedelta(days=2)
    mutated = ResearchDataset(changed_x, changed_y, ds.feature_available_at, changed_ends)
    old = prepare_sector_dataset(ds, MAPPING)
    new = prepare_sector_dataset(mutated, MAPPING)
    pd.testing.assert_frame_equal(old.x.loc[~later], new.x.loc[~later])
    pd.testing.assert_series_equal(old.y.loc[~later], new.y.loc[~later])
    pd.testing.assert_series_equal(old.label_end.loc[~later], new.label_end.loc[~later])


@pytest.mark.parametrize("problem,match", [
    ("unknown", "no mapping"), ("empty_sector", "non-empty"),
    ("metadata_order", "exactly"), ("duplicate", "duplicate"),
    ("short_horizon", "30-minute"), ("nonfinite_label", "finite"),
    ("infinite_feature", "infinity"), ("synthetic", "SYN_"),
    ("contract_datetime_duplicate", "multiple Date"),
    ("inconsistent_trading_date", "one exchange trading Date"),
])
def test_preparation_rejects_invalid_input(problem, match):
    ds, mapping = dataset(), dict(MAPPING)
    if problem == "unknown":
        del mapping["AA"]
    elif problem == "empty_sector":
        mapping["AA"] = " "
    elif problem == "metadata_order":
        ds = ResearchDataset(ds.x, ds.y, ds.feature_available_at.iloc[::-1], ds.label_end)
    elif problem == "duplicate":
        ds = ResearchDataset(*(pd.concat([v, v.iloc[:1]]) for v in
                               [ds.x, ds.y, ds.feature_available_at, ds.label_end]))
    elif problem == "short_horizon":
        ds.label_end.iloc[0] = ds.x.index[0][2] + pd.Timedelta(minutes=29)
    elif problem == "nonfinite_label":
        ds.y.iloc[0] = np.nan
    elif problem == "infinite_feature":
        ds.x.iloc[0, 0] = np.inf
    elif problem == "synthetic":
        ds = ResearchDataset(*(v.rename(index={"AA": "SYN_alpha_0000"}, level="Contract")
                               for v in [ds.x, ds.y, ds.feature_available_at, ds.label_end]))
    elif problem == "contract_datetime_duplicate":
        ds = ResearchDataset(*(pd.concat([v, v.iloc[:1].rename(
            index={pd.Timestamp("2024-01-02"): pd.Timestamp("2024-01-03")}, level="Date")])
            for v in [ds.x, ds.y, ds.feature_available_at, ds.label_end]))
    elif problem == "inconsistent_trading_date":
        index_frame = ds.x.index.to_frame(index=False)
        index_frame.loc[0, "Date"] += pd.Timedelta(days=1)
        new_index = pd.MultiIndex.from_frame(index_frame)
        ds = ResearchDataset(*(v.set_axis(new_index) for v in
                               [ds.x, ds.y, ds.feature_available_at, ds.label_end]))
    with pytest.raises((ValueError, TypeError), match=match):
        prepare_sector_dataset(ds, mapping)


def test_augmentation_has_fixed_weights_fit_only_sources_and_strict_missingness():
    ds = prepare_sector_dataset(dataset(), MAPPING)
    fit_end = pd.Timestamp("2024-01-02 10:35")
    augmented, audit = augment_fit_samples(ds, MAPPING, fit_start="2024-01-02 09:00",
                                          fit_end=fit_end, n_synthetic_per_sector=2, seed=3)
    pd.testing.assert_frame_equal(augmented.x.loc[ds.x.index], ds.x)
    pd.testing.assert_series_equal(augmented.y.loc[ds.y.index], ds.y)
    synthetic = augmented.x.index.get_level_values("Contract").str.startswith("SYN_")
    sx = augmented.x.loc[synthetic]
    assert len(sx) == 6
    assert augmented.x.index.is_unique
    assert (sx.index.get_level_values("Datetime") < fit_end).all()
    assert (augmented.label_end.loc[synthetic] < fit_end).all()
    assert sx["missing"].isna().all()
    assert audit["synthetic_rows"] == 6
    assert audit["excluded_fit_rows_label_boundary"] == 2
    assert audit["synthetic_missing_feature_values"] == 6
    for sector, state in audit["sectors"].items():
        weights = np.asarray(state["weights"])
        np.testing.assert_allclose(weights.sum(axis=1), 1)
        for t in sx.index.get_level_values("Datetime").unique():
            rows = sx.loc[(sx.index.get_level_values("Datetime") == t) &
                          sx.index.get_level_values("Contract").isin(state["synthetic_contracts"])]
            if rows.empty:
                continue
            source_index = pd.MultiIndex.from_tuples(
                [(t.normalize(), c, t) for c in state["members"]], names=ds.x.index.names)
            np.testing.assert_allclose(rows.feature, weights @ ds.x.loc[source_index, "feature"])
            np.testing.assert_allclose(augmented.y.loc[rows.index], weights @ ds.y.loc[source_index])
            assert augmented.feature_available_at.loc[rows.index].eq(ds.feature_available_at.loc[source_index].max()).all()
            assert augmented.label_end.loc[rows.index].eq(ds.label_end.loc[source_index].max()).all()


def test_augmentation_is_reproducible_and_unaffected_by_valid_test_values():
    ds = prepare_sector_dataset(dataset(), MAPPING)
    later = ds.x.index.get_level_values("Datetime") >= pd.Timestamp("2024-01-02 11:00")
    x, y = ds.x.copy(), ds.y.copy()
    x.loc[later] = 900
    y.loc[later] = -900
    changed = ResearchDataset(x, y, ds.feature_available_at, ds.label_end)
    kwargs = dict(fit_start="2024-01-02 09:00", fit_end="2024-01-02 10:35", seed=11)
    first, audit1 = augment_fit_samples(ds, MAPPING, **kwargs)
    second, audit2 = augment_fit_samples(changed, MAPPING, **kwargs)
    repeated, audit3 = augment_fit_samples(ds, dict(reversed(list(MAPPING.items()))), **kwargs)
    syn = first.x.index.get_level_values("Contract").str.startswith("SYN_")
    pd.testing.assert_frame_equal(first.x.loc[syn], second.x.loc[syn])
    pd.testing.assert_series_equal(first.y.loc[syn], second.y.loc[syn])
    pd.testing.assert_frame_equal(first.x, repeated.x)
    assert audit1["sectors"] == audit2["sectors"] == audit3["sectors"]
    assert audit1["input_fingerprints"] != audit2["input_fingerprints"]


def test_augmentation_renormalizes_missing_contract_weights_and_skips_singletons():
    raw = dataset()
    mapping = dict(MAPPING, AC="alpha")  # fixed universe includes an absent member
    kwargs = dict(fit_start="2024-01-02 09:00", fit_end="2024-01-02 10:35", seed=8)
    augmented, audit = augment_fit_samples(raw, mapping, **kwargs)
    state = audit["sectors"]["alpha"]
    weight = np.asarray(state["weights"])[0, :2]
    weight /= weight.sum()
    t = pd.Timestamp("2024-01-02 09:00")
    row = (t.normalize(), state["synthetic_contracts"][0], t)
    assert augmented.x.loc[row, "feature"] == pytest.approx(weight @ np.array([10., 12.]))
    assert audit["skipped_groups_fewer_than_two"] == 1  # AA remains alone at 10:00
    assert not any(c == state["synthetic_contracts"][0] and dt.hour == 10
                   for _, c, dt in augmented.x.index)


@pytest.mark.parametrize("kwargs,match", [
    ({"fit_start": "2024-01-03"}, "Fit"),
    ({"alpha": 0}, "alpha"), ({"alpha": np.nan}, "alpha"),
    ({"n_synthetic_per_sector": 1.5}, "non-negative integer"),
    ({"seed": -1}, "seed"),
])
def test_augmentation_rejects_invalid_parameters(kwargs, match):
    defaults = dict(fit_start="2024-01-02", fit_end="2024-01-03")
    defaults.update(kwargs)
    with pytest.raises(ValueError, match=match):
        augment_fit_samples(dataset(), MAPPING, **defaults)


def test_augmentation_can_be_explicitly_disabled():
    ds = dataset()
    augmented, audit = augment_fit_samples(ds, MAPPING, fit_start="2024-01-02",
                                          fit_end="2024-01-03", n_synthetic_per_sector=0)
    assert augmented is ds
    assert audit["synthetic_rows"] == 0
