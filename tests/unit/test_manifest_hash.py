"""Tests for Manifest loading, validation, and hash check."""
import hashlib
import tempfile
from pathlib import Path

import pytest

from alphaTrade.adapter.manifest import Manifest


MINIMAL_MANIFEST = {
    "manifest_version": "1.0.0",
    "run_name": "test_run",
    "model_arch": "mlp",
    "opset": 17,
    "git_sha": "abc123",
    "model_hash": "unknown",
    "output_format": "logits",
    "input_shape": [416],
    "classes": ["BUY", "SELL", "HOLD"],
    "class_indices": {"BUY": 0, "SELL": 1, "HOLD": 2},
    "feature_names": ["RSI", "Close"],
    "n_features": 2,
    "window": 32,
    "normalize": "zscore",
    "norm_stats": {"mean": {"RSI": 55.0, "Close": 150.0}, "std": {"RSI": 10.0, "Close": 20.0}},
    "ticker": "AAPL",
    "interval": "1d",
}


def test_load_valid():
    m = Manifest.model_validate(MINIMAL_MANIFEST)
    assert m.ticker == "AAPL"
    assert m.n_features == 2


def test_feature_names_length_mismatch():
    bad = {**MINIMAL_MANIFEST, "n_features": 99}
    with pytest.raises(Exception):
        Manifest.model_validate(bad)


def test_unsupported_major_version():
    bad = {**MINIMAL_MANIFEST, "manifest_version": "2.0.0"}
    with pytest.raises(Exception):
        Manifest.model_validate(bad)


def test_hash_check_passes():
    with tempfile.TemporaryDirectory() as d:
        model_bytes = b"fake onnx bytes"
        model_path = Path(d) / "model.onnx"
        model_path.write_bytes(model_bytes)

        h = hashlib.sha256(model_bytes).hexdigest()
        m = Manifest.model_validate({**MINIMAL_MANIFEST, "model_hash": h})
        m.verify_model_hash(model_path)  # should not raise


def test_hash_check_fails():
    with tempfile.TemporaryDirectory() as d:
        model_path = Path(d) / "model.onnx"
        model_path.write_bytes(b"fake onnx bytes")
        m = Manifest.model_validate({**MINIMAL_MANIFEST, "model_hash": "0" * 64})
        with pytest.raises(ValueError, match="hash mismatch"):
            m.verify_model_hash(model_path)
