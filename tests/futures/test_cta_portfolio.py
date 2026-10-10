import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from futures_research.cta_portfolio import (
    build_sector_weights,
    ensemble_predictions,
    research_backtest,
)


def sample_signals(periods=2):
    rows = []
    for timestamp in pd.date_range("2024-01-02 09:00", periods=periods, freq="30min"):
        for sector, products in (("agriculture", ("A", "B", "C", "D")),
                                 ("metals", ("E", "F", "G", "H"))):
            for i, product in enumerate(products):
                rows.append({"Date": timestamp.normalize(), "Contract": product + "-delivery",
                             "Datetime": timestamp, "sector": sector, "prediction": float(i),
                             "signal_available_at": timestamp})
    return pd.DataFrame(rows)


def metadata():
    return {product + "-delivery": {"sector": "agriculture" if product in "ABCD" else "metals",
                                   "product": product, "role": "main" if product in "ABEF" else "secondary"}
            for product in "ABCDEFGH"}


def portfolios(signals=None, **kwargs):
    return build_sector_weights(sample_signals() if signals is None else signals, metadata(),
                                sector_allocations={"agriculture": .25, "metals": .75}, **kwargs)


def raw_returns(weights):
    result = weights[["Date", "Contract", "Datetime"]].copy()
    result["return_start"] = result.Datetime
    result["return_end"] = result.Datetime + pd.Timedelta("30min")
    result["return"] = .02
    result["label_available_at"] = result.return_end
    return result


