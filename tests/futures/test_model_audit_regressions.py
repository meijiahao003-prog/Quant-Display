"""Adversarial model audit regressions; all values and timestamps are synthetic."""
from functools import wraps
from importlib.util import find_spec
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from futures_research import factor_model_testing as testing
from futures_research import factor_model_training as training


def frame_fixture():
    dt = pd.date_range("2030-01-02 09:00", periods=12, freq="30min")
    index = pd.MultiIndex.from_arrays(
        [dt.normalize(), ["SYNTH"] * len(dt), dt], names=training.EXPECTED_INDEX_NAMES
    )
    x = pd.DataFrame({"feature": np.arange(len(dt), dtype=float)}, index=index)
    y = pd.Series(np.arange(len(dt), dtype=float), index=index, name="target")
    metadata = {"label_end": pd.Series(dt + pd.Timedelta(minutes=30), index=index),
                "feature_available_at": pd.Series(dt, index=index)}
    result = SimpleNamespace(
        training_stage="final", refit_full=True, model=object(), method="lightgbm",
        feature_names=("feature",), target="target",
        audit={"config": {}, "run_id": "synthetic_frozen_model", "split_audit": {
            "fit": {"actual_end": "2030-01-01 10:00"},
            "valid": {"actual_end": "2030-01-01 12:00"}},
            "temporal_audit": {"max_training_label_end": "2030-01-01 12:30",
                               "purge_check_executed": True, "fit_as_of": "2030-01-02"}},
    )
    return x, y, metadata, result


def evaluate(x, y, metadata, result, **kwargs):
    with patch.object(testing, "_predict_tree_model", return_value=x["feature"].to_numpy()):
        return testing.evaluate_factor_model(result, x, y, "target", **metadata, **kwargs)


def test_test_error_metrics_keep_training_target_units_with_independent_ic():
    x, y, metadata, model = frame_fixture()
    ic_y = (-y + 100).rename("raw_return")
    result = evaluate(x, y, metadata, model, ic_y=ic_y)
    assert result.overall_metrics["test_rmse"] == 0
    assert result.overall_metrics["test_mae"] == 0
    assert result.overall_metrics["pooled_pearson_ic"] == pytest.approx(-1)
    assert (result.monthly_metrics["rmse"] == 0).all()
    pd.testing.assert_series_equal(result.predictions["actual"], ic_y, check_names=False)
    pd.testing.assert_series_equal(result.predictions["target"], y, check_names=False)
    assert result.audit["error_metrics_target"] == "target"
    changed = evaluate(x, y, metadata, model, ic_y=(ic_y + 10))
    assert result.audit["run_id"] != changed.audit["run_id"]


@pytest.mark.parametrize("unknown", [np.nan, 0.0])
def test_undefined_test_ic_never_discards_or_blocks_predictable_rows(unknown):
    x, y, metadata, model = frame_fixture()
    baseline = evaluate(x, y, metadata, model)
    unavailable = y * 0 + unknown
    result = evaluate(x, unavailable, metadata, model)
    pd.testing.assert_series_equal(result.predictions.prediction, baseline.predictions.prediction)
    assert len(result.predictions) == len(x)
    assert not result.audit["pooled_ic_defined"]
    assert not result.audit["monthly_ic_defined"]
    assert np.isnan(result.overall_metrics["pooled_pearson_ic"])


def test_test_refit_preprocessing_is_rejected_before_prediction():
    x, y, metadata, model = frame_fixture()
    x.attrs.update(processing_method="zscore", processing_fit_end="2030-01-03")
    with patch.object(testing, "_predict_tree_model") as predict:
        with pytest.raises(ValueError, match="prediction cutoff"):
            testing.evaluate_factor_model(model, x, y, "target", **metadata)
        predict.assert_not_called()


def test_test_cannot_precede_declared_freeze_even_if_training_rows_are_old():
    x, _, _, model = frame_fixture()
    model.audit["temporal_audit"]["fit_as_of"] = "2030-01-03"
    with pytest.raises(ValueError, match="freeze time"):
        testing._verify_test_is_after_training(model, pd.DatetimeIndex(x.index.get_level_values("Datetime")))


def test_unnamed_training_label_cannot_bypass_target_column_guard():
    x, y, _, _ = frame_fixture()
    x["independent_horizon_target"] = y
    with pytest.raises(ValueError, match="label leakage"):
        training.validate_training_inputs(x, y.rename(None), "independent_horizon_target")


