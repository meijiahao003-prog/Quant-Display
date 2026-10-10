"""Regressions for temporal selection and frozen fusion-artifact bindings."""
from dataclasses import replace
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from futures_research import factor_hierarchical_residual as h
from futures_research import factor_model_training as training
from futures_research import factor_ridge_stacking as ridge


def fixture():
    dt = pd.DatetimeIndex([
        pd.Timestamp(f"2030-{month:02d}-{day:02d} 09:30")
        for month in range(1, 6) for day in (1, 5, 10, 15, 20, 25)
    ])
    index = pd.MultiIndex.from_arrays(
        [dt.normalize(), ["SYNTH"] * len(dt), dt], names=["Date", "Contract", "Datetime"]
    )
    values = np.tile(np.arange(6, dtype=float), 5)
    grouped = {g: pd.DataFrame({f"{g}_factor": values}, index=index) for g in h.EXPECTED_GROUPS}
    y = pd.Series(values * 0.01 + np.sin(np.arange(len(dt))) * 0.001, index=index, name="target")
    metadata = {
        "label_end": pd.Series(dt + pd.Timedelta(minutes=30), index=index),
        "feature_available_at": pd.Series(dt, index=index),
    }
    plan = {"iterations": 1, "params": training.make_training_config("xgboost")["params"]}
    return grouped, y, metadata, plan


def fake_fit(**kwargs):
    return h.ModelFit(object(), {}, pd.DataFrame())


def fake_predict(model, frame, names):
    return frame.iloc[:, 0].to_numpy() * 0.005


def core_result(root, grouped, y, metadata, plan):
    return h.generate_monthly_core_oof(
        grouped, y, "target", plan, core_train_start="2030-01-01",
        oof_start="2030-02-01", oof_end="2030-04-30", plans_frozen_at="2030-01-01",
        output_dir=root, show_progress=False, final_fit_fn=fake_fit,
        predict_fn=fake_predict, **metadata,
    )


def promote(root, grouped, core, metadata, plan):
    return h.run_monthly_residual_promotion(
        grouped, core, {g: plan for g in h.RESIDUAL_GROUPS}, target="target",
        residual_train_start="2030-02-01", promotion_start="2030-04-01",
        promotion_end="2030-04-30", plans_frozen_at="2030-03-31", test_start="2030-05-01",
        frozen_config_confirmed=True, output_dir=root, final_fit_fn=fake_fit,
        predict_fn=fake_predict, **metadata,
    )


def research_test(root, grouped, y, metadata, plan, core, promotion, feature_loader):
    dt = y.index.get_level_values("Datetime")
    before_test = dt < pd.Timestamp("2030-05-01")
    return h.run_research_walk_forward_test(
        {g: x.loc[before_test] for g, x in grouped.items()}, y.loc[before_test],
        core, promotion, plan, {g: plan for g in h.RESIDUAL_GROUPS}, feature_loader,
        lambda start, end: y.loc[(dt >= start) & (dt <= end)],
        target="target", core_train_start="2030-01-01", residual_train_start="2030-02-01",
        test_start="2030-05-01", test_end="2030-05-31", plans_frozen_at="2030-04-30",
        output_dir=root, final_fit_fn=fake_fit, predict_fn=fake_predict, **metadata,
    )


def test_promotion_excludes_labels_realised_inside_test(tmp_path):
    grouped, y, metadata, plan = fixture()
    crossing_key = y.index[23]  # April 25; its label deliberately is not yet known on May 1.
    metadata["label_end"].loc[crossing_key] = pd.Timestamp("2030-05-01")
    core = core_result(tmp_path, grouped, y, metadata, plan)
    result = promote(tmp_path, grouped, core, metadata, plan)
    assert crossing_key not in result.predictions.index
    assert result.audit["purged_selection_labels_unavailable_before_test"] == 1
    assert pd.Timestamp(result.audit["max_selection_label_end"]) < pd.Timestamp("2030-05-01")


def test_modified_core_prediction_and_matching_residual_cannot_reuse_audit(tmp_path):
    grouped, y, metadata, plan = fixture()
    core = core_result(tmp_path, grouped, y, metadata, plan)
    core.predictions["pred_G01"] = core.predictions["actual"]  # Perfect leaked target prediction.
    core.predictions["core_residual"] = 0.0
    with pytest.raises(ValueError, match="audit binding mismatch"):
        promote(tmp_path, grouped, core, metadata, plan)


def test_changed_core_label_timing_cannot_reuse_audit(tmp_path):
    grouped, y, metadata, plan = fixture()
    core = core_result(tmp_path, grouped, y, metadata, plan)
    metadata["label_end"].iloc[6] += pd.Timedelta(minutes=1)
    with pytest.raises(ValueError, match="temporal metadata changed"):
        promote(tmp_path, grouped, core, metadata, plan)


