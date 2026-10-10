"""Metamorphic leakage regressions for the sector CTA research workflow."""
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from futures_research import cta_strategy
from futures_research.cta_portfolio import (
    build_sector_weights, ensemble_predictions, research_backtest,
)
from futures_research.cta_sector import augment_fit_samples, prepare_sector_dataset
from futures_research.pipeline import ResearchDataset

from .test_cta_portfolio import metadata, portfolios, raw_returns, sample_signals
from .test_cta_sector import MAPPING, dataset
from .test_cta_strategy import mock_models, research_case


def test_test_only_contract_mapping_cannot_change_fit_augmentation():
    ds = prepare_sector_dataset(dataset(), MAPPING)
    kwargs = dict(fit_start="2024-01-02", fit_end="2024-01-03",
                  n_synthetic_per_sector=3, seed=61)
    old, old_audit = augment_fit_samples(ds, MAPPING, **kwargs)
    new_mapping = dict(MAPPING, FUTURE_ALPHA="alpha", FUTURE_BETA="beta")
    new, new_audit = augment_fit_samples(ds, new_mapping, **kwargs)
    pd.testing.assert_frame_equal(old.x, new.x)
    pd.testing.assert_series_equal(old.y, new.y)
    assert old_audit["sectors"] == new_audit["sectors"]
    assert new_audit["weight_universe"] == "eligible_fit_contracts_only"


def test_validation_only_contract_does_not_enter_fit_weight_space():
    ds = dataset()
    extra = ds.subset(ds.x.index[(ds.x.index.get_level_values("Contract") == "AA") &
                               (ds.x.index.get_level_values("Datetime").hour == 11)])
    extra = ResearchDataset(*(v.rename(index={"AA": "LATER"}, level="Contract") for v in
                              [extra.x, extra.y, extra.feature_available_at, extra.label_end]))
    extended = ResearchDataset(*(pd.concat([a, b]) for a, b in zip(
        [ds.x, ds.y, ds.feature_available_at, ds.label_end],
        [extra.x, extra.y, extra.feature_available_at, extra.label_end])))
    mapping = dict(MAPPING, LATER="alpha")
    kwargs = dict(fit_start="2024-01-02 09:00", fit_end="2024-01-02 10:35",
                  n_synthetic_per_sector=3, seed=61)
    old, audit_old = augment_fit_samples(ds, mapping, **kwargs)
    new, audit_new = augment_fit_samples(extended, mapping, **kwargs)
    synthetics = old.x.index.get_level_values("Contract").str.startswith("SYN_")
    pd.testing.assert_frame_equal(old.x.loc[synthetics], new.x.loc[old.x.index[synthetics]])
    pd.testing.assert_series_equal(old.y.loc[synthetics], new.y.loc[old.y.index[synthetics]])
    assert audit_old["sectors"] == audit_new["sectors"]


@pytest.mark.parametrize("mutation", ["late_label", "missing_label", "different_future_outcome"])
def test_future_label_state_never_changes_test_inference_universe_or_book(monkeypatch, tmp_path, mutation):
    train, test, returns, cfg = research_case()
    events = []
    mock_models(monkeypatch, cfg, events)
    changed_y, changed_end = test.y.copy(), test.label_end.copy()
    alpha = test.x.index.get_level_values("Contract").str.startswith("A")
    single_member = test.x.index.get_level_values("Contract") == "A0"
    if mutation == "late_label":
        changed_end.loc[single_member] += pd.Timedelta("2d")
    elif mutation == "missing_label":
        changed_y.loc[single_member] = np.nan
    else:
        changed_y.loc[alpha] = changed_y.loc[alpha] * -777 + .123
    changed = ResearchDataset(test.x, changed_y, test.feature_available_at, changed_end)
    for name, payload in (("baseline", test), ("changed", changed)):
        events.clear()

        def load_test():
            events.append(("test_loader",))
            return payload

        result = cta_strategy.run_sector_strategy(train, load_test, lambda: returns,
                                                  deepcopy(cfg), tmp_path / name)
        assert result["test_audit"]["label_end_used_for_signal_selection"] is False
        assert result["test_audit"]["retained_rows"] == len(test.x)
    for filename in ("model_signals.csv", "ensemble_signals.csv", "target_weights.csv", "research_curve.csv"):
        pd.testing.assert_frame_equal(pd.read_csv(tmp_path / "baseline" / filename),
                                      pd.read_csv(tmp_path / "changed" / filename))