@pytest.mark.skipif(find_spec("torch") is None, reason="optional torch dependency")
def test_sequence_validation_uses_aligned_independent_ic_without_changing_windows():
    import torch
    x, y, _, _ = frame_fixture()
    # Shuffle input order to check that the independent label follows exactly
    # the same stable contract/time ordering and endpoints as the feature windows.
    order = np.random.default_rng(1).permutation(len(x))
    x, y = x.iloc[order], y.iloc[order]
    ic_y = -y.to_numpy()
    ic_y[0] = np.nan
    split = training._SplitData("valid", x.to_numpy(np.float32), y.to_numpy(), x.index,
                                pd.Timestamp("2030-01-02"), pd.Timestamp("2030-01-03"), ic_y)
    cfg = training.make_training_config("transformer")
    cfg["params"]["window_size"] = 3
    dataset = training._build_window_dataset(split, cfg, torch)
    loader = training._loader(torch, dataset, cfg, shuffle=False)

    class LastFeature(torch.nn.Module):
        def forward(self, values):
            return values[:, -1, 0]

    rmse, raw_ic, label_ic = training._validation_rmse_ic(
        torch, LastFeature(), loader, torch.device("cpu"), ic_targets=dataset.ic_targets
    )
    assert len(dataset) == len(x) - 2
    assert rmse == 0
    assert raw_ic == pytest.approx(-1)
    assert label_ic == pytest.approx(1)
    assert np.isnan(dataset.ic_targets).sum() == 1


@pytest.mark.parametrize("method", ["xgboost", "lightgbm"])
def test_tree_validation_ic_is_the_actual_prediction_for_each_round(method):
    if find_spec(method) is None:
        pytest.skip("optional native tree dependency")
    module = __import__(method)
    rng = np.random.default_rng(17)
    X = rng.normal(size=(96, 3)).astype(np.float32)
    y = X[:, 0] + 0.4 * X[:, 1] ** 2 - 0.3 * X[:, 2]
    ic_y = X[:, 0] - X[:, 1] + 0.2 * X[:, 2]
    dates = pd.date_range("2030-01-01 09:00", periods=48, freq="min").append(
        pd.date_range("2030-01-02 09:00", periods=48, freq="min"))
    index = pd.MultiIndex.from_arrays([dates.normalize(), ["SYNTH"] * 96, dates],
                                     names=training.EXPECTED_INDEX_NAMES)
    features = ("a", "b", "c")
    fit = training._SplitData("fit", X[:48], y[:48], index[:48], dates.min(), dates[48], ic_y[:48])
    valid = training._SplitData("valid", X[48:], y[48:], index[48:], dates[48], dates.max(), ic_y[48:])
    config = training.make_training_config(method)
    config["live_plot"]["enabled"] = False
    if method == "lightgbm":
        config["params"].update(min_data_in_leaf=3, learning_rate=0.3, num_leaves=7)
    else:
        config["params"].update(eta=0.3, max_depth=2)
    config = training._normalise_config(method, config, training_stage="selection", training_iterations=8)
    captured = []
    native_train = module.train

    @wraps(native_train)
    def capture_full_booster(*args, **kwargs):
        booster = native_train(*args, **kwargs)
        captured.append(booster)
        return booster

    with patch.object(module, "train", new=capture_full_booster):
        adapter = training._TRAINING_METHODS[method](config, features)
        result = adapter.fit_selection(fit, valid, training._LiveLossPlot(False, 1, "synthetic"))
    full_booster = captured[0]
    expected = []
    for iteration in range(1, 9):
        if method == "lightgbm":
            prediction = full_booster.predict(valid.X, num_iteration=iteration)
        else:
            matrix = module.DMatrix(valid.X, feature_names=list(features))
            prediction = full_booster.predict(matrix, iteration_range=(0, iteration))
        expected.append(training._pearson_ic(prediction, valid.ic_y))
    assert np.ptp(expected) > 0.005  # The fixture discriminates a cached first-round IC.
    np.testing.assert_allclose(result.history.valid_pearson_ic, expected, atol=1e-6)
    assert result.best_valid_ic_iteration == np.argmax(result.history.valid_pearson_ic.to_numpy()) + 1


def test_training_run_identity_binds_the_independent_validation_ic_label():
    x, y, metadata, _ = frame_fixture()
    cfg = training.make_training_config("xgboost")
    cfg["live_plot"]["enabled"] = False

    class SyntheticAdapter:
        def __init__(self, config, feature_names):
            pass

        def fit_selection(self, fit, valid, plotter):
            history = pd.DataFrame({"iteration": [1], "train_rmse": [1.0],
                                    "valid_rmse": [1.0], "valid_pearson_ic": [0.5]})
            return training._FitOutcome(object(), history, 1, 1.0, 1, 0.5, {})

    def run(ic_y):
        with patch.dict(training._TRAINING_METHODS, {"xgboost": SyntheticAdapter}):
            return training.train_factor_model(
                x, y, "target", "xgboost", "selection", 1,
                "2030-01-02 09:00", "2030-01-02 12:00",
                "2030-01-02 12:00", "2030-01-02 15:00", config=cfg,
                ic_y=ic_y, **metadata,
            )

    first = run(y.rename("raw_return"))
    second = run((y + 100).rename("raw_return"))
    assert first.audit["valid_ic_target_sha256"] != second.audit["valid_ic_target_sha256"]
    assert first.audit["run_id"] != second.audit["run_id"]
    with pytest.raises(ValueError, match="independent IC target"):
        run(y.rename("feature"))
