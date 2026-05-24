from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI


def _make_app(mlflow_tracking_uri: str = "http://mlflow:5000"):
    from alphaTrade.api.routers.models import make_router

    app = FastAPI()

    def noop_session():
        return MagicMock()

    def noop_api_key():
        return None

    router = make_router(
        session_dep=lambda: noop_session,
        api_key_dep=lambda: noop_api_key,
        registry=None,
        settings=None,
        mlflow_tracking_uri=mlflow_tracking_uri,
    )
    app.include_router(router, prefix="/api/v1")
    return app


def _make_registered_model(name: str, versions: list[dict]) -> MagicMock:
    rm = MagicMock()
    rm.name = name
    rm.latest_versions = []
    for v in versions:
        mv = MagicMock()
        mv.version = v["version"]
        mv.current_stage = v["stage"]
        mv.run_id = v.get("run_id", "run123")
        rm.latest_versions.append(mv)
    return rm


def test_get_registry_lists_models():
    app = _make_app()
    client = TestClient(app)

    mock_mlflow_client = MagicMock()
    mock_mlflow_client.search_registered_models.return_value = [
        _make_registered_model("AAPL_mlp", [
            {"version": "2", "stage": "Production"},
            {"version": "3", "stage": "Staging"},
        ]),
        _make_registered_model("TSLA_lstm", [
            {"version": "1", "stage": "Staging"},
        ]),
    ]

    with patch("alphaTrade.api.routers.models.MlflowClient", return_value=mock_mlflow_client):
        resp = client.get("/api/v1/models/registry")

    assert resp.status_code == 200
    data = resp.json()
    names = [m["name"] for m in data]
    assert "AAPL_mlp" in names
    assert "TSLA_lstm" in names


def test_get_registry_returns_503_when_no_mlflow_uri():
    from alphaTrade.api.routers.models import make_router
    app = FastAPI()

    def noop_session():
        return MagicMock()
    def noop_api_key():
        return None

    router = make_router(
        session_dep=lambda: noop_session,
        api_key_dep=lambda: noop_api_key,
        registry=None,
        settings=None,
        mlflow_tracking_uri=None,
    )
    app.include_router(router, prefix="/api/v1")
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/v1/models/registry")
    assert resp.status_code == 503


def test_promote_transitions_to_production():
    app = _make_app()
    client = TestClient(app)

    mock_mlflow_client = MagicMock()
    promoted_version = MagicMock()
    promoted_version.version = "3"
    promoted_version.current_stage = "Production"
    mock_mlflow_client.transition_model_version_stage.return_value = promoted_version

    with patch("alphaTrade.api.routers.models.MlflowClient", return_value=mock_mlflow_client):
        resp = client.post("/api/v1/models/AAPL_mlp/promote", json={"version": "3"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["model_name"] == "AAPL_mlp"
    assert body["version"] == "3"
    assert body["stage"] == "Production"

    mock_mlflow_client.transition_model_version_stage.assert_called_once_with(
        name="AAPL_mlp",
        version="3",
        to_stage="Production",
        archive_existing_versions=True,
    )


def test_promote_uses_latest_staging_when_version_omitted():
    app = _make_app()
    client = TestClient(app)

    staging_v = MagicMock()
    staging_v.version = "4"

    mock_mlflow_client = MagicMock()
    mock_mlflow_client.get_latest_versions.return_value = [staging_v]
    promoted_v = MagicMock()
    promoted_v.version = "4"
    promoted_v.current_stage = "Production"
    mock_mlflow_client.transition_model_version_stage.return_value = promoted_v

    with patch("alphaTrade.api.routers.models.MlflowClient", return_value=mock_mlflow_client):
        resp = client.post("/api/v1/models/AAPL_mlp/promote", json={})

    assert resp.status_code == 200
    assert resp.json()["version"] == "4"


def test_demote_transitions_to_staging():
    app = _make_app()
    client = TestClient(app)

    mock_mlflow_client = MagicMock()
    demoted_v = MagicMock()
    demoted_v.version = "2"
    demoted_v.current_stage = "Staging"
    mock_mlflow_client.transition_model_version_stage.return_value = demoted_v

    with patch("alphaTrade.api.routers.models.MlflowClient", return_value=mock_mlflow_client):
        resp = client.post("/api/v1/models/AAPL_mlp/demote", json={"version": "2"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["model_name"] == "AAPL_mlp"
    assert body["version"] == "2"
    assert body["stage"] == "Staging"

    mock_mlflow_client.transition_model_version_stage.assert_called_once_with(
        name="AAPL_mlp",
        version="2",
        to_stage="Staging",
    )


def test_promote_returns_404_when_no_staging_version():
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)

    mock_mlflow_client = MagicMock()
    mock_mlflow_client.get_latest_versions.return_value = []  # no Staging versions

    with patch("alphaTrade.api.routers.models.MlflowClient", return_value=mock_mlflow_client):
        resp = client.post("/api/v1/models/AAPL_mlp/promote", json={})

    assert resp.status_code == 404


def test_demote_returns_404_when_no_production_version():
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)

    mock_mlflow_client = MagicMock()
    mock_mlflow_client.get_latest_versions.return_value = []  # no Production versions

    with patch("alphaTrade.api.routers.models.MlflowClient", return_value=mock_mlflow_client):
        resp = client.post("/api/v1/models/AAPL_mlp/demote", json={})

    assert resp.status_code == 404
