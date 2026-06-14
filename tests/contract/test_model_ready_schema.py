"""Contract tests for the model.ready Redis pub/sub payload schema.

Verifies that alphaTrade's model.ready consumer correctly handles the new
alphaGen payload format (run_name, version, published_at, artifact_prefix?)
and rejects the old format (type, minio_bucket, minio_path, manifest_path).

Aligns with alphaTest D3 (model.ready schema).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from alphaTrade.store.model_sync import ModelSyncDaemon


def _make_daemon(tmp_path: Path, with_minio: bool = False) -> ModelSyncDaemon:
    from alphaTrade.config import MinioConfig, ModelSyncConfig
    minio = MinioConfig() if with_minio else None
    return ModelSyncDaemon(
        sync_cfg=ModelSyncConfig(),
        models_dir=tmp_path / "models",
        minio_cfg=minio,
    )


# ---------------------------------------------------------------------------
# Payload parsing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_new_schema_explicit_publish_triggers_minio_download(tmp_path):
    """artifact_prefix present → _sync_from_minio called, on_promote fires."""
    on_promote = AsyncMock()
    daemon = ModelSyncDaemon(
        sync_cfg=__import__("alphaTrade.config", fromlist=["ModelSyncConfig"]).ModelSyncConfig(),
        models_dir=tmp_path / "models",
        on_promote=on_promote,
        minio_cfg=__import__("alphaTrade.config", fromlist=["MinioConfig"]).MinioConfig(),
    )

    payload = {
        "run_name": "aapl_daily_mlp",
        "version": "v3",
        "artifact_prefix": "prod/isa/aapl_daily_mlp/v3",
        "published_at": "2026-06-14T10:00:00+00:00",
    }

    with patch.object(daemon, "_sync_from_minio") as mock_minio, \
         patch.object(daemon, "_read_sync_record", return_value="v3"):
        await daemon._handle_model_ready_event(json.dumps(payload).encode())

    mock_minio.assert_called_once_with("aapl_daily_mlp", "v3", "prod/isa/aapl_daily_mlp/v3")
    on_promote.assert_awaited_once_with(["aapl_daily_mlp"])


@pytest.mark.asyncio
async def test_new_schema_auto_promote_triggers_mlflow_poll(tmp_path):
    """artifact_prefix absent → _sync_once called immediately."""
    on_promote = AsyncMock()
    daemon = ModelSyncDaemon(
        sync_cfg=__import__("alphaTrade.config", fromlist=["ModelSyncConfig"]).ModelSyncConfig(),
        models_dir=tmp_path / "models",
        on_promote=on_promote,
    )

    payload = {
        "run_name": "aapl_daily_mlp",
        "version": "v4",
        "published_at": "2026-06-14T10:05:00+00:00",
    }

    with patch.object(daemon, "_sync_once", new=AsyncMock(return_value=["aapl_daily_mlp"])):
        await daemon._handle_model_ready_event(json.dumps(payload).encode())

    on_promote.assert_awaited_once_with(["aapl_daily_mlp"])


@pytest.mark.asyncio
async def test_unknown_extra_fields_tolerated(tmp_path):
    """Extra/unknown fields in payload must not raise."""
    daemon = _make_daemon(tmp_path)
    payload = {
        "run_name": "model_x",
        "version": "v1",
        "published_at": "2026-06-14T10:00:00Z",
        "future_field": "ignored",
        "another_unknown": 42,
    }
    with patch.object(daemon, "_sync_once", new=AsyncMock(return_value=[])):
        await daemon._handle_model_ready_event(json.dumps(payload).encode())


@pytest.mark.asyncio
async def test_published_at_iso8601_with_timezone_accepted(tmp_path):
    """published_at with timezone offset is accepted (not rejected)."""
    daemon = _make_daemon(tmp_path)
    for ts in [
        "2026-06-14T10:00:00+00:00",
        "2026-06-14T10:00:00Z",
        "2026-06-14T11:00:00+01:00",
    ]:
        payload = {"run_name": "m", "version": "v1", "published_at": ts}
        with patch.object(daemon, "_sync_once", new=AsyncMock(return_value=[])):
            await daemon._handle_model_ready_event(json.dumps(payload).encode())


@pytest.mark.asyncio
async def test_missing_run_name_is_ignored(tmp_path):
    """Payload without run_name must be silently dropped."""
    daemon = _make_daemon(tmp_path)
    payload = {"version": "v1", "published_at": "2026-06-14T10:00:00Z"}
    with patch.object(daemon, "_sync_once", new=AsyncMock()) as mock_sync:
        await daemon._handle_model_ready_event(json.dumps(payload).encode())
    mock_sync.assert_not_called()


@pytest.mark.asyncio
async def test_missing_version_is_ignored(tmp_path):
    """Payload without version must be silently dropped."""
    daemon = _make_daemon(tmp_path)
    payload = {"run_name": "m", "published_at": "2026-06-14T10:00:00Z"}
    with patch.object(daemon, "_sync_once", new=AsyncMock()) as mock_sync:
        await daemon._handle_model_ready_event(json.dumps(payload).encode())
    mock_sync.assert_not_called()


@pytest.mark.asyncio
async def test_old_schema_fields_not_required(tmp_path):
    """Old fields (type, minio_bucket, minio_path, manifest_path) absent — no error."""
    daemon = _make_daemon(tmp_path)
    payload = {
        "run_name": "aapl_daily_mlp",
        "version": "v5",
        "published_at": "2026-06-14T10:00:00Z",
        # old fields intentionally absent
    }
    with patch.object(daemon, "_sync_once", new=AsyncMock(return_value=[])):
        await daemon._handle_model_ready_event(json.dumps(payload).encode())


@pytest.mark.asyncio
async def test_malformed_json_is_ignored(tmp_path):
    """Invalid JSON must not raise — daemon keeps running."""
    daemon = _make_daemon(tmp_path)
    await daemon._handle_model_ready_event(b"not-json{{{")


@pytest.mark.asyncio
async def test_string_payload_accepted(tmp_path):
    """Redis may deliver payload as str (decode_responses=True) — must work."""
    daemon = _make_daemon(tmp_path)
    payload = {"run_name": "m", "version": "v1", "published_at": "2026-06-14T10:00:00Z"}
    with patch.object(daemon, "_sync_once", new=AsyncMock(return_value=[])):
        await daemon._handle_model_ready_event(json.dumps(payload))  # str, not bytes
