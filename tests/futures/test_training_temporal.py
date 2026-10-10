"""Synthetic regressions for temporal boundary enforcement (no market claims)."""
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import tempfile

import numpy as np
import pandas as pd

from futures_research import factor_model_training as training
from futures_research import factor_hierarchical_residual as hierarchical
from futures_research.factor_runtime import temporal_metadata_for


def fixture(times=None):
    dt = pd.DatetimeIndex(times if times is not None else pd.date_range('2030-01-01', periods=10, freq='D'))
    index = pd.MultiIndex.from_arrays([dt.normalize(), ['SYNTH'] * len(dt), dt], names=['Date', 'Contract', 'Datetime'])
    x = pd.DataFrame({'feature_a': np.arange(len(dt), dtype=float)}, index=index)
    y = pd.Series(np.arange(len(dt), dtype=float) / 100, index=index, name='target')
    metadata = {'label_end': pd.Series(dt + pd.Timedelta(hours=12), index=index),
                'feature_available_at': pd.Series(dt, index=index)}
    return x, y, metadata


class CapturingAdapter:
    captured = {}

    def __init__(self, config, feature_names):
        pass

    def fit_selection(self, fit, valid, plotter):
        self.captured.update(fit=fit.index, valid=valid.index)
        history = pd.DataFrame({'iteration': [1], 'train_rmse': [1.], 'valid_rmse': [1.], 'valid_pearson_ic': [0.1]})
        return training._FitOutcome(object(), history, 1, 1., 1, 0.1, {})

    def fit_frozen(self, parts, rounds, plotter):
        self.captured['final'] = parts[0].index
        return training._FrozenOutcome(object(), pd.DataFrame({'iteration': [rounds], 'full_train_rmse': [1.]}), {})


class TrainingTemporalTests(unittest.TestCase):
    def call(self, x, y, metadata, stage='selection'):
        config = training.make_training_config('xgboost')
        config['live_plot']['enabled'] = False
        with patch.dict(training._TRAINING_METHODS, {'xgboost': CapturingAdapter}), patch.object(training, '_save_final_artifacts', return_value=({}, {})):
            return training.train_factor_model(x, y, 'target', 'xgboost', stage, 1,
                                              '2030-01-01', '2030-01-06', '2030-01-06', '2030-01-11',
                                              config=config, **metadata)

    def test_missing_temporal_metadata_fails_closed(self):
        x, y, _ = fixture()
        with self.assertRaisesRegex(ValueError, 'required'):
            self.call(x, y, {})

    def test_selection_purges_label_overlapping_validation(self):
        x, y, metadata = fixture()
        metadata['label_end'].iloc[4] = pd.Timestamp('2030-01-06')
        result = self.call(x, y, metadata)
        self.assertNotIn(x.index[4], CapturingAdapter.captured['fit'])
        self.assertEqual(result.audit['temporal_audit']['purged_fit_rows'], 1)
        self.assertEqual(len(CapturingAdapter.captured['valid']), 5)

    def test_final_purges_labels_unavailable_at_final_cutoff(self):
        x, y, metadata = fixture()
        metadata['label_end'].iloc[-1] = pd.Timestamp('2030-01-11')
        result = self.call(x, y, metadata, stage='final')
        self.assertNotIn(x.index[-1], CapturingAdapter.captured['final'])
        self.assertEqual(len(CapturingAdapter.captured['final']), 9)
        self.assertEqual(result.audit['temporal_audit']['purged_valid_rows'], 1)

    def test_future_feature_is_rejected_before_fitting(self):
        x, y, metadata = fixture()
        metadata['feature_available_at'].iloc[0] += pd.Timedelta(seconds=1)
        with self.assertRaises(ValueError):
            self.call(x, y, metadata)

    def test_metadata_context_does_not_escape_operation(self):
        x, y, metadata = fixture()
        self.call(x, y, metadata)
        with self.assertRaisesRegex(ValueError, 'missing'):
            temporal_metadata_for(x.index)

    def test_incomplete_metadata_cannot_silently_align(self):
        x, y, metadata = fixture()
        metadata['label_end'] = metadata['label_end'].iloc[:-1]
        with self.assertRaisesRegex(ValueError, 'cover'):
            self.call(x, y, metadata)

    def test_preprocessing_fitted_on_validation_is_rejected(self):
        x, y, metadata = fixture()
        x.attrs.update(processing_method='zscore', processing_fit_end='2030-01-07')
        with self.assertRaisesRegex(ValueError, 'within each fold'):
            self.call(x, y, metadata)

    def test_preprocessing_without_fit_provenance_is_rejected(self):
        x, y, metadata = fixture()
        x.attrs['compression_method'] = 'pca'
        with self.assertRaisesRegex(ValueError, 'provenance'):
            self.call(x, y, metadata)

    def test_target_column_is_rejected(self):
        x, y, _ = fixture()
        x['target'] = y
        with self.assertRaisesRegex(ValueError, 'label leakage'):
            training.validate_training_inputs(x, y, 'target')

    def test_test_boundary_includes_training_label_end(self):
        # Import QC lazily because this guard itself needs no optional ML library.
        from futures_research.factor_model_testing import _verify_test_is_after_training
        result = SimpleNamespace(audit={'split_audit': {'fit': {'actual_end': '2030-01-01'}},
                                       'temporal_audit': {'purge_check_executed': True, 'max_training_label_end': '2030-01-05'}})
        with self.assertRaisesRegex(ValueError, 'overlaps'):
            _verify_test_is_after_training(result, pd.DatetimeIndex(['2030-01-03']))


