# tests/unit/test_model_sync.py
from alphaTrade.config import Settings


def test_settings_has_minio_defaults():
    s = Settings()
    assert s.minio.endpoint == "localhost:9000"
    assert s.minio.access_key == "minioadmin"
    assert s.minio.secret_key == "minioadmin"
    assert s.minio.bucket == "models"
    assert s.minio.secure is False


def test_settings_has_sync_defaults():
    s = Settings()
    assert s.model_sync.user == "default"
    assert s.model_sync.account == "default"
    assert s.model_sync.poll_interval == 60
    assert s.model_sync.max_versions == 5
    assert s.model_sync.enabled is True


def test_settings_has_validation_defaults():
    s = Settings()
    assert s.model_sync.validation.min_sharpe == 0.5
    assert s.model_sync.validation.max_drawdown == 0.20
    assert s.model_sync.validation.min_hit_rate == 0.45


def test_minio_endpoint_overridable_via_env(monkeypatch):
    monkeypatch.setenv("MINIO__ENDPOINT", "remotehost:9000")
    s = Settings()
    assert s.minio.endpoint == "remotehost:9000"


def test_model_sync_poll_interval_overridable_via_env(monkeypatch):
    monkeypatch.setenv("MODEL_SYNC__POLL_INTERVAL", "120")
    s = Settings()
    assert s.model_sync.poll_interval == 120


import json
from pathlib import Path
import pytest
from alphaTrade.config import ValidationThresholds
from alphaTrade.store.model_sync import ValidationGate


def _write_backtest(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "backtest.json"
    p.write_text(json.dumps(data))
    return p


def test_validation_gate_passes():
    thresholds = ValidationThresholds(min_sharpe=0.5, max_drawdown=0.20, min_hit_rate=0.45)
    gate = ValidationGate(thresholds)
    result = gate.check({"sharpe": 1.2, "max_drawdown": -0.08, "hit_rate": 0.55, "n_trades": 20})
    assert result.passed is True
    assert result.reason is None


def test_validation_gate_fails_sharpe():
    thresholds = ValidationThresholds(min_sharpe=0.5, max_drawdown=0.20, min_hit_rate=0.45)
    gate = ValidationGate(thresholds)
    result = gate.check({"sharpe": 0.3, "max_drawdown": -0.08, "hit_rate": 0.55, "n_trades": 20})
    assert result.passed is False
    assert "sharpe" in result.reason


def test_validation_gate_fails_drawdown():
    thresholds = ValidationThresholds(min_sharpe=0.5, max_drawdown=0.20, min_hit_rate=0.45)
    gate = ValidationGate(thresholds)
    result = gate.check({"sharpe": 1.2, "max_drawdown": -0.35, "hit_rate": 0.55, "n_trades": 20})
    assert result.passed is False
    assert "drawdown" in result.reason


def test_validation_gate_fails_hit_rate():
    thresholds = ValidationThresholds(min_sharpe=0.5, max_drawdown=0.20, min_hit_rate=0.45)
    gate = ValidationGate(thresholds)
    result = gate.check({"sharpe": 1.2, "max_drawdown": -0.08, "hit_rate": 0.30, "n_trades": 20})
    assert result.passed is False
    assert "hit_rate" in result.reason


def test_validation_gate_fails_missing_key():
    thresholds = ValidationThresholds()
    gate = ValidationGate(thresholds)
    result = gate.check({"sharpe": 1.2})  # missing max_drawdown, hit_rate
    assert result.passed is False
    assert "missing" in result.reason.lower()
