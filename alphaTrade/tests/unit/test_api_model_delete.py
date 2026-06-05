"""Tests for DELETE /models/{run_name}."""
from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from mlflow.exceptions import MlflowException
from sqlmodel import Session, create_engine

from alphaTrade.api.app import create_app
from alphaTrade.health import HealthState
from alphaTrade.store.db import run_migrations
from alphaTrade.store.repos import (
    ModelAdoption,
    ModelAdoptionRepo,
    ModelOverrideRecord,
    ModelOverrideRepo,
)


def _make_engine(tmp_path: Path):
    db = tmp_path / "test.db"
    run_migrations(db)
    return create_engine(f"sqlite:///{db}")


def _make_router(
    engine=None,
    user_id: str = "user-1",
    role: str = "standard",
    mlflow_tracking_uri: str = "http://mlflow:5000",
    registry=None,
    settings=None,
):
    """Build a minimal FastAPI app with the models router and injected request state."""
    from alphaTrade.api.routers.models import make_router
    from alphaTrade.api.deps import make_session_dep

    app = FastAPI()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user_id = user_id
        request.state.role = role
        return await call_next(request)

    if engine is not None:
        session_dep = make_session_dep(engine)
    else:
        def session_dep():
            return MagicMock()

    def noop_api_key():
        return None

    router = make_router(
        session_dep=session_dep,
        api_key_dep=noop_api_key,
        registry=registry,
        settings=settings,
        mlflow_tracking_uri=mlflow_tracking_uri,
    )
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


def _mock_mlflow_client(owner_user_id: str = "user-1") -> MagicMock:
    client = MagicMock()
    mv = MagicMock()
    mv.tags = {"user_id": owner_user_id}
    client.get_model_version_by_alias.return_value = mv
    return client


class TestDeleteModelOwnership:
    def test_returns_200_for_owner(self):
        client = _make_router(user_id="user-1")
        with patch("alphaTrade.api.routers.models.MlflowClient", return_value=_mock_mlflow_client("user-1")):
            resp = client.delete("/api/v1/models/my_model")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": True, "run_name": "my_model"}

    def test_returns_403_for_non_owner(self):
        client = _make_router(user_id="user-2")
        with patch("alphaTrade.api.routers.models.MlflowClient", return_value=_mock_mlflow_client("user-1")):
            resp = client.delete("/api/v1/models/my_model")
        assert resp.status_code == 403

    def test_admin_can_delete_own_legacy_model(self):
        """Legacy models (no user_id tag) are deletable by anyone including admin."""
        client = _make_router(user_id="admin-id", role="admin")
        mock_client = MagicMock()
        mock_mv = MagicMock()
        mock_mv.tags = {}  # no user_id tag = legacy
        mock_client.get_model_version_by_alias.return_value = mock_mv
        with patch("alphaTrade.api.routers.models.MlflowClient", return_value=mock_client):
            resp = client.delete("/api/v1/models/legacy_model")
        assert resp.status_code == 200

    def test_admin_can_delete_user_owned_model(self):
        """Admin (user_id=None from _req_user) must bypass ownership check entirely."""
        client = _make_router(user_id="admin-id", role="admin")
        with patch("alphaTrade.api.routers.models.MlflowClient", return_value=_mock_mlflow_client("other-user")):
            resp = client.delete("/api/v1/models/someone_elses_model")
        assert resp.status_code == 200


class TestDeleteModelDbCleanup:
    def test_deletes_override_record(self, tmp_path):
        engine = _make_engine(tmp_path)
        with Session(engine) as s:
            ModelOverrideRepo(s).upsert(ModelOverrideRecord(run_name="my_model", visibility="private"))
        client = _make_router(engine=engine, user_id="user-1")
        with patch("alphaTrade.api.routers.models.MlflowClient", return_value=_mock_mlflow_client("user-1")):
            resp = client.delete("/api/v1/models/my_model")
        assert resp.status_code == 200
        with Session(engine) as s:
            assert ModelOverrideRepo(s).get("my_model") is None

    def test_deletes_adoption_rows(self, tmp_path):
        engine = _make_engine(tmp_path)
        with Session(engine) as s:
            ModelAdoptionRepo(s).adopt("user-2", "my_model", source_user_id="user-1")
            ModelAdoptionRepo(s).adopt("user-3", "my_model", source_user_id="user-1")
        client = _make_router(engine=engine, user_id="user-1")
        with patch("alphaTrade.api.routers.models.MlflowClient", return_value=_mock_mlflow_client("user-1")):
            resp = client.delete("/api/v1/models/my_model")
        assert resp.status_code == 200
        with Session(engine) as s:
            assert ModelAdoptionRepo(s).adopted_models("user-2") == []
            assert ModelAdoptionRepo(s).adopted_models("user-3") == []

    def test_succeeds_with_no_override_or_adoptions(self, tmp_path):
        """Model may have no DB records — should not error."""
        engine = _make_engine(tmp_path)
        client = _make_router(engine=engine, user_id="user-1")
        with patch("alphaTrade.api.routers.models.MlflowClient", return_value=_mock_mlflow_client("user-1")):
            resp = client.delete("/api/v1/models/clean_model")
        assert resp.status_code == 200


class TestDeleteModelBestEffort:
    def test_returns_200_when_mlflow_delete_fails(self, tmp_path):
        """MLflow failure is logged but does not abort the delete."""
        engine = _make_engine(tmp_path)
        mock_client = _mock_mlflow_client("user-1")
        mock_client.delete_registered_model.side_effect = MlflowException("not found")
        client = _make_router(engine=engine, user_id="user-1")
        with patch("alphaTrade.api.routers.models.MlflowClient", return_value=mock_client):
            resp = client.delete("/api/v1/models/my_model")
        assert resp.status_code == 200

    def test_returns_200_when_mlflow_not_configured(self, tmp_path):
        engine = _make_engine(tmp_path)
        client = _make_router(engine=engine, user_id="user-1", mlflow_tracking_uri="")
        # No MLflow URI — _try_mlflow_client returns None, ownership check falls through to legacy path
        resp = client.delete("/api/v1/models/legacy_model")
        assert resp.status_code == 200

    def test_deletes_local_model_dir(self, tmp_path):
        from alphaTrade.config import Settings
        engine = _make_engine(tmp_path)
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        model_dir = models_dir / "my_model"
        model_dir.mkdir()
        (model_dir / "model.onnx").write_bytes(b"fake")
        sync_dir = models_dir / ".sync"
        sync_dir.mkdir()
        (sync_dir / "my_model").write_text("1")

        settings = MagicMock()
        settings.models_dir = models_dir
        settings.minio.endpoint = "localhost:9000"
        settings.minio.access_key = "key"
        settings.minio.secret_key = "secret"
        settings.minio.bucket = "models"
        settings.model_sync.user = "u"
        settings.model_sync.account = "a"

        client = _make_router(engine=engine, user_id="user-1", settings=settings)
        with patch("alphaTrade.api.routers.models.MlflowClient", return_value=_mock_mlflow_client("user-1")), \
             patch("boto3.client") as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.get_paginator.return_value.paginate.return_value = [{"Contents": []}]
            resp = client.delete("/api/v1/models/my_model")

        assert resp.status_code == 200
        assert not model_dir.exists()
        assert not (sync_dir / "my_model").exists()
