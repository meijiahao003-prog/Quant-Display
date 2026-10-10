import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from futures_research.adapters import audit_daily_coverage, load_daily_inputs
from futures_research.continuous import (
    ContinuousConfig,
    RoleConfig,
    build_continuous,
    canonical_id,
    normalize_bars,
    normalize_catalog,
    strategy_for_product,
)


def fixture():
    # Deliberately explicit sessions. Jan 4/5 are absent; no bdate_range assumption.
    calendar = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07",
                               "2020-01-08", "2020-01-09", "2020-01-10", "2020-01-13"])
    catalog = pd.DataFrame([
        {"contract_id": canonical_id("DCE", "M", month), "exchange": "DCE", "product": "M",
             "delivery_month": month, "list_date": "2019-01-01", "last_trade_date": expiry,
             "known_at": "2019-01-01", "source": "synthetic-fixture"}
        for month, expiry in [("2020-05-01", "2020-05-15"),
                              ("2020-09-01", "2020-09-15"),
                              ("2021-01-01", "2021-01-15")]
    ])
    rows = []
    for i, date in enumerate(calendar[:-1]):
        for k, cid in enumerate(catalog.contract_id):
            strength = [300, 100, 20][k] if i == 0 else [100, 300, 20][k]
            price = 100. + k * 100 + i
            rows.append({"date": date, "contract_id": cid, "open": price, "high": price + 1,
                             "low": price - 1, "close": price, "volume": strength,
                             "open_interest": strength})
    return pd.DataFrame(rows), catalog, calendar