def test_callback_implementation_change_invalidates_cache(tmp_path):
    grouped, y, metadata, plan = fixture()
    original = core_result(tmp_path, grouped, y, metadata, plan)
    def different_predict(model, frame, names):
        return frame.iloc[:, 0].to_numpy() * 0.01
    result = h.generate_monthly_core_oof(
        grouped, y, "target", plan, core_train_start="2030-01-01", oof_start="2030-02-01",
        oof_end="2030-04-30", plans_frozen_at="2030-01-01", output_dir=tmp_path,
        show_progress=False, final_fit_fn=fake_fit, predict_fn=different_predict, **metadata,
    )
    assert not any(block["cache_hit"] for block in result.audit["monthly_blocks"])
    np.testing.assert_allclose(result.predictions.pred_G01, original.predictions.pred_G01 * 2)


def test_test_rejects_preprocessor_fitted_in_current_month(tmp_path):
    grouped, y, metadata, plan = fixture()
    core = core_result(tmp_path, grouped, y, metadata, plan)
    promotion = promote(tmp_path, grouped, core, metadata, plan)
    def loader(start, end):
        dt = y.index.get_level_values("Datetime")
        loaded = {g: x.loc[(dt >= start) & (dt <= end)].copy() for g, x in grouped.items()}
        loaded["G01"].attrs.update(processing_method="zscore", processing_fit_end="2030-05-20")
        return loaded
    with pytest.raises(ValueError, match="within each fold"):
        research_test(tmp_path, grouped, y, metadata, plan, core, promotion, loader)


def test_test_rejects_changed_promotion_selection_before_loading_test(tmp_path):
    grouped, y, metadata, plan = fixture()
    core = core_result(tmp_path, grouped, y, metadata, plan)
    promotion = promote(tmp_path, grouped, core, metadata, plan)
    changed = ("G02",) if promotion.selected_groups != ("G02",) else ()
    promotion = replace(promotion, selected_groups=changed)
    def must_not_load(start, end):
        raise AssertionError("Test should not be loaded for an unbound selection")
    with pytest.raises(ValueError, match="selected groups differ"):
        research_test(tmp_path, grouped, y, metadata, plan, core, promotion, must_not_load)


def ridge_oof(root, grouped, y, metadata, plan):
    def base_fit(**kwargs):
        model_path = Path(root) / f"fake_{kwargs['group']}.json"
        model_path.write_text("synthetic mock model", encoding="utf-8")
        return SimpleNamespace(model=object(), artifact_paths={"model": str(model_path)}, audit={})
    fold = ridge.WalkForwardFold(
        "fold", pd.Timestamp("2030-01-01"), pd.Timestamp("2030-01-15 23:59:59"),
        pd.Timestamp("2030-01-16"), pd.Timestamp("2030-01-31 23:59:59"),
        pd.Timestamp("2030-02-01"), pd.Timestamp("2030-02-28 23:59:59"),
    )
    with patch.object(ridge, "_train_one_final_model", side_effect=base_fit), patch.object(
        ridge, "_predict_xgboost", side_effect=fake_predict
    ):
        return ridge.run_walk_forward_oof(
            grouped, y, "target", {g: deepcopy(plan) for g in ridge.EXPECTED_GROUPS}, folds=[fold],
            plans_frozen_at="2030-01-01", output_dir=root, **metadata,
        )


@pytest.mark.parametrize("changed", ["features", "target", "fold_ids", "metadata"])
def test_ridge_meta_rejects_modified_oof_payloads(tmp_path, changed):
    grouped, y, metadata, plan = fixture()
    oof = ridge_oof(tmp_path, grouped, y, metadata, plan)
    if changed == "features":
        oof.features.iloc[0, 0] += 1.0
    elif changed == "target":
        oof.target.iloc[0] += 1.0
    elif changed == "fold_ids":
        oof.fold_ids.iloc[0] = "invented_fold"
    else:
        metadata["label_end"].loc[oof.features.index[0]] += pd.Timedelta(minutes=1)
    with pytest.raises(ValueError, match="OOF audit binding mismatch"):
        ridge.fit_ridge_meta_model(oof, output_dir=tmp_path, **metadata)


def test_ridge_test_rejects_future_fitted_preprocessing(tmp_path):
    grouped, y, metadata, plan = fixture()
    oof = ridge_oof(tmp_path, grouped, y, metadata, plan)
    meta = ridge.fit_ridge_meta_model(oof, output_dir=tmp_path, **metadata)
    final = ridge.FinalBaseModels(
        models={}, group_order=ridge.EXPECTED_GROUPS, group_feature_names=oof.group_feature_names,
        target_name="target", artifact_paths={}, audit={
            "valid_end_used_as_training": "2030-02-28", "max_training_label_end": "2030-02-26",
            "as_of_time": "2030-03-01", "group_plan_sha256": meta.audit["source_group_plan_sha256"],
        },
    )
    dt = y.index.get_level_values("Datetime")
    mask = (dt >= pd.Timestamp("2030-03-01")) & (dt < pd.Timestamp("2030-04-01"))
    tail = {g: x.loc[mask].copy() for g, x in grouped.items()}
    tail["G01"].attrs.update(compression_method="pca", compression_fit_end="2030-03-15")
    with pytest.raises(ValueError, match="within each fold"):
        ridge.evaluate_final_ridge_stack(
            final, meta, tail, y.loc[mask], "target", test_start="2030-03-01", test_end="2030-03-31",
            output_dir=tmp_path, **metadata,
        )
