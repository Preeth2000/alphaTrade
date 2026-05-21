# tests/unit/test_model_sync.py
import json
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from alphaTrade.config import Settings, ValidationThresholds
from alphaTrade.store.model_sync import ModelSyncDaemon, ValidationGate


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


def _make_daemon(tmp_path: Path, cfg=None) -> ModelSyncDaemon:
    from alphaTrade.config import MinioConfig, ModelSyncConfig as MSC
    cfg = cfg or MSC()
    return ModelSyncDaemon(
        minio_cfg=MinioConfig(),
        sync_cfg=cfg,
        models_dir=tmp_path / "models",
    )


def test_sync_record_roundtrip(tmp_path):
    daemon = _make_daemon(tmp_path)
    daemon._write_sync_record("AAPL_Transformer", "v3")
    assert daemon._read_sync_record("AAPL_Transformer") == "v3"


def test_sync_record_missing_returns_none(tmp_path):
    daemon = _make_daemon(tmp_path)
    assert daemon._read_sync_record("AAPL_Transformer") is None


def test_promote_copies_and_writes_record(tmp_path):
    daemon = _make_daemon(tmp_path)
    src = tmp_path / "tmp" / "AAPL_Transformer"
    src.mkdir(parents=True)
    (src / "manifest.json").write_text('{"run_name": "AAPL_Transformer"}')
    (src / "model.onnx").write_bytes(b"\x00\x01")

    daemon._promote(src, "AAPL_Transformer", "v2")

    dest = daemon._models_dir / "AAPL_Transformer"
    assert (dest / "manifest.json").exists()
    assert (dest / "model.onnx").exists()
    assert daemon._read_sync_record("AAPL_Transformer") == "v2"


def test_prune_keeps_max_versions(tmp_path):
    from alphaTrade.config import ModelSyncConfig as MSC
    daemon = _make_daemon(tmp_path, cfg=MSC(max_versions=2))
    versions = ["v1", "v2", "v3"]
    to_delete = daemon._versions_to_prune(versions, promoted="v3")
    assert to_delete == ["v1"]


def test_prune_unbounded_keeps_all(tmp_path):
    from alphaTrade.config import ModelSyncConfig as MSC
    daemon = _make_daemon(tmp_path, cfg=MSC(max_versions=-1))
    versions = ["v1", "v2", "v3", "v4", "v5"]
    assert daemon._versions_to_prune(versions, promoted="v5") == []


@pytest.mark.asyncio
async def test_sync_once_skips_when_version_unchanged(tmp_path):
    daemon = _make_daemon(tmp_path)
    daemon._write_sync_record("AAPL_Transformer", "v2")

    mock_s3 = MagicMock()
    mock_s3.list_objects_v2 = MagicMock(return_value={
        "CommonPrefixes": [{"Prefix": "default/default/AAPL_Transformer/"}]
    })
    mock_s3.get_object = MagicMock(return_value={
        "Body": MagicMock(read=lambda: json.dumps({"version": "v2"}).encode())
    })

    with patch("alphaTrade.store.model_sync.ModelSyncDaemon._make_s3_client", return_value=mock_s3):
        downloaded = await daemon._sync_once()

    assert downloaded == []
    mock_s3.get_object.assert_called_once()  # only fetched latest, no download


@pytest.mark.asyncio
async def test_sync_once_downloads_and_promotes_on_new_version(tmp_path):
    daemon = _make_daemon(tmp_path)
    daemon._write_sync_record("AAPL_Transformer", "v1")

    backtest_data = json.dumps({
        "sharpe": 1.5, "max_drawdown": -0.05, "hit_rate": 0.60, "n_trades": 50
    }).encode()
    manifest_data = json.dumps({"run_name": "AAPL_Transformer", "model_hash": "abc123"}).encode()

    def fake_download(Bucket, Key, Filename):
        p = Path(Filename)
        p.parent.mkdir(parents=True, exist_ok=True)
        if Key.endswith("backtest.json"):
            p.write_bytes(backtest_data)
        elif Key.endswith("manifest.json"):
            p.write_bytes(manifest_data)
        elif Key.endswith("model.onnx"):
            p.write_bytes(b"\x00" * 8)

    mock_s3 = MagicMock()
    mock_s3.list_objects_v2 = MagicMock(side_effect=[
        # first call: list run prefixes
        {"CommonPrefixes": [{"Prefix": "default/default/AAPL_Transformer/"}]},
        # second call: list files in v2/
        {"Contents": [
            {"Key": "default/default/AAPL_Transformer/v2/manifest.json"},
            {"Key": "default/default/AAPL_Transformer/v2/model.onnx"},
            {"Key": "default/default/AAPL_Transformer/v2/backtest.json"},
        ]}
    ])
    mock_s3.get_object = MagicMock(return_value={
        "Body": MagicMock(read=lambda: json.dumps({"version": "v2"}).encode())
    })
    mock_s3.download_file = MagicMock(side_effect=fake_download)

    with patch("alphaTrade.store.model_sync.ModelSyncDaemon._make_s3_client", return_value=mock_s3):
        promoted = await daemon._sync_once()

    assert "AAPL_Transformer" in promoted
    assert daemon._read_sync_record("AAPL_Transformer") == "v2"
    assert (tmp_path / "models" / "AAPL_Transformer" / "manifest.json").exists()


@pytest.mark.asyncio
async def test_sync_once_rejects_failed_validation(tmp_path):
    daemon = _make_daemon(tmp_path)

    backtest_data = json.dumps({
        "sharpe": -2.1, "max_drawdown": -0.12, "hit_rate": 0.0, "n_trades": 1
    }).encode()
    manifest_data = json.dumps({"run_name": "AAPL_Transformer", "model_hash": "abc123"}).encode()

    def fake_download(Bucket, Key, Filename):
        p = Path(Filename)
        p.parent.mkdir(parents=True, exist_ok=True)
        if Key.endswith("backtest.json"):
            p.write_bytes(backtest_data)
        elif Key.endswith("manifest.json"):
            p.write_bytes(manifest_data)
        elif Key.endswith("model.onnx"):
            p.write_bytes(b"\x00" * 8)

    mock_s3 = MagicMock()
    mock_s3.list_objects_v2 = MagicMock(side_effect=[
        {"CommonPrefixes": [{"Prefix": "default/default/AAPL_Transformer/"}]},
        {"Contents": [
            {"Key": "default/default/AAPL_Transformer/v1/manifest.json"},
            {"Key": "default/default/AAPL_Transformer/v1/model.onnx"},
            {"Key": "default/default/AAPL_Transformer/v1/backtest.json"},
        ]}
    ])
    mock_s3.get_object = MagicMock(return_value={
        "Body": MagicMock(read=lambda: json.dumps({"version": "v1"}).encode())
    })
    mock_s3.download_file = MagicMock(side_effect=fake_download)

    with patch("alphaTrade.store.model_sync.ModelSyncDaemon._make_s3_client", return_value=mock_s3):
        promoted = await daemon._sync_once()

    assert promoted == []
    assert daemon._read_sync_record("AAPL_Transformer") is None
    assert not (tmp_path / "models" / "AAPL_Transformer").exists()