class ContinuousTests(unittest.TestCase):
    def test_t_plus_one_confirmation_and_raw_roll_gap(self):
        bars, catalog, calendar = fixture()
        result = build_continuous(bars, catalog, calendar)
        main = result.mapping.query("role == 'main'").set_index("date")
        first, second = catalog.contract_id.iloc[:2]
        self.assertEqual(main.loc[calendar[1], "contract_id"], first)
        self.assertEqual(main.loc[calendar[3], "contract_id"], first)
        self.assertEqual(main.loc[calendar[4], "contract_id"], second)
        self.assertTrue((result.mapping.decision_date < result.mapping.date).all())
        roll = result.rolls.query("role == 'main'").iloc[0]
        self.assertEqual(roll.bridge_date, calendar[3])
        self.assertAlmostEqual(roll.ratio, 103. / 203.)
        # The raw gap is retained and is not an executable investment return.
        raw = result.raw.query("role == 'main'").set_index("date")
        self.assertEqual(raw.loc[calendar[4], "close"], 204.)
        self.assertEqual(raw.loc[calendar[3], "close"], 103.)
        self.assertFalse(raw.loc[calendar[-1], "bar_present"])

    def test_future_bars_and_future_metadata_leave_history_unchanged(self):
        bars, catalog, calendar = fixture()
        cutoff = calendar[3]
        prefix = build_continuous(bars.loc[bars.date <= cutoff], catalog, calendar)
        future_version = catalog.iloc[[0]].copy()
        future_version["known_at"] = "2020-01-09"
        future_version["last_trade_date"] = "2020-02-01"
        extended_catalog = pd.concat([catalog, future_version], ignore_index=True)
        full = build_continuous(bars, extended_catalog, calendar)
        assert_frame_equal(prefix.mapping, full.mapping.loc[full.mapping.decision_date <= cutoff]
                           .reset_index(drop=True))
        assert_frame_equal(prefix.adjusted.loc[prefix.adjusted.date <= cutoff].reset_index(drop=True),
                           full.adjusted.loc[full.adjusted.date <= cutoff].reset_index(drop=True))

    def test_redline_is_thirty_calendar_days_and_tests_effective_date(self):
        bars, catalog, calendar = fixture()
        old = catalog.contract_id.iloc[0]
        new = canonical_id("DCE", "M", "2020-02-01")
        bars.loc[bars.contract_id.eq(old), "contract_id"] = new
        catalog.loc[0, ["contract_id", "delivery_month", "last_trade_date"]] = [
            new, "2020-02-01", "2020-02-06"]
        result = build_continuous(bars, catalog, calendar)
        at_redline = result.mapping.query("role == 'main'").set_index("date").loc[calendar[3]]
        self.assertEqual(at_redline.decision_date, calendar[2])
        self.assertNotEqual(at_redline.contract_id, new)
        self.assertEqual(at_redline.reason, "PAIR_HARD_RESET")
        audit = result.scores.loc[result.scores.contract_id.eq(new)
                                  & result.scores.effective_date.eq(calendar[3])]
        self.assertTrue(audit.reject_reason.str.contains("delivery_protection").all())

    def test_missing_calendar_day_breaks_confirmation_and_stays_missing(self):
        bars, catalog, calendar = fixture()
        bars = bars.loc[bars.date.ne(calendar[2])]
        result = build_continuous(bars, catalog, calendar)
        no_bars = result.raw.loc[result.raw.date.eq(calendar[2])]
        self.assertFalse(no_bars.bar_present.any())
        self.assertTrue(no_bars.close.isna().all())
        # On a fully absent decision date the pair becomes empty, not forward filled.
        after_gap = result.mapping.loc[result.mapping.date.eq(calendar[3])]
        self.assertTrue(after_gap.contract_id.isna().all())
        self.assertTrue(after_gap.confirmation_streak.eq(0).all())

    def test_broken_bridge_never_uses_future_close_or_forward_fill(self):
        bars, catalog, calendar = fixture()
        bars.loc[bars.contract_id.eq(catalog.contract_id.iloc[1]) & bars.date.eq(calendar[1]),
                 "close"] = np.nan
        cfg = ContinuousConfig(main=RoleConfig(confirm_days=1))
        result = build_continuous(bars, catalog, calendar, cfg)
        main = result.adjusted.loc[result.adjusted.role.eq("main")
                                   & (result.adjusted.date >= calendar[2])]
        self.assertTrue(main.close_adjusted.isna().all())
        self.assertTrue(main.factor.isna().all())
        self.assertEqual(result.rolls.query("role == 'main'").iloc[0].status, "BROKEN_BRIDGE")
        self.assertTrue(main.close.notna().any())

    def test_calendar_contract_and_duplicate_fail_closed(self):
        bars, catalog, calendar = fixture()
        for bad_calendar in (calendar[:-1], calendar[::-1], calendar.insert(0, calendar[0])):
            with self.subTest(calendar=bad_calendar), self.assertRaises(ValueError):
                build_continuous(bars, catalog, bad_calendar)
        with self.assertRaises(ValueError):
            normalize_bars(pd.concat([bars, bars.iloc[[0]]]))
        with self.assertRaises(ValueError):
            canonical_id("CZCE", "MA", "601")
        self.assertNotEqual(canonical_id("CZCE", "MA", "2016-01-01"),
                            canonical_id("CZCE", "MA", "2026-01-01"))

    def test_unavailable_metadata_or_late_bar_cannot_select_contract(self):
        bars, catalog, calendar = fixture()
        catalog.loc[0, "known_at"] = "2020-01-06"
        bars["available_date"] = bars.date
        bars.loc[bars.contract_id.eq(catalog.contract_id.iloc[1]) & bars.date.eq(calendar[0]),
                 "available_date"] = calendar[1]
        result = build_continuous(bars, catalog, calendar)
        self.assertEqual(result.mapping.iloc[0].contract_id, catalog.contract_id.iloc[2])
        self.assertIsNone(result.mapping.iloc[1].contract_id)
        reasons = result.scores.loc[result.scores.decision_date.eq(calendar[0]), "reject_reason"]
        self.assertTrue(reasons.str.contains("unknown_metadata").any())
        self.assertTrue(reasons.str.contains("late_bar").any())

    def test_threshold_is_strict_and_configuration_is_validated(self):
        bars, catalog, calendar = fixture()
        for cid, value in zip(catalog.contract_id, [100, 120, 100]):
            later = bars.contract_id.eq(cid) & bars.date.gt(calendar[0])
            bars.loc[later, ["volume", "open_interest"]] = value
        cfg = ContinuousConfig(main=RoleConfig(oi_weight=1.0, confirm_days=1))
        main = build_continuous(bars, catalog, calendar, cfg).mapping.query("role == 'main'")
        self.assertTrue(main.contract_id.eq(catalog.contract_id.iloc[0]).all())
        for kwargs in ({"confirm_days": True}, {"oi_weight": 1.1}, {"switch_multiple": 1.0}):
            with self.assertRaises(ValueError):
                RoleConfig(**kwargs)
        self.assertEqual(strategy_for_product("AU").main.oi_weight, 0.3)

    def test_secondary_has_its_own_five_day_confirmation(self):
        bars, catalog, calendar = fixture()
        for cid, value in zip(catalog.contract_id, [1000, 10, 100]):
            later = bars.contract_id.eq(cid) & bars.date.gt(calendar[0])
            bars.loc[later, ["volume", "open_interest"]] = value
        secondary = build_continuous(bars, catalog, calendar).mapping.query("role == 'secondary'")
        self.assertTrue(secondary.iloc[:5].contract_id.eq(catalog.contract_id.iloc[1]).all())
        self.assertEqual(secondary.iloc[5].contract_id, catalog.contract_id.iloc[2])
        self.assertEqual(secondary.iloc[5].confirmation_streak, 5)

    def test_csv_adapter_fingerprints_and_expected_coverage(self):
        bars, catalog, calendar = fixture()
        bars = bars.drop(index=0)
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            bars.to_csv(folder / "bars.csv", index=False)
            catalog.to_csv(folder / "catalog.csv", index=False)
            pd.DataFrame({"date": calendar}).to_csv(folder / "calendar.csv", index=False)
            inputs = load_daily_inputs(folder / "bars.csv", folder / "catalog.csv", folder / "calendar.csv")
            self.assertEqual(set(inputs.input_sha256), {"bars", "catalog", "calendar"})
            self.assertTrue(all(len(value) == 64 for value in inputs.input_sha256.values()))
            audit = audit_daily_coverage(inputs.bars, inputs.catalog, inputs.calendar)
            self.assertEqual(audit.status.value_counts()["MISSING_BAR"], 4)
            self.assertEqual(len(audit), len(calendar) * len(catalog))

    def test_early_exclusion_survives_normalization_and_reuse(self):
        bars, catalog, calendar = fixture()
        catalog["first_excluded_date"] = pd.NaT
        catalog.loc[0, "first_excluded_date"] = calendar[3]
        normalized = normalize_catalog(catalog)
        assert_frame_equal(normalized, normalize_catalog(normalized))
        result = build_continuous(bars, normalized, calendar)
        main = result.mapping.query("role == 'main'").set_index("date")
        self.assertNotEqual(main.loc[calendar[3], "contract_id"], catalog.contract_id.iloc[0])


if __name__ == "__main__":
    unittest.main()
