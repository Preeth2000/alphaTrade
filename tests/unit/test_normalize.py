"""Tests for normalization pipeline."""
import numpy as np
import pandas as pd

from alphaTrade.adapter.normalize import normalize
from alphaTrade.adapter.manifest import Manifest

BASE = {
    "manifest_version": "1.0.0", "run_name": "t", "model_arch": "mlp", "opset": 17,
    "git_sha": "x", "model_hash": "unknown", "output_format": "logits",
    "input_shape": [2], "classes": ["BUY","SELL","HOLD"], "class_indices": {"BUY":0,"SELL":1,"HOLD":2},
    "feature_names": ["A","B"], "n_features": 2, "window": 1,
    "ticker": "X", "interval": "1d",
}


def _manifest(normalize_mode: str, stats: dict) -> Manifest:
    return Manifest.model_validate({**BASE, "normalize": normalize_mode, "norm_stats": stats})


def test_zscore():
    m = _manifest("zscore", {"mean": {"A": 10.0, "B": 0.0}, "std": {"A": 2.0, "B": 1.0}})
    df = pd.DataFrame({"A": [10.0, 12.0], "B": [0.0, 1.0]})
    out = normalize(df, m)
    np.testing.assert_allclose(out["A"].values, [0.0, 1.0], atol=1e-5)
    np.testing.assert_allclose(out["B"].values, [0.0, 1.0], atol=1e-5)


def test_minmax():
    m = _manifest("minmax", {"min": {"A": 0.0, "B": 0.0}, "max": {"A": 10.0, "B": 4.0}})
    df = pd.DataFrame({"A": [0.0, 10.0], "B": [0.0, 2.0]})
    out = normalize(df, m)
    np.testing.assert_allclose(out["A"].values, [0.0, 1.0], atol=1e-5)
    np.testing.assert_allclose(out["B"].values, [0.0, 0.5], atol=1e-5)


def test_none():
    m = _manifest("none", {})
    df = pd.DataFrame({"A": [1.0, 2.0], "B": [3.0, 4.0]})
    out = normalize(df, m)
    pd.testing.assert_frame_equal(out, df)


def test_zero_std_guard():
    m = _manifest("zscore", {"mean": {"A": 5.0, "B": 0.0}, "std": {"A": 0.0, "B": 1.0}})
    df = pd.DataFrame({"A": [5.0], "B": [1.0]})
    out = normalize(df, m)
    # zero std → divide by 1.0, result = (5-5)/1 = 0
    assert out["A"].iloc[0] == 0.0
