"""Regression checks for forecast membership and upstream information time."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from futures_research import pipeline
from futures_research.pipeline import ResearchDataset, _prepare, _transform, validate_config
from futures_research.synthetic import make_demo


def _mock_models(monkeypatch, seen):
    def train(**kwargs):
        return SimpleNamespace(best_iteration=2, audit={"stage": kwargs["training_stage"]})
    def evaluate(model, x, y, **kwargs):
        seen.append(y.copy())
        return SimpleNamespace(predictions=pd.DataFrame({"prediction": x.iloc[:, 0]}, index=x.index),
                               overall_metrics=pd.Series({"ic": np.nan}))
    monkeypatch.setattr(pipeline, "train_factor_model", train)
    monkeypatch.setattr(pipeline, "evaluate_factor_model", evaluate)


def test_future_label_completion_and_missingness_do_not_select_forecasts(monkeypatch, tmp_path):
    train, loader, cfg = make_demo()
    original = loader()
    delayed = original.label_end.copy()
    delayed.iloc[::2] = pd.Timestamp(cfg["intervals"]["test"][1]) + pd.Timedelta("1d")
    missing = original.y.copy()
    missing.iloc[1::3] = np.nan
    changed = ResearchDataset(original.x, missing, original.feature_available_at, delayed)
    seen = []
    _mock_models(monkeypatch, seen)
    for name, test in (("baseline", original), ("unavailable_outcomes", changed)):
        pipeline.run_research(train, lambda: test, cfg, tmp_path / name)
    pd.testing.assert_frame_equal(pd.read_csv(tmp_path / "baseline" / "signals.csv"),
                                  pd.read_csv(tmp_path / "unavailable_outcomes" / "signals.csv"))
    assert seen[0].notna().all()
    assert seen[1].iloc[::2].isna().all()
    assert seen[1].iloc[1::3].isna().all()
    assert len(seen[1]) == len(original.x)


def test_all_test_labels_unavailable_still_exports_full_decision_panel(monkeypatch, tmp_path):
    train, loader, cfg = make_demo()
    ds = loader()
    unknown = ResearchDataset(ds.x, ds.y * np.nan, ds.feature_available_at, ds.label_end)
    seen = []
    _mock_models(monkeypatch, seen)
    result = pipeline.run_research(train, lambda: unknown, cfg, tmp_path / "unknown")
    assert result["test_split_audit"]["scoreable_target_rows"] == 0
    assert result["test_split_audit"]["prediction_rows"] == len(ds.x)
    assert seen[0].isna().all()
    assert len(pd.read_csv(tmp_path / "unknown" / "signals.csv")) == len(ds.x)


def test_test_loader_cannot_change_nested_frozen_configuration(monkeypatch, tmp_path):
    train, loader, cfg = make_demo()
    expected = deepcopy(cfg)
    seen = []
    _mock_models(monkeypatch, seen)
    def mutating_loader():
        ds = loader()
        cfg["intervals"]["test"][0] = "2099-01-01"
        cfg["groups"]["core"].clear()
        cfg["models"].clear()
        return ds
    result = pipeline.run_research(train, mutating_loader, cfg, tmp_path / "frozen")
    assert result["models"] == expected["models"]
    assert len(seen[0]) == 240


@pytest.mark.parametrize("prefix", ["processing", "compression"])
def test_future_upstream_transform_cannot_be_laundered_by_new_fit(prefix):
    ds, _, cfg = make_demo()
    ds.x.attrs.update({f"{prefix}_method": "test_transform", f"{prefix}_fit_end": cfg["intervals"]["test"][1]})
    with pytest.raises(ValueError, match="prediction cutoff"):
        _prepare(ds, cfg)


def test_future_upstream_test_transform_rejected_before_frozen_transform():
    ds, loader, cfg = make_demo()
    _, z, pca, _ = _prepare(ds, cfg)
    test = loader()
    test.x.attrs.update(processing_method="zscore", processing_fit_end=cfg["intervals"]["test"][1])
    with pytest.raises(ValueError, match="prediction cutoff"):
        _transform(test, cfg, z, pca)


def test_missing_labels_are_opt_in_for_test_and_never_allowed_for_fit():
    ds, _, _ = make_demo()
    raw = ds.x.join(ds.y).join(ds.label_end).join(ds.feature_available_at).reset_index()
    raw[ds.y.name] = np.nan
    with pytest.raises(ValueError, match="labels must be finite"):
        ResearchDataset.from_frame(raw, features=list(ds.x), target=ds.y.name)
    test = ResearchDataset.from_frame(raw, features=list(ds.x), target=ds.y.name, allow_missing_labels=True)
    test.validate(allow_missing_labels=True)
    with pytest.raises(ValueError, match="labels must be finite"):
        test.validate()
    raw[ds.y.name] = np.inf
    with pytest.raises(ValueError, match="labels must be finite"):
        ResearchDataset.from_frame(raw, features=list(ds.x), target=ds.y.name, allow_missing_labels=True)


def test_direct_dataset_rejects_target_column_and_duplicate_instrument_timestamp():
    ds, _, _ = make_demo()
    contaminated = ResearchDataset(ds.x.assign(**{ds.y.name: ds.y}), ds.y, ds.feature_available_at, ds.label_end)
    with pytest.raises(ValueError, match="target column"):
        contaminated.validate()
    row = ds.x.index[0]
    idx = pd.MultiIndex.from_tuples([row, (row[0] + pd.Timedelta("1d"), row[1], row[2])], names=ds.x.index.names)
    invalid = ResearchDataset(pd.DataFrame({"f": [1., 2.]}, index=idx), pd.Series([.1, .2], index=idx, name="y"),
                              pd.Series(row[2], index=idx), pd.Series(row[2] + pd.Timedelta("30min"), index=idx))
    with pytest.raises(ValueError, match="multiple Date"):
        invalid.validate()


@pytest.mark.parametrize("value", [1.5, True, -1])
def test_seed_and_iterations_are_not_silently_coerced(value):
    _, _, cfg = make_demo()
    cfg["seed"] = value
    with pytest.raises(ValueError, match="seed"):
        validate_config(cfg)
    cfg["seed"] = 7
    cfg["iterations"]["xgboost"] = value
    with pytest.raises(ValueError, match="iterations"):
        validate_config(cfg)
