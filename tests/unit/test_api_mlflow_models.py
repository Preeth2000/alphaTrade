from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI
from mlflow.exceptions import MlflowException


def _make_app(mlflow_tracking_uri: str = "http://mlflow:5000"):
    from alphaTrade.api.routers.models import make_router

    app = FastAPI()

    def noop_session():
        return MagicMock()

    def noop_api_key():
        return None

    router = make_router(
        session_dep=noop_session,
        api_key_dep=noop_api_key,
        registry=None,
        settings=None,
        mlflow_tracking_uri=mlflow_tracking_uri,
    )
    app.include_router(router, prefix="/api/v1")
    return app


def _make_registered_model(name: str, aliases: dict[str, str]) -> MagicMock:
    """aliases: {alias: version_str} e.g. {"production": "2", "staging": "3"}"""
    rm = MagicMock()
    rm.name = name
    rm.aliases = aliases
    return rm


def _make_model_version(version: str, run_id: str = "run123") -> MagicMock:
    mv = MagicMock()
    mv.version = version
    mv.run_id = run_id
    return mv


def test_get_registry_lists_models():
    app = _make_app()
    client = TestClient(app)

    mock_mlflow_client = MagicMock()
    mock_mlflow_client.search_registered_models.return_value = [
        _make_registered_model("AAPL_mlp", {"production": "2", "staging": "3"}),
        _make_registered_model("TSLA_lstm", {"staging": "1"}),
    ]
    mock_mlflow_client.get_model_version_by_alias.side_effect = lambda name, alias: {
        ("AAPL_mlp", "production"): _make_model_version("2", "run_prod"),
        ("AAPL_mlp", "staging"): _make_model_version("3", "run_stg"),
        ("TSLA_lstm", "staging"): _make_model_version("1", "run_tsla"),
    }[(name, alias)]

    with patch("alphaTrade.api.routers.models.MlflowClient", return_value=mock_mlflow_client):
        resp = client.get("/api/v1/models/registry")

    assert resp.status_code == 200
    data = resp.json()
    names = [m["name"] for m in data]
    assert "AAPL_mlp" in names
    assert "TSLA_lstm" in names
    aapl = next(m for m in data if m["name"] == "AAPL_mlp")
    stages = {v["stage"] for v in aapl["versions"]}
    assert "production" in stages
    assert "staging" in stages


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
    mock_mlflow_client.delete_registered_model_alias.return_value = None

    with patch("alphaTrade.api.routers.models.MlflowClient", return_value=mock_mlflow_client):
        resp = client.post("/api/v1/models/AAPL_mlp/promote", json={"version": "3"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["model_name"] == "AAPL_mlp"
    assert body["version"] == "3"
    assert body["stage"] == "production"

    mock_mlflow_client.set_registered_model_alias.assert_called_once_with("AAPL_mlp", "production", "3")


def test_promote_uses_staging_alias_when_version_omitted():
    app = _make_app()
    client = TestClient(app)

    staging_v = _make_model_version("4")
    mock_mlflow_client = MagicMock()
    mock_mlflow_client.get_model_version_by_alias.return_value = staging_v
    mock_mlflow_client.delete_registered_model_alias.return_value = None

    with patch("alphaTrade.api.routers.models.MlflowClient", return_value=mock_mlflow_client):
        resp = client.post("/api/v1/models/AAPL_mlp/promote", json={})

    assert resp.status_code == 200
    assert resp.json()["version"] == "4"
    mock_mlflow_client.get_model_version_by_alias.assert_called_once_with("AAPL_mlp", "staging")
    mock_mlflow_client.set_registered_model_alias.assert_called_once_with("AAPL_mlp", "production", "4")


def test_demote_transitions_to_staging():
    app = _make_app()
    client = TestClient(app)

    mock_mlflow_client = MagicMock()
    mock_mlflow_client.delete_registered_model_alias.return_value = None

    with patch("alphaTrade.api.routers.models.MlflowClient", return_value=mock_mlflow_client):
        resp = client.post("/api/v1/models/AAPL_mlp/demote", json={"version": "2"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["model_name"] == "AAPL_mlp"
    assert body["version"] == "2"
    assert body["stage"] == "staging"

    mock_mlflow_client.set_registered_model_alias.assert_called_once_with("AAPL_mlp", "staging", "2")


def test_promote_returns_404_when_no_staging_alias():
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)

    mock_mlflow_client = MagicMock()
    mock_mlflow_client.get_model_version_by_alias.side_effect = MlflowException("no alias")

    with patch("alphaTrade.api.routers.models.MlflowClient", return_value=mock_mlflow_client):
        resp = client.post("/api/v1/models/AAPL_mlp/promote", json={})

    assert resp.status_code == 404


def test_demote_returns_404_when_no_production_alias():
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)

    mock_mlflow_client = MagicMock()
    mock_mlflow_client.get_model_version_by_alias.side_effect = MlflowException("no alias")

    with patch("alphaTrade.api.routers.models.MlflowClient", return_value=mock_mlflow_client):
        resp = client.post("/api/v1/models/AAPL_mlp/demote", json={})

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /models — MLflow-only model inclusion
# ---------------------------------------------------------------------------

def _make_mlflow_run(params: dict) -> MagicMock:
    run = MagicMock()
    run.data.params = params
    return run


def _make_models_app(tmp_path, mlflow_tracking_uri: str = "http://mlflow:5000", registry=None):
    """Test app for GET /models with a real SQLite session."""
    from sqlmodel import Session, create_engine
    from alphaTrade.store.db import run_migrations
    from alphaTrade.api.routers.models import make_router

    db = tmp_path / "test.db"
    run_migrations(db)
    engine = create_engine(f"sqlite:///{db}")

    def get_session():
        with Session(engine) as s:
            yield s

    app = FastAPI()
    router = make_router(
        session_dep=get_session,
        api_key_dep=lambda: None,
        registry=registry,
        settings=None,
        mlflow_tracking_uri=mlflow_tracking_uri,
    )
    app.include_router(router, prefix="/api/v1")
    return app, engine


def test_list_models_includes_mlflow_only_models(tmp_path):
    """Models in MLflow (staging alias) but unknown to alphaTrade appear in GET /models."""
    app, _ = _make_models_app(tmp_path)
    client = TestClient(app)

    rm = MagicMock()
    rm.name = "AAPL_lstm_v1"
    rm.aliases = {"staging": "1"}

    mv = _make_model_version("1", run_id="run_abc")
    run = _make_mlflow_run({"ticker": "AAPL", "interval": "1d", "arch": "lstm"})

    mock_mlflow_client = MagicMock()
    mock_mlflow_client.search_registered_models.return_value = [rm]
    mock_mlflow_client.get_model_version_by_alias.return_value = mv
    mock_mlflow_client.get_run.return_value = run

    with patch("alphaTrade.api.routers.models.MlflowClient", return_value=mock_mlflow_client):
        resp = client.get("/api/v1/models")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    m = data[0]
    assert m["run_name"] == "AAPL_lstm_v1"
    assert m["active"] is False
    assert m["retired"] is False
    assert m["ticker"] == "AAPL"
    assert m["interval"] == "1d"
    assert m["model_arch"] == "lstm"
    assert m["trade_count"] == 0


def test_list_models_mlflow_only_skipped_when_no_alias(tmp_path):
    """Registered models with no aliases are not included."""
    app, _ = _make_models_app(tmp_path)
    client = TestClient(app)

    rm = MagicMock()
    rm.name = "AAPL_lstm_v1"
    rm.aliases = {}

    mock_mlflow_client = MagicMock()
    mock_mlflow_client.search_registered_models.return_value = [rm]

    with patch("alphaTrade.api.routers.models.MlflowClient", return_value=mock_mlflow_client):
        resp = client.get("/api/v1/models")

    assert resp.status_code == 200
    assert resp.json() == []


def test_list_models_mlflow_unavailable_does_not_fail(tmp_path):
    """MLflow being down degrades gracefully — returns empty list, not 5xx."""
    app, _ = _make_models_app(tmp_path)
    client = TestClient(app)

    mock_mlflow_client = MagicMock()
    mock_mlflow_client.search_registered_models.side_effect = MlflowException("connection refused")

    with patch("alphaTrade.api.routers.models.MlflowClient", return_value=mock_mlflow_client):
        resp = client.get("/api/v1/models")

    assert resp.status_code == 200
    assert resp.json() == []


def test_list_models_no_duplicate_when_model_already_in_db(tmp_path):
    """MLflow model already known to alphaTrade (via DB) is not duplicated."""
    from sqlmodel import Session
    from alphaTrade.store.repos import ModelPerformanceRepo

    app, engine = _make_models_app(tmp_path)
    client = TestClient(app)

    with Session(engine) as s:
        ModelPerformanceRepo(s).get_or_create("AAPL_lstm_v1")

    rm = MagicMock()
    rm.name = "AAPL_lstm_v1"
    rm.aliases = {"staging": "1"}

    mock_mlflow_client = MagicMock()
    mock_mlflow_client.search_registered_models.return_value = [rm]

    with patch("alphaTrade.api.routers.models.MlflowClient", return_value=mock_mlflow_client):
        resp = client.get("/api/v1/models")

    assert resp.status_code == 200
    data = resp.json()
    assert len([m for m in data if m["run_name"] == "AAPL_lstm_v1"]) == 1


def test_promote_inserts_launching_deployment(tmp_path):
    """POST promote creates a model_deployments row with status=launching."""
    from sqlmodel import Session, create_engine, select
    from alphaTrade.store.db import run_migrations
    from alphaTrade.store.repos import ModelDeployment

    app, engine = _make_models_app(tmp_path)
    client = TestClient(app)

    mock_client = MagicMock()
    mock_client.delete_registered_model_alias.return_value = None

    with patch("alphaTrade.api.routers.models.MlflowClient", return_value=mock_client):
        resp = client.post("/api/v1/models/TSLA_v1/promote", json={"version": "2"})

    assert resp.status_code == 200

    with Session(engine) as session:
        rows = list(session.exec(
            select(ModelDeployment).where(ModelDeployment.run_name == "TSLA_v1")
        ).all())

    assert len(rows) == 1
    assert rows[0].status == "launching"
    assert rows[0].activated_at is None


def test_deployments_endpoint_returns_latest_per_model(tmp_path):
    """GET /models/deployments returns latest deployment row per model."""
    from sqlmodel import Session, create_engine
    from alphaTrade.store.db import run_migrations
    from alphaTrade.store.repos import ModelDeploymentRepo

    app, engine = _make_models_app(tmp_path)
    client = TestClient(app)

    # Seed two deployments for same model — latest should win
    with Session(engine) as session:
        repo = ModelDeploymentRepo(session)
        repo.insert_launching("AAPL_v2")
        row = repo.insert_launching("AAPL_v2")
        repo.mark_active("AAPL_v2")  # marks the latest launching row active
        repo.insert_launching("TSLA_v1")

    resp = client.get("/api/v1/models/deployments")
    assert resp.status_code == 200
    data = {d["run_name"]: d for d in resp.json()}

    assert "AAPL_v2" in data
    assert data["AAPL_v2"]["status"] == "active"
    assert "TSLA_v1" in data
    assert data["TSLA_v1"]["status"] == "launching"
