# tests/unit/test_model_sync.py
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from mlflow.exceptions import MlflowException
from alphaTrade.config import Settings
from alphaTrade.store.model_sync import ModelSyncDaemon


# --- Settings / config tests ---

def test_settings_has_minio_defaults():
    s = Settings()
    assert s.minio.endpoint == "localhost:9000"
    assert s.minio.access_key == "minioadmin"
    assert s.minio.bucket == "models"


def test_settings_has_sync_defaults():
    s = Settings()
    assert s.model_sync.poll_interval == 60
    assert s.model_sync.enabled is True


def test_model_sync_poll_interval_overridable_via_env(monkeypatch):
    monkeypatch.setenv("MODEL_SYNC__POLL_INTERVAL", "120")
    s = Settings()
    assert s.model_sync.poll_interval == 120


# --- ModelSyncDaemon helper ---

def _make_daemon(tmp_path: Path, cfg=None) -> ModelSyncDaemon:
    from alphaTrade.config import ModelSyncConfig as MSC
    cfg = cfg or MSC()
    return ModelSyncDaemon(
        sync_cfg=cfg,
        models_dir=tmp_path / "models",
    )


# --- Sync record + promote tests ---

def test_sync_record_roundtrip(tmp_path):
    daemon = _make_daemon(tmp_path)
    daemon._write_sync_record("AAPL_Transformer", "3")
    assert daemon._read_sync_record("AAPL_Transformer") == "3"


def test_sync_record_missing_returns_none(tmp_path):
    daemon = _make_daemon(tmp_path)
    assert daemon._read_sync_record("AAPL_Transformer") is None


def test_promote_copies_and_writes_record(tmp_path):
    daemon = _make_daemon(tmp_path)
    src = tmp_path / "tmp" / "AAPL_Transformer"
    src.mkdir(parents=True)
    (src / "manifest.json").write_text('{"run_name": "AAPL_Transformer"}')
    (src / "model.onnx").write_bytes(b"\x00\x01")

    daemon._promote(src, "AAPL_Transformer", "2")

    dest = daemon._models_dir / "AAPL_Transformer"
    assert (dest / "manifest.json").exists()
    assert (dest / "model.onnx").exists()
    assert daemon._read_sync_record("AAPL_Transformer") == "2"


# --- MLflow polling tests ---

def _make_mock_production_version(name: str, version: str, run_id: str) -> MagicMock:
    v = MagicMock()
    v.version = version
    v.run_id = run_id
    v.name = name
    return v


def _make_mock_registered_model(name: str) -> MagicMock:
    rm = MagicMock()
    rm.name = name
    return rm


@pytest.mark.asyncio
async def test_sync_once_skips_when_version_unchanged(tmp_path):
    daemon = _make_daemon(tmp_path)
    daemon._write_sync_record("AAPL_mlp", "3")

    mock_client = MagicMock()
    mock_client.search_registered_models.return_value = [
        _make_mock_registered_model("AAPL_mlp")
    ]
    mock_client.get_model_version_by_alias.side_effect = lambda name, alias: (
        _make_mock_production_version("AAPL_mlp", "3", "run999")
        if alias == "production"
        else (_ for _ in ()).throw(MlflowException("no alias"))
    )

    with patch("alphaTrade.store.model_sync.MlflowClient", return_value=mock_client):
        promoted = await daemon._sync_once()

    assert promoted == []


@pytest.mark.asyncio
async def test_sync_once_downloads_and_promotes_on_new_production_version(tmp_path):
    daemon = _make_daemon(tmp_path)
    daemon._write_sync_record("AAPL_mlp", "2")

    backtest_data = json.dumps({
        "sharpe": 1.5, "max_drawdown": -0.05, "hit_rate": 0.60, "n_trades": 50
    })

    def fake_download_artifacts(run_id=None, artifact_path=None, dst_path=None):
        dst = Path(dst_path)
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "backtest.json").write_text(backtest_data)
        (dst / "manifest.json").write_text('{"run_name": "AAPL_mlp"}')
        (dst / "model.onnx").write_bytes(b"\x00" * 8)
        return str(dst)

    mock_client = MagicMock()
    mock_client.search_registered_models.return_value = [
        _make_mock_registered_model("AAPL_mlp")
    ]
    mock_client.get_model_version_by_alias.side_effect = lambda name, alias: (
        _make_mock_production_version("AAPL_mlp", "3", "run456")
        if alias == "production"
        else (_ for _ in ()).throw(MlflowException("no alias"))
    )

    with patch("alphaTrade.store.model_sync.MlflowClient", return_value=mock_client), \
         patch("alphaTrade.store.model_sync.mlflow") as mock_mlflow:
        mock_mlflow.artifacts.download_artifacts.side_effect = fake_download_artifacts
        promoted = await daemon._sync_once()

    assert promoted == ["AAPL_mlp"]
    assert daemon._read_sync_record("AAPL_mlp") == "3"
    assert (daemon._models_dir / "AAPL_mlp" / "model.onnx").exists()