class PortfolioTests(unittest.TestCase):
    def test_fixed_sector_allocations_and_flat_sector_cash(self):
        signals = sample_signals()
        signals.loc[signals.sector.eq("metals"), "prediction"] = 1.
        result = portfolios(signals)
        self.assertTrue(result.loc[result.sector.eq("metals"), "weight"].eq(0).all())
        sums = result.groupby("Datetime").weight.agg(["sum", lambda x: x.abs().sum()])
        np.testing.assert_allclose(sums.iloc[:, 0], 0.)
        np.testing.assert_allclose(sums.iloc[:, 1], .25)
        absent = portfolios(signals[signals.sector.eq("agriculture")])
        np.testing.assert_allclose(absent.groupby("Datetime").weight.apply(lambda x: x.abs().sum()), .25)

    def test_future_returns_never_change_portfolio_selection(self):
        signals = sample_signals()
        expected = portfolios(signals)
        signals["actual"] = np.nan
        signals["return"] = [-99., 99.] * (len(signals) // 2)
        assert_frame_equal(expected, portfolios(signals))

    def test_explicit_main_secondary_and_both(self):
        for contract_set in ("main", "secondary", "both"):
            with self.subTest(contract_set=contract_set):
                result = portfolios(contract_set=contract_set)
                if contract_set != "both":
                    self.assertTrue(result.loc[result.role.ne(contract_set), "weight"].eq(0).all())
                np.testing.assert_allclose(result.groupby("Datetime").weight.sum(), 0.)
                np.testing.assert_allclose(result.groupby("Datetime").weight.apply(lambda x: x.abs().sum()), 1.)

    def test_single_instrument_sector_and_tied_scores_stay_cash(self):
        signals = sample_signals()
        signals = signals[signals.Contract.isin(["A-delivery", "B-delivery", "E-delivery"])]
        result = portfolios(signals, contract_set="main")
        self.assertTrue(result.loc[result.sector.eq("metals"), "weight"].eq(0).all())
        np.testing.assert_allclose(result.groupby("Datetime").weight.apply(lambda x: x.abs().sum()), .25)
        signals["prediction"] = 1.
        self.assertTrue(portfolios(signals).weight.eq(0).all())

    def test_rejects_ambiguous_or_future_metadata(self):
        meta = metadata()
        meta["B-delivery"]["product"] = "A"
        with self.assertRaisesRegex(ValueError, "same product/role"):
            build_sector_weights(sample_signals(), meta, sector_allocations={"agriculture": .25, "metals": .75})
        meta = pd.DataFrame.from_dict(metadata(), orient="index").rename_axis("Contract").reset_index()
        meta["metadata_available_at"] = pd.Timestamp("2024-01-03")
        with self.assertRaisesRegex(ValueError, "unavailable"):
            build_sector_weights(sample_signals(), meta, sector_allocations={"agriculture": .25, "metals": .75})

    def test_ensemble_scale_invariant_and_no_actual_selection(self):
        first = sample_signals().assign(model="lightgbm")
        second = sample_signals().assign(model="xgboost")
        second["prediction"] = second.prediction * 999. + 50.
        data = pd.concat([first, second], ignore_index=True)
        result = ensemble_predictions(data, model_weights={"lightgbm": .3, "xgboost": .7})
        data["actual"] = np.nan
        assert_frame_equal(result, ensemble_predictions(data, model_weights={"lightgbm": .3, "xgboost": .7}))
        np.testing.assert_allclose(result.prediction, [.25, .5, .75, 1.] * 4)
        data.loc[data.Contract.eq("A-delivery"), "signal_available_at"] += pd.Timedelta("1min")
        delayed = ensemble_predictions(data, model_weights={"lightgbm": .3, "xgboost": .7})
        self.assertTrue((delayed.loc[delayed.sector.eq("agriculture"), "signal_available_at"]
                         == delayed.loc[delayed.sector.eq("agriculture"), "Datetime"] + pd.Timedelta("1min")).all())

    def test_ensemble_rejects_incomplete_coverage(self):
        data = pd.concat([sample_signals().assign(model="one"), sample_signals().assign(model="two")])
        with self.assertRaisesRegex(ValueError, "identical sample coverage"):
            ensemble_predictions(data.iloc[:-1], model_weights={"one": .5, "two": .5})
        with self.assertRaisesRegex(ValueError, "exactly match"):
            ensemble_predictions(data, model_weights={"one": 1.})

    def test_held_missing_returns_fail_without_changing_weights(self):
        weights = portfolios()
        before = weights.copy(deep=True)
        returns = raw_returns(weights)
        held_index = weights.index[weights.weight.ne(0)][0]
        returns.loc[held_index, "return"] = np.nan
        with self.assertRaisesRegex(ValueError, "held instrument return"):
            research_backtest(weights, returns)
        with self.assertRaisesRegex(ValueError, "missing return"):
            research_backtest(weights, returns.drop(index=held_index))
        assert_frame_equal(before, weights)
        returns = raw_returns(weights)
        returns.loc[weights.weight.eq(0), "return"] = np.nan
        self.assertEqual(len(research_backtest(weights, returns).curves), 2)

    def test_signal_timing_future_labels_and_interval_alignment(self):
        weights = portfolios()
        returns = raw_returns(weights)
        late = weights.copy()
        late["signal_available_at"] += pd.Timedelta("1ns")
        with self.assertRaisesRegex(ValueError, "signal availability"):
            research_backtest(late, returns)
        bad = returns.copy()
        bad["label_available_at"] -= pd.Timedelta("1ns")
        with self.assertRaisesRegex(ValueError, "before return_end"):
            research_backtest(weights, bad)
        bad = returns.copy()
        bad.loc[0, "return_end"] -= pd.Timedelta("1min")
        with self.assertRaisesRegex(ValueError, "same return interval"):
            research_backtest(weights, bad)

    def test_rejects_overlapping_forward_labels_and_unsorted_bars(self):
        weights = portfolios()
        returns = raw_returns(weights)
        returns["return_end"] += pd.Timedelta("1min")
        returns["label_available_at"] = returns.return_end
        with self.assertRaisesRegex(ValueError, "overlap"):
            research_backtest(weights, returns)
        with self.assertRaisesRegex(ValueError, "chronological"):
            research_backtest(weights.iloc[::-1], raw_returns(weights))

    def test_interval_reset_costs_include_drift_and_final_exit(self):
        weights = portfolios(sample_signals(periods=1))
        returns = raw_returns(weights)
        returns["return"] = np.where(weights.weight > 0, .1, -.1)
        result = research_backtest(weights, returns, commission_bps=10., slippage_bps=0.)
        row = result.curves.iloc[0]
        self.assertAlmostEqual(row.gross_return, .1)
        self.assertAlmostEqual(row.entry_turnover, 1.)
        self.assertAlmostEqual(row.exit_turnover, 1.)
        self.assertAlmostEqual(row.cost, .002)
        self.assertAlmostEqual(row.nav, 1.098)
        self.assertTrue(result.audit["final_liquidation_charged"])
        self.assertFalse(any("annual" in k for k in result.metrics))
        returns["return"] = .1
        drifted = research_backtest(weights, returns, commission_bps=10., slippage_bps=0.)
        self.assertAlmostEqual(drifted.curves.exit_turnover.iloc[0], 1.1)
        self.assertAlmostEqual(drifted.curves.nav.iloc[0], 1. - .0021)

    def test_all_cash_cost_zero_and_no_unobserved_gap_positions(self):
        weights = portfolios()
        weights["weight"] = 0.
        returns = raw_returns(weights)
        returns["return"] = np.nan
        first_timestamp = returns.Datetime.min()
        returns.loc[returns.Datetime.eq(first_timestamp), "return_end"] -= pd.Timedelta("5min")
        result = research_backtest(weights, returns)
        np.testing.assert_array_equal(result.curves.nav, 1.)
        np.testing.assert_array_equal(result.curves.cost, 0.)
        self.assertEqual(result.audit["position_policy"], "reset_to_cash_at_every_interval_end")

    def test_nonpositive_equity_rejected_and_annualization_only_explicit(self):
        weights = portfolios()
        returns = raw_returns(weights)
        returns["return"] = np.where(weights.weight < 0, 10., 0.)
        with self.assertRaisesRegex(ValueError, "non-positive"):
            research_backtest(weights, returns)
        with self.assertRaisesRegex(ValueError, "entry costs exhaust"):
            research_backtest(weights, raw_returns(weights), commission_bps=10000.)
        result = research_backtest(weights, raw_returns(weights), periods_per_year=250.)
        self.assertIn("annualized_return", result.metrics)


if __name__ == "__main__":
    unittest.main()