class HierarchicalTemporalTests(unittest.TestCase):
    def test_final_shards_retain_every_boundary_row(self):
        x, y, metadata = fixture()
        captured = {}
        def trainer(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(model=object(), audit={}, frozen_history=pd.DataFrame())
        plan = {'iterations': 1, 'params': training.make_training_config('xgboost')['params']}
        with patch.object(hierarchical, '_training_api', return_value=(training.make_training_config, trainer)):
            hierarchical._default_final_fit(x=x, y=y, target='target', plan=plan, role='synthetic')
        dt = x.index.get_level_values('Datetime')
        left = (dt >= captured['fit_start']) & (dt < captured['fit_end'])
        right = (dt >= captured['valid_start']) & (dt < captured['valid_end'])
        self.assertTrue((left | right).all())
        self.assertFalse((left & right).any())

    def test_monthly_oof_purges_labels_and_invalidates_feature_cache(self):
        x, y, metadata = fixture(['2030-01-01', '2030-01-15', '2030-01-31', '2030-02-01', '2030-02-02'])
        metadata['label_end'].iloc[2] = pd.Timestamp('2030-02-01')
        groups = {g: x.rename(columns={'feature_a': f'{g}_feature'}).copy() for g in hierarchical.EXPECTED_GROUPS}
        plan = {'iterations': 1, 'params': training.make_training_config('xgboost')['params']}
        calls = []
        def fake_fit(**kwargs):
            calls.append(kwargs['x'].index)
            return hierarchical.ModelFit(object(), {}, pd.DataFrame())
        def fake_predict(model, frame, names):
            return frame.iloc[:, 0].to_numpy() * 0.1
        with tempfile.TemporaryDirectory() as root:
            args = dict(core_train_start='2030-01-01', oof_start='2030-02-01', oof_end='2030-02-02 23:59:59',
                        plans_frozen_at='2030-01-01', output_dir=root, show_progress=False,
                        final_fit_fn=fake_fit, predict_fn=fake_predict, **metadata)
            hierarchical.generate_monthly_core_oof(groups, y, 'target', plan, **args)
            self.assertNotIn(x.index[2], calls[-1])
            hierarchical.generate_monthly_core_oof(groups, y, 'target', plan, **args)
            self.assertEqual(len(calls), 1)
            groups['G01'].iloc[-1, 0] += 100
            result = hierarchical.generate_monthly_core_oof(groups, y, 'target', plan, **args)
            self.assertEqual(len(calls), 2)
            self.assertGreater(result.predictions.iloc[-1]['pred_G01'], 10)

    def test_plan_selection_must_precede_oof(self):
        from futures_research.factor_runtime import require_plan_frozen_before
        with self.assertRaisesRegex(ValueError, 'future-informed'):
            require_plan_frozen_before('2030-02-02', '2030-02-01')

    def test_unbound_core_predictions_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'audited'):
            hierarchical._require_core_frame(pd.DataFrame())


if __name__ == '__main__':
    unittest.main()