@pytest.mark.asyncio
async def test_sync_once_fails_permanently_when_backtest_json_missing(tmp_path):
    daemon = _make_daemon(tmp_path)

    def fake_download_artifacts(run_id=None, artifact_path=None, dst_path=None):
        dst = Path(dst_path)
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "model.onnx").write_bytes(b"\x00")
        # deliberately no backtest.json
        return str(dst)

    mock_client = MagicMock()
    mock_client.search_registered_models.return_value = [
        _make_mock_registered_model("AAPL_mlp")
    ]
    mock_client.get_model_version_by_alias.side_effect = lambda name, alias: (
        _make_mock_production_version("AAPL_mlp", "1", "run001")
        if alias == "production"
        else (_ for _ in ()).throw(MlflowException("no alias"))
    )

    with patch("alphaTrade.store.model_sync.MlflowClient", return_value=mock_client), \
         patch("alphaTrade.store.model_sync.mlflow") as mock_mlflow:
        mock_mlflow.artifacts.download_artifacts.side_effect = fake_download_artifacts
        promoted = await daemon._sync_once()

    assert promoted == []
    assert daemon._read_sync_record("AAPL_mlp") == "FAILED:1"


@pytest.mark.asyncio
async def test_sync_once_fails_permanently_when_no_run_id(tmp_path):
    daemon = _make_daemon(tmp_path)

    no_run_id_version = MagicMock()
    no_run_id_version.version = "1"
    no_run_id_version.run_id = None
    no_run_id_version.name = "AAPL_mlp"

    mock_client = MagicMock()
    mock_client.search_registered_models.return_value = [
        _make_mock_registered_model("AAPL_mlp")
    ]
    mock_client.get_model_version_by_alias.side_effect = lambda name, alias: (
        no_run_id_version
        if alias == "production"
        else (_ for _ in ()).throw(MlflowException("no alias"))
    )

    with patch("alphaTrade.store.model_sync.MlflowClient", return_value=mock_client), \
         patch("alphaTrade.store.model_sync.mlflow"):
        promoted = await daemon._sync_once()

    assert promoted == []
    assert daemon._read_sync_record("AAPL_mlp") == "FAILED:1"


@pytest.mark.asyncio
async def test_sync_once_notifies_new_staging_model(tmp_path):
    daemon = _make_daemon(tmp_path)

    staging_v = MagicMock()
    staging_v.version = "1"
    staging_v.name = "AAPL_lstm"

    mock_client = MagicMock()
    mock_client.search_registered_models.return_value = [
        _make_mock_registered_model("AAPL_lstm")
    ]
    mock_client.get_model_version_by_alias.side_effect = lambda name, alias: (
        staging_v if alias == "staging"
        else (_ for _ in ()).throw(MlflowException("no alias"))
    )

    notify_calls = []
    daemon._notify_staging = lambda name, version: notify_calls.append((name, version))

    with patch("alphaTrade.store.model_sync.MlflowClient", return_value=mock_client), \
         patch("alphaTrade.store.model_sync.mlflow"):
        await daemon._sync_once()

    assert ("AAPL_lstm", "1") in notify_calls


@pytest.mark.asyncio
async def test_sync_once_does_not_re_notify_seen_staging(tmp_path):
    daemon = _make_daemon(tmp_path)
    daemon._seen_staging.add("AAPL_lstm:v1")

    staging_v = MagicMock()
    staging_v.version = "1"
    staging_v.name = "AAPL_lstm"

    mock_client = MagicMock()
    mock_client.search_registered_models.return_value = [
        _make_mock_registered_model("AAPL_lstm")
    ]
    mock_client.get_model_version_by_alias.side_effect = lambda name, alias: (
        staging_v if alias == "staging"
        else (_ for _ in ()).throw(MlflowException("no alias"))
    )

    notify_calls = []
    daemon._notify_staging = lambda name, version: notify_calls.append((name, version))

    with patch("alphaTrade.store.model_sync.MlflowClient", return_value=mock_client), \
         patch("alphaTrade.store.model_sync.mlflow"):
        await daemon._sync_once()

    assert notify_calls == []
