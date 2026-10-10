import importlib.util
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd
import pytest

from futures_research.pipeline import ResearchDataset, run_research, validate_config, _prepare, _purge
from futures_research.synthetic import make_demo


def test_input_rejects_target_as_feature_and_future_availability():
    ds, _, _ = make_demo()
    raw = ds.x.join(ds.y).join(ds.label_end).join(ds.feature_available_at).reset_index()
    with pytest.raises(ValueError, match="exclude"):
        ResearchDataset.from_frame(raw, features=[ds.y.name], target=ds.y.name)
    raw["feature_available_at"] = raw["Datetime"] + pd.Timedelta("1s")
    with pytest.raises(ValueError):
        ResearchDataset.from_frame(raw, features=list(ds.x.columns), target=ds.y.name)


def test_fit_preprocessing_ignores_validation_changes():
    ds, _, cfg = make_demo()
    baseline, z1, p1, _ = _prepare(ds, cfg)
    changed = ds.x.copy()
    later = changed.index.get_level_values("Datetime") >= pd.Timestamp(cfg["intervals"]["valid"][0])
    changed.loc[later] += 1e6
    mutated, z2, p2, _ = _prepare(ResearchDataset(changed, ds.y, ds.feature_available_at, ds.label_end), cfg)
    assert z1.fingerprint == z2.fingerprint
    for name in p1:
        np.testing.assert_array_equal(p1[name].components, p2[name].components)
    np.testing.assert_array_equal(baseline.loc[~later], mutated.loc[~later])
    assert baseline.attrs["processing_fit_end"] < cfg["intervals"]["valid"][0]


def test_half_open_intervals_cannot_overlap():
    _, _, cfg = make_demo()
    cfg["intervals"]["valid"][0] = cfg["intervals"]["fit"][0]
    with pytest.raises(ValueError, match="overlap"):
        validate_config(cfg)


def test_embargo_reaches_unopened_test_boundary():
    ds, _, cfg = make_demo()
    cfg["embargo"] = "3d"
    purged, _ = _purge(ds, cfg, ("fit", "valid"))
    assert (purged.label_end + pd.Timedelta("3d") < pd.Timestamp(cfg["intervals"]["test"][0])).all()


def test_deferred_test_loading_and_signal_export():
    if importlib.util.find_spec("xgboost") is None:
        pytest.skip("optional XGBoost backend not installed")
    try:
        import xgboost  # noqa: F401
    except (ImportError, OSError) as exc:
        pytest.skip(f"optional XGBoost native dependency unavailable: {exc}")
    ds, loader, cfg = make_demo()
    cfg["iterations"]["xgboost"] = 3
    called = []
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "run"
        def guarded_loader():
            assert (output / "selection.json").is_file()
            assert list((output / "models").rglob("*.json"))
            called.append(True)
            return loader()
        manifest = run_research(ds, guarded_loader, cfg, output, synthetic=True)
        assert called == [True]
        assert manifest["status"] == "complete"
        signals = pd.read_csv(output / "signals.csv")
        assert "actual" not in signals and "future_return" not in signals
        assert signals.synthetic.all()
        assert len(signals) == 240
        with pytest.raises(FileExistsError):
            run_research(ds, loader, cfg, output, synthetic=True)