@pytest.mark.parametrize("mode", ["sector_median_excess", "sector_zscore"])
def test_incomplete_sector_targets_are_unknown_without_changing_features(mode):
    ds = dataset()
    modified = ds.y.copy()
    modified.iloc[0] = np.nan
    missing = ResearchDataset(ds.x, modified, ds.feature_available_at, ds.label_end)
    before = prepare_sector_dataset(ds, MAPPING, target_mode=mode)
    after = prepare_sector_dataset(missing, MAPPING, target_mode=mode, allow_missing_labels=True)
    pd.testing.assert_frame_equal(before.x, after.x)
    pd.testing.assert_series_equal(before.feature_available_at, after.feature_available_at)
    affected_time = ds.x.index[0][2]
    affected = ((ds.x.index.get_level_values("Datetime") == affected_time) &
                ds.x.index.get_level_values("Contract").isin(["AA", "AB"]))
    assert after.y.loc[affected].isna().all()
    pd.testing.assert_series_equal(before.y.loc[~affected], after.y.loc[~affected])


def test_signal_timestamp_cannot_precede_decision_in_any_portfolio_entry_point():
    signals = sample_signals()
    signals["signal_available_at"] -= pd.Timedelta("1ns")
    with pytest.raises(ValueError, match="precede.*decision"):
        ensemble_predictions(signals.assign(model="one"), model_weights={"one": 1.})
    with pytest.raises(ValueError, match="precede.*decision"):
        build_sector_weights(signals, metadata(), sector_allocations={"agriculture": .25, "metals": .75})
    book = portfolios()
    returns = raw_returns(book)
    book["signal_available_at"] -= pd.Timedelta("1ns")
    with pytest.raises(ValueError, match="precede.*decision"):
        research_backtest(book, returns)


def test_metric_labels_after_test_cutoff_are_unknown_but_predictions_stay_complete(monkeypatch, tmp_path):
    train, test, returns, cfg = research_case()
    events = []
    mock_models(monkeypatch, cfg, events)
    test.label_end.loc[test.x.index.get_level_values("Contract") == "A0"] = pd.Timestamp(cfg["intervals"]["test"][1])
    original_evaluate = cta_strategy.evaluate_factor_model
    observations = []

    def evaluate(model, x, y, **kwargs):
        if model.sector == "alpha":
            assert y.isna().all()  # The sector median requires the late peer.
            late = x.index.get_level_values("Contract") == "A0"
            assert kwargs["ic_y"].loc[late].isna().all()
            assert kwargs["ic_y"].loc[~late].notna().all()
        else:
            assert y.notna().all()
        observations.append(len(x))
        return original_evaluate(model, x, y, **kwargs)

    monkeypatch.setattr(cta_strategy, "evaluate_factor_model", evaluate)

    def load_test():
        events.append(("test_loader",))
        return test

    manifest = cta_strategy.run_sector_strategy(train, load_test, lambda: returns, cfg, tmp_path / "run")
    assert len(observations) == 8 and set(observations) == {6}
    assert manifest["test_audit"]["labels_missing_for_metrics"] == 6
    assert len(pd.read_csv(tmp_path / "run" / "target_weights.csv")) == len(test.x)


@pytest.mark.parametrize("column", ["return_end", "label_available_at"])
def test_execution_outcomes_after_declared_cutoff_fail_instead_of_changing_book(monkeypatch, tmp_path, column):
    train, test, returns, cfg = research_case()
    events = []
    mock_models(monkeypatch, cfg, events)
    returns[column] = pd.Timestamp(cfg["intervals"]["test"][1])
    books = []
    original_build = cta_strategy.build_sector_weights

    def build(*args, **kwargs):
        book = original_build(*args, **kwargs)
        books.append(book)
        return book

    monkeypatch.setattr(cta_strategy, "build_sector_weights", build)

    def load_test():
        events.append(("test_loader",))
        return test

    with pytest.raises(ValueError, match="execution outcomes.*declared Test end"):
        cta_strategy.run_sector_strategy(train, load_test, lambda: returns, cfg, tmp_path / "run")
    assert len(books) == 1 and len(books[0]) == len(test.x)
    assert books[0].weight.ne(0).any()


@pytest.mark.parametrize("prefix", ["processing", "compression"])
@pytest.mark.parametrize("stage", ["train", "test"])
def test_prior_future_fitted_transform_cannot_be_hidden_by_sector_normalization(monkeypatch, tmp_path, prefix, stage):
    train, test, returns, cfg = research_case()
    events = []
    mock_models(monkeypatch, cfg, events)
    ds = train if stage == "train" else test
    boundary = cfg["intervals"]["fit"][1] if stage == "train" else cfg["intervals"]["test"][0]
    ds.x.attrs[f"{prefix}_method"] = "zscore" if prefix == "processing" else "pca"
    ds.x.attrs[f"{prefix}_fit_end"] = boundary

    def load_test():
        events.append(("test_loader",))
        return test

    with pytest.raises(ValueError, match="fitted at/after"):
        cta_strategy.run_sector_strategy(train, load_test, lambda: returns, cfg, tmp_path / "run")
    assert not any(event[0] == "evaluate" for event in events)
    if stage == "train":
        assert not events


