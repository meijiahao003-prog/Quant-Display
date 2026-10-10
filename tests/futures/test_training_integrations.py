"""Small synthetic integrations. Optional libraries are skipped when absent."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from futures_research import factor_model_training as training


def fixture():
    dt = pd.date_range('2030-01-01 09:30', periods=32, freq='D')
    index = pd.MultiIndex.from_arrays([dt.normalize(), ['SYNTH'] * len(dt), dt], names=['Date', 'Contract', 'Datetime'])
    x = pd.DataFrame({'factor_a': np.arange(len(dt), dtype=float)}, index=index)
    y = pd.Series(np.arange(len(dt), dtype=float) / 30, index=index, name='target')
    metadata = {'label_end': pd.Series(dt + pd.Timedelta(minutes=30), index=index),
                'feature_available_at': pd.Series(dt, index=index)}
    return x, y, metadata


@unittest.skipUnless(importlib.util.find_spec('xgboost') and importlib.util.find_spec('sklearn') and importlib.util.find_spec('pyarrow'), 'optional ML dependencies')
class RidgeIntegrationTests(unittest.TestCase):
    def test_oof_meta_final_and_frozen_test(self):
        from futures_research import factor_ridge_stacking as ridge
        x, y, metadata = fixture()
        groups = {g: x.rename(columns={'factor_a': g}).copy() for g in ridge.EXPECTED_GROUPS}
        plans = {g: {'iterations': 1, 'params': training.make_training_config('xgboost')['params']} for g in ridge.EXPECTED_GROUPS}
        fold = ridge.WalkForwardFold('synthetic_fold', pd.Timestamp('2030-01-01'), pd.Timestamp('2030-01-10 23:59:59'),
                                     pd.Timestamp('2030-01-11'), pd.Timestamp('2030-01-20 23:59:59'),
                                     pd.Timestamp('2030-01-21'), pd.Timestamp('2030-01-26 23:59:59'))
        # Give later rows a familiar training range so a one-tree toy model has variance.
        for group in groups.values():
            group.iloc[20:, 0] = np.tile([0., 10., 19.], 4)
        with tempfile.TemporaryDirectory() as root:
            oof = ridge.run_walk_forward_oof(groups, y, 'target', plans, folds=[fold], plans_frozen_at='2030-01-01', output_dir=root, **metadata)
            self.assertTrue(oof.audit['purge_check_executed'])
            meta = ridge.fit_ridge_meta_model(oof, output_dir=root, **metadata)
            self.assertTrue(meta.audit['ridge_oof_prediction_is_in_sample_meta_fit'])
            final = ridge.fit_final_base_models(groups, y, 'target', plans,
                                                fit_start='2030-01-01', fit_end='2030-01-15 23:59:59',
                                                valid_start='2030-01-16', valid_end='2030-01-26 23:59:59',
                                                as_of_time='2030-01-27', plans_frozen_at='2030-01-01', output_dir=root, **metadata)
            tail = {g: frame.iloc[26:] for g, frame in groups.items()}
            result = ridge.evaluate_final_ridge_stack(final, meta, tail, y.iloc[26:], 'target',
                                                      test_start='2030-01-27', test_end='2030-02-02', output_dir=root, **metadata)
            self.assertEqual(len(result.predictions), 6)
            self.assertTrue(Path(result.artifact_paths['audit']).exists())


@unittest.skipUnless(importlib.util.find_spec('torch'), 'optional torch dependency')
class SequenceWindowTests(unittest.TestCase):
    def test_sequence_windows_never_cross_contract_or_endpoint(self):
        import torch
        dt = pd.to_datetime(['2030-01-01 09:00', '2030-01-01 10:00', '2030-01-01 11:00'] * 2)
        index = pd.MultiIndex.from_arrays([dt.normalize(), ['A'] * 3 + ['B'] * 3, dt], names=['Date', 'Contract', 'Datetime'])
        split = training._SplitData('synthetic', np.arange(6, dtype=np.float32).reshape(-1, 1), np.arange(6, dtype=float), index, dt.min(), dt.max())
        config = training.make_training_config('itransformer')
        config['params']['window_size'] = 2
        dataset = training._build_window_dataset(split, config, torch)
        self.assertEqual(len(dataset), 4)
        for i in range(len(dataset)):
            values, label = dataset[i]
            self.assertEqual(float(values[-1, 0]), float(label))
            self.assertTrue(bool((values[:, 0] <= label).all()))
            self.assertNotEqual(values[:, 0].tolist(), [2., 3.])

@unittest.skipUnless(importlib.util.find_spec('xgboost'), 'optional XGBoost dependency')
class HierarchicalIntegrationTests(unittest.TestCase):
    def test_monthly_residual_research_flow_and_loader_order(self):
        from futures_research import factor_hierarchical_residual as h
        dt = pd.DatetimeIndex([pd.Timestamp(f'2030-{month:02d}-{day:02d} 09:30')
                               for month in range(1, 6) for day in (1, 5, 10, 15, 20, 25)])
        index = pd.MultiIndex.from_arrays([dt.normalize(), ['SYNTH'] * len(dt), dt], names=['Date', 'Contract', 'Datetime'])
        values = np.tile(np.arange(6, dtype=float), 5)
        y = pd.Series(values * 0.01 + np.sin(np.arange(len(dt))) * 0.001, index=index, name='target')
        groups = {g: pd.DataFrame({f'{g}_factor': values}, index=index) for g in h.EXPECTED_GROUPS}
        metadata = {'label_end': pd.Series(dt + pd.Timedelta(minutes=30), index=index),
                    'feature_available_at': pd.Series(dt, index=index)}
        params = training.make_training_config('xgboost')['params']
        core_plan = {'iterations': 1, 'params': dict(params)}
        residual_plans = {g: {'iterations': 1, 'params': dict(params)} for g in h.RESIDUAL_GROUPS}
        events = []
        def feature_loader(start, end):
            events.append('features')
            mask = (dt >= start) & (dt <= end)
            return {g: frame.loc[mask] for g, frame in groups.items()}
        def target_loader(start, end):
            events.append('target')
            return y.loc[(dt >= start) & (dt <= end)]
        with tempfile.TemporaryDirectory() as root:
            core = h.generate_monthly_core_oof(groups, y, 'target', core_plan, core_train_start='2030-01-01',
                                               oof_start='2030-02-01', oof_end='2030-04-30', plans_frozen_at='2030-01-01',
                                               output_dir=root, show_progress=False, **metadata)
            selection = h.run_residual_parameter_selection(groups, core, {'G02': {'max_iterations': 1, 'params': dict(params)}},
                                                           target='target', groups=['G02'], fit_start='2030-02-01', fit_end='2030-02-28',
                                                           valid_start='2030-03-01', valid_end='2030-03-31', test_start='2030-05-01',
                                                           output_dir=root, **metadata)
            self.assertEqual(len(selection.histories['G02']), 1)
            promoted = h.run_monthly_residual_promotion(groups, core, residual_plans, target='target',
                                                       residual_train_start='2030-02-01', promotion_start='2030-04-01',
                                                       promotion_end='2030-04-30', plans_frozen_at='2030-03-31', test_start='2030-05-01',
                                                       frozen_config_confirmed=True, output_dir=root, **metadata)
            history_mask = dt < pd.Timestamp('2030-05-01')
            result = h.run_research_walk_forward_test({g: frame.loc[history_mask] for g, frame in groups.items()},
                                                      y.loc[history_mask], core, promoted, core_plan, residual_plans,
                                                      feature_loader, target_loader, target='target', core_train_start='2030-01-01',
                                                      residual_train_start='2030-02-01', test_start='2030-05-01', test_end='2030-05-31',
                                                      plans_frozen_at='2030-04-30', output_dir=root, **metadata)
            self.assertEqual(events, ['features', 'target'])
            self.assertEqual(len(result.predictions), 6)


if __name__ == '__main__':
    unittest.main()
