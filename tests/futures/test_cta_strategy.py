"""Orchestration contracts with lightweight estimators and real portfolio logic."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from futures_research import cta_strategy
from futures_research.pipeline import ResearchDataset


def research_case():
    contracts = ["A0", "A1", "A2", "B0", "B1", "B2"]
    metadata = [{"Contract": c, "sector": "alpha" if c.startswith("A") else "beta",
                 "product": c[0] + ("0" if c[-1] in "01" else "1"),
                 "role": "secondary" if c[-1] == "1" else "main",
                 "metadata_available_at": "2024-01-01"} for c in contracts]
    rows = []
    for day in pd.date_range("2024-01-02", periods=3):
        for hour in (9, 10):
            t = day + pd.Timedelta(hours=hour)
            for j, c in enumerate(contracts):
                rows.append({"Date": day, "Datetime": t, "Contract": c,
                             "feature_available_at": t, "label_end": t + pd.Timedelta("30min"),
                             "factor": float(j + hour), "return_30m": (j + 1) / 1000})
    frame = pd.DataFrame(rows)
    train = ResearchDataset.from_frame(frame[frame.Datetime < "2024-01-04"],
                                       features=["factor"], target="return_30m")
    test = ResearchDataset.from_frame(frame[frame.Datetime >= "2024-01-04"],
                                      features=["factor"], target="return_30m")
    returns = frame.loc[frame.Datetime >= "2024-01-04", ["Date", "Contract", "Datetime", "return_30m"]].rename(columns={"return_30m": "return"})
    returns["return_start"] = returns.Datetime
    returns["return_end"] = returns.Datetime + pd.Timedelta("30min")
    returns = returns.sort_values(["Datetime", "Contract"]).reset_index(drop=True)
    models = ["xgboost", "lightgbm", "transformer", "itransformer"]
    config = {
        "seed": 17, "timezone": "Asia/Shanghai", "groups": {"all": ["factor"]},
        "intervals": {"fit": ["2024-01-02", "2024-01-03"],
                      "valid": ["2024-01-03", "2024-01-04"],
                      "test": ["2024-01-04", "2024-01-05"]},
        "raw_target_definition": "uncentered_forward_simple_return_30m",
        "contract_metadata": metadata, "models": models,
        "model_weights": dict.fromkeys(models, .25),
        "sector_allocations": {"alpha": .6, "beta": .4},
        "iterations": dict.fromkeys(models, 9),
        "augmentation": {"n_synthetic_per_sector": 1},
        "selection_rule": "validation_raw_ic",
    }
    return train, test, returns, config


def mock_models(monkeypatch, config, events, *, outcomes=None):
    mapping = {m["Contract"]: m["sector"] for m in config["contract_metadata"]}
    outcomes = outcomes if outcomes is not None else {"alter": False}

    def train(**kwargs):
        real = kwargs["x"].index.get_level_values("Contract")
        sector = {mapping[c] for c in real if not c.startswith("SYN_")}
        assert len(sector) == 1  # No accidental pooled-sector training.
        sector = sector.pop()
        stage, method = kwargs["training_stage"], kwargs["method"]
        assert kwargs["target"] == "sector_median_excess_return_30m"
        assert kwargs["x"].index.equals(kwargs["ic_y"].index)
        synthetic = real.str.startswith("SYN_")
        assert kwargs["ic_y"].loc[synthetic].isna().all()
        assert kwargs["ic_y"].loc[~synthetic].notna().all()
        if stage == "final":
            assert kwargs["training_iterations"] == 3  # Valid raw IC selection, not RMSE.
        events.append((stage, sector, method))
        return SimpleNamespace(best_iteration=2, best_validation_ic_iteration=3,
                               audit={"mock_estimator": True}, sector=sector, method=method)

    def evaluate(model, x, y, **kwargs):
        assert events.count(("test_loader",)) == 1
        events.append(("evaluate", model.sector, model.method))
        score = np.asarray([int(c[-1]) for c in x.index.get_level_values("Contract")], dtype=float)
        predictions = pd.DataFrame({"prediction": score, "actual": y,
                                    "pairwise_finite": True}, index=x.index)
        if outcomes["alter"]:
            predictions["actual"] = np.nan
            predictions["pairwise_finite"] = False
        if outcomes.get("missing_forecast"):
            predictions.loc[x.index.get_level_values("Contract") == "A1", "prediction"] = np.nan
        return SimpleNamespace(predictions=predictions, overall_metrics=pd.Series({"ic": .2}))

    monkeypatch.setattr(cta_strategy, "make_training_config", lambda *a, **k: {"live_plot": {}, "params": {}})
    monkeypatch.setattr(cta_strategy, "train_factor_model", train)
    monkeypatch.setattr(cta_strategy, "evaluate_factor_model", evaluate)
    monkeypatch.setattr(cta_strategy.importlib.metadata, "version", lambda _: "test-estimator")


def test_all_models_freeze_before_test_and_weights_freeze_before_execution_returns(monkeypatch, tmp_path):
    train, test, returns, cfg = research_case()
    events, frozen_books = [], []
    mock_models(monkeypatch, cfg, events)
    real_build = cta_strategy.build_sector_weights

    def build(*args, **kwargs):
        result = real_build(*args, **kwargs)
        frozen_books.append(result.copy())
        events.append(("weights_built",))
        return result

    monkeypatch.setattr(cta_strategy, "build_sector_weights", build)

    def load_test():
        completed = {(sector, method) for stage, sector, method in events if stage == "final"}
        assert completed == {(sector, model) for sector in ("alpha", "beta") for model in cfg["models"]}
        assert (tmp_path / "run" / "selection.json").is_file()
        events.append(("test_loader",))
        return test

    def load_returns():
        assert len(frozen_books) == 1
        assert events[-1] == ("weights_built",)
        assert frozen_books[0].weight.ne(0).any()
        events.append(("execution_returns_loader",))
        return returns

    manifest = cta_strategy.run_sector_strategy(train, load_test, load_returns, cfg, tmp_path / "run", synthetic=True)
    assert manifest["status"] == "complete"
    assert manifest["all_sector_models_frozen_before_test_loader"]
    assert manifest["weights_built_before_execution_returns_loader"]
    assert not manifest["prediction_eligibility_uses_future_returns"]
    assert manifest["full_execution_engine"] is False
    assert events.count(("test_loader",)) == events.count(("execution_returns_loader",)) == 1
    saved = pd.read_csv(tmp_path / "run" / "target_weights.csv")
    np.testing.assert_allclose(saved.weight, frozen_books[0].weight)


def test_actual_and_pairwise_finite_never_select_the_live_book(monkeypatch, tmp_path):
    train, test, returns, cfg = research_case()
    events, outcomes = [], {"alter": False}
    mock_models(monkeypatch, cfg, events, outcomes=outcomes)
    for name, changed in (("original", False), ("future_values_changed", True)):
        events.clear()
        outcomes["alter"] = changed

        def load_test():
            events.append(("test_loader",))
            return test

        cta_strategy.run_sector_strategy(train, load_test, lambda: returns, cfg, tmp_path / name)
    old = pd.read_csv(tmp_path / "original" / "target_weights.csv")
    new = pd.read_csv(tmp_path / "future_values_changed" / "target_weights.csv")
    pd.testing.assert_frame_equal(old, new)
    assert new.weight.ne(0).any()  # Would fail if pairwise_finite filtered the universe.
    signals = pd.read_csv(tmp_path / "future_values_changed" / "model_signals.csv")
    assert not {"actual", "pairwise_finite", "return_30m"}.intersection(signals.columns)


def test_missing_forecasts_exclude_model_incomplete_samples_without_using_labels(monkeypatch, tmp_path):
    train, test, returns, cfg = research_case()
    events = []
    mock_models(monkeypatch, cfg, events, outcomes={"alter": True, "missing_forecast": True})

    def load_test():
        events.append(("test_loader",))
        return test

    manifest = cta_strategy.run_sector_strategy(train, load_test, lambda: returns, cfg, tmp_path / "run")
    book = pd.read_csv(tmp_path / "run" / "target_weights.csv")
    assert "A1" not in set(book.Contract)
    assert "A0" in set(book.Contract)
    assert manifest["excluded_model_rows_without_all_predictions"] == 8


@pytest.mark.parametrize("declaration", [None, "sector_median_excess", "sector_zscore", "return_30m"])
def test_raw_target_declaration_prevents_implicit_double_centering(monkeypatch, tmp_path, declaration):
    train, _, _, cfg = research_case()
    cfg["raw_target_definition"] = declaration

    def forbidden(*args, **kwargs):
        pytest.fail("invalid target declaration must fail before training or loading Test")

    monkeypatch.setattr(cta_strategy, "train_factor_model", forbidden)
    with pytest.raises(ValueError, match="uncentered"):
        cta_strategy.run_sector_strategy(train, forbidden, forbidden, cfg, tmp_path / "invalid")
    assert not (tmp_path / "invalid").exists()


@pytest.mark.parametrize("problem,match", [
    ("missing_model_weight", "model weights"), ("negative_model_weight", "model weights"),
    ("nonfinite_model_weight", "model weights"), ("wrong_total_weight", "model weights"),
    ("missing_allocation", "sector allocations"), ("duplicate_contract", "contract_metadata"),
    ("missing_metadata_field", "contract_metadata"), ("unsafe_sector", "sector IDs"),
    ("missing_metadata_availability", "contract_metadata"),
])
def test_bad_frozen_configuration_fails_before_any_training(monkeypatch, tmp_path, problem, match):
    train, _, _, cfg = research_case()
    cfg = deepcopy(cfg)
    if problem == "missing_model_weight":
        del cfg["model_weights"]["lightgbm"]
    elif problem == "negative_model_weight":
        cfg["model_weights"]["lightgbm"] = -.25
    elif problem == "nonfinite_model_weight":
        cfg["model_weights"]["lightgbm"] = np.nan
    elif problem == "wrong_total_weight":
        cfg["model_weights"]["lightgbm"] = .3
    elif problem == "missing_allocation":
        del cfg["sector_allocations"]["alpha"]
    elif problem == "duplicate_contract":
        cfg["contract_metadata"].append(dict(cfg["contract_metadata"][0]))
    elif problem == "missing_metadata_field":
        for row in cfg["contract_metadata"]:
            del row["role"]
    elif problem == "missing_metadata_availability":
        for row in cfg["contract_metadata"]:
            del row["metadata_available_at"]
    elif problem == "unsafe_sector":
        cfg["contract_metadata"][0]["sector"] = "../alpha"

    def forbidden(*args, **kwargs):
        pytest.fail("invalid fixed configuration must fail before any training/loading")

    monkeypatch.setattr(cta_strategy, "train_factor_model", forbidden)
    with pytest.raises(ValueError, match=match):
        cta_strategy.run_sector_strategy(train, forbidden, forbidden, cfg, tmp_path / "invalid")
    assert not (tmp_path / "invalid").exists()


def test_future_sector_membership_cannot_influence_training(monkeypatch, tmp_path):
    train, _, _, cfg = research_case()
    for row in cfg["contract_metadata"]:
        row["metadata_available_at"] = "2024-01-05"

    def forbidden(*args, **kwargs):
        pytest.fail("future sector membership must fail before training or reading outcomes")

    monkeypatch.setattr(cta_strategy, "train_factor_model", forbidden)
    with pytest.raises(ValueError, match="metadata must be known"):
        cta_strategy.run_sector_strategy(train, forbidden, forbidden, cfg, tmp_path / "run")


def test_test_only_contract_also_requires_point_in_time_metadata(monkeypatch, tmp_path):
    train, test, _, cfg = research_case()
    cfg["contract_metadata"].append({"Contract": "C0", "sector": "alpha", "product": "C",
                                     "role": "main", "metadata_available_at": "2024-01-05"})
    test = ResearchDataset(*(v.rename(index={"A2": "C0"}, level="Contract")
                             for v in [test.x, test.y, test.feature_available_at, test.label_end]))
    events = []
    mock_models(monkeypatch, cfg, events)

    def load_test():
        events.append(("test_loader",))
        return test

    def forbidden(*args, **kwargs):
        pytest.fail("unknown Test-time sector membership must fail before evaluation or returns")

    monkeypatch.setattr(cta_strategy, "evaluate_factor_model", forbidden)
    with pytest.raises(ValueError, match="metadata must be known"):
        cta_strategy.run_sector_strategy(train, load_test, forbidden, cfg, tmp_path / "run")
    assert events.count(("test_loader",)) == 1