def test_reinvesting_equity_before_held_outcomes_are_available_is_rejected():
    book = portfolios()
    returns = raw_returns(book)
    returns["return"] = np.where(book.weight > 0, .1, -.1)
    first_held = (book.Datetime == book.Datetime.min()) & book.weight.ne(0)
    returns.loc[first_held, "label_available_at"] += pd.Timedelta("15min")
    # Formerly this accepted NAV 1.1 -> 1.21 by sizing the second interval
    # against the first interval's equity 15 minutes before it was observable.
    with pytest.raises(ValueError, match="equity.*unavailable.*next return_start"):
        research_backtest(book, returns, commission_bps=0., slippage_bps=0.)


@pytest.mark.parametrize("unknown", ["missing", "delayed"])
def test_nonheld_outcome_availability_does_not_block_equity_reinvestment(unknown):
    book = portfolios()
    returns = raw_returns(book)
    returns["return"] = np.where(book.weight > 0, .1, -.1)
    nonheld = book.weight.eq(0)
    assert nonheld.any()
    returns.loc[nonheld, "return"] = np.nan
    returns.loc[nonheld, "label_available_at"] = (
        pd.NaT if unknown == "missing" else pd.Timestamp("2025-01-01"))
    result = research_backtest(book, returns, commission_bps=0., slippage_bps=0.)
    np.testing.assert_allclose(result.curves.nav, [1.1, 1.21])
    pd.testing.assert_series_equal(result.curves.equity_available_at,
                                   result.curves.return_end.rename("equity_available_at"))
    assert result.audit["zero_position_outcome_availability"] == "ignored"


def test_held_outcome_unknown_is_rejected_and_default_availability_is_return_end():
    book = portfolios()
    returns = raw_returns(book)
    returns["return"] = np.where(book.weight > 0, .1, -.1)
    implicit = research_backtest(book, returns.drop(columns="label_available_at"),
                                 commission_bps=0., slippage_bps=0.)
    explicit = research_backtest(book, returns, commission_bps=0., slippage_bps=0.)
    pd.testing.assert_frame_equal(implicit.curves, explicit.curves)
    held = book.index[book.weight.ne(0)][0]
    returns.loc[held, "label_available_at"] = pd.NaT
    with pytest.raises(ValueError, match="held instrument label_available_at must be finite"):
        research_backtest(book, returns)


def test_equity_available_exactly_at_next_entry_is_accepted_and_recorded():
    book = portfolios()
    returns = raw_returns(book)
    returns["return"] = np.where(book.weight > 0, .1, -.1)
    first = book.Datetime == book.Datetime.min()
    # A 5-minute shorter first holding interval leaves enough time to observe
    # its delayed outcome exactly when the second interval opens at 09:30.
    returns.loc[first, "return_end"] -= pd.Timedelta("5min")
    result = research_backtest(book, returns, commission_bps=0., slippage_bps=0.)
    assert result.curves.equity_available_at.iloc[0] == result.curves.return_start.iloc[1]
    assert result.audit["equity_availability_policy"] == "held_outcomes_available_no_later_than_next_entry"
    np.testing.assert_allclose(result.curves.nav, [1.1, 1.21])


def test_unheld_late_outcomes_cannot_block_completed_strategy_report(monkeypatch, tmp_path):
    train, test, returns, cfg = research_case()
    events = []
    mock_models(monkeypatch, cfg, events)
    returns["label_available_at"] = returns.return_end
    # A1 is the middle-ranked, zero-weight instrument. Its unknown outcome
    # cannot change this portfolio or whether its realized P&L is reportable.
    returns.loc[returns.Contract.eq("A1"), "label_available_at"] = pd.Timestamp("2099-01-01")
    def loader():
        events.append(("test_loader",))
        return test
    manifest = cta_strategy.run_sector_strategy(train, loader, lambda: returns, cfg, tmp_path / "run")
    assert manifest["status"] == "complete"
    book = pd.read_csv(tmp_path / "run" / "target_weights.csv")
    assert book.loc[book.Contract.eq("A1"), "weight"].eq(0).all()
