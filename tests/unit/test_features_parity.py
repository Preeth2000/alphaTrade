"""Feature pipeline parity test vs alphaGen inference_reference.py.

Loads the aapl_daily_mlp_example reference artifact, fetches live OHLCV,
runs both alphaGen's canonical pipeline and alphaLink's pipeline on the
same data, and asserts the input tensors and logits match to ≤1e-4.

This is the highest-risk test per HANDOVER §5 — any deviation here means
silent wrong predictions in production.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ALPHALINK_ROOT = Path(__file__).parent.parent.parent
ALPHAGEN_ROOT = ALPHALINK_ROOT.parent / "alphaGen"
ARTIFACT_DIR = ALPHAGEN_ROOT / "artifacts" / "aapl_daily_mlp_example"
REFERENCE_SCRIPT = ALPHAGEN_ROOT / "examples" / "inference_reference.py"


def _skip_if_missing():
    import os
    if os.environ.get("SKIP_PARITY", "0") == "1":
        pytest.skip("SKIP_PARITY=1")
    if not ARTIFACT_DIR.exists():
        pytest.skip(f"alphaGen artifact not found at {ARTIFACT_DIR}")
    if not REFERENCE_SCRIPT.exists():
        pytest.skip(f"inference_reference.py not found at {REFERENCE_SCRIPT}")
    try:
        import talib  # noqa: F401
    except ImportError:
        pytest.skip("TA-Lib not installed")


def _load_reference_module():
    """Import inference_reference.py from alphaGen without installing it."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("inference_reference", REFERENCE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # Add alphaGen to path so any relative imports resolve
    sys.path.insert(0, str(ALPHAGEN_ROOT))
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def shared_ohlcv():
    """Fetch OHLCV once and share across tests in this module."""
    _skip_if_missing()
    from alphalink.adapter.manifest import Manifest
    manifest = Manifest.load(ARTIFACT_DIR / "manifest.json")
    from alphalink.data.yfinance_provider import YFinanceProvider
    df = YFinanceProvider().fetch_ohlcv(manifest.ticker, manifest.interval, manifest.window)
    return df


@pytest.fixture(scope="module")
def manifest():
    _skip_if_missing()
    from alphalink.adapter.manifest import Manifest
    return Manifest.load(ARTIFACT_DIR / "manifest.json")


@pytest.fixture(scope="module")
def ref():
    _skip_if_missing()
    return _load_reference_module()


def test_feature_tensor_matches_reference(shared_ohlcv, manifest, ref):
    """alphaLink feature tensor == alphaGen reference tensor to ≤1e-4."""
    import json

    # alphaLink pipeline
    from alphalink.adapter.features import compute_features
    from alphalink.adapter.normalize import normalize
    from alphalink.adapter.window import build_input

    al_features = compute_features(shared_ohlcv, manifest.feature_names)
    al_features = al_features.dropna()
    al_features = normalize(al_features, manifest)
    al_tensor = build_input(al_features, manifest)

    # alphaGen reference pipeline — uses raw dict manifest
    raw_manifest = json.loads((ARTIFACT_DIR / "manifest.json").read_text())
    ag_features = ref.compute_features(shared_ohlcv, raw_manifest)
    ag_features = ref.normalize(ag_features, raw_manifest)
    ag_tensor = ref.build_input(ag_features, raw_manifest)

    np.testing.assert_allclose(
        al_tensor, ag_tensor, atol=1e-4,
        err_msg="alphaLink input tensor diverges from alphaGen reference. "
                "Check features.py, normalize.py, window.py for deviations."
    )


def test_logits_match_reference(shared_ohlcv, manifest, ref):
    """alphaLink logits == alphaGen reference logits to ≤1e-4."""
    import json

    from alphalink.adapter.features import compute_features
    from alphalink.adapter.normalize import normalize
    from alphalink.adapter.window import build_input
    from alphalink.adapter.inference import OnnxModel

    # alphaLink
    al_features = compute_features(shared_ohlcv, manifest.feature_names)
    al_features = al_features.dropna()
    al_features = normalize(al_features, manifest)
    al_tensor = build_input(al_features, manifest)
    al_model = OnnxModel(manifest, ARTIFACT_DIR / "model.onnx")
    al_logits = al_model.run(al_tensor)

    # alphaGen reference
    raw_manifest = json.loads((ARTIFACT_DIR / "manifest.json").read_text())
    ag_features = ref.compute_features(shared_ohlcv, raw_manifest)
    ag_features = ref.normalize(ag_features, raw_manifest)
    ag_tensor = ref.build_input(ag_features, raw_manifest)

    import onnxruntime as ort
    sess = ort.InferenceSession(str(ARTIFACT_DIR / "model.onnx"), providers=["CPUExecutionProvider"])
    ag_logits = sess.run(None, {"input": ag_tensor})[0][0]

    np.testing.assert_allclose(
        al_logits, ag_logits, atol=1e-4,
        err_msg="alphaLink logits diverge from alphaGen reference logits."
    )


def test_signal_is_valid(shared_ohlcv, manifest):
    """End-to-end: pipeline produces a valid BUY/SELL/HOLD signal."""
    from alphalink.adapter.features import compute_features
    from alphalink.adapter.normalize import normalize
    from alphalink.adapter.window import build_input
    from alphalink.adapter.inference import OnnxModel
    from alphalink.consensus.softmax_avg import CLASS_NAMES

    features = compute_features(shared_ohlcv, manifest.feature_names)
    features = features.dropna()
    features = normalize(features, manifest)
    x = build_input(features, manifest)
    model = OnnxModel(manifest, ARTIFACT_DIR / "model.onnx")
    logits = model.run(x)

    assert logits.shape == (3,)
    assert logits.dtype == np.float32
    signal = CLASS_NAMES[int(np.argmax(logits))]
    assert signal in CLASS_NAMES
