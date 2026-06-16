"""Tests for role-gated redaction of failure_msg in GET /models/deployments."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from alphaTrade.store.db import run_migrations
from alphaTrade.store.repos import ModelDeploymentRepo


def _make_engine(tmp_path: Path):
    db = tmp_path / "test.db"
    run_migrations(db)
    return create_engine(f"sqlite:///{db}")


def _make_client(engine, role: str | None, user_id: str = "user-1"):
    from alphaTrade.api.routers.models import make_router
    from alphaTrade.api.deps import make_session_dep

    app = FastAPI()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user_id = user_id
        if role is not None:
            request.state.role = role
        return await call_next(request)

    session_dep = make_session_dep(engine)

    def noop_api_key():
        return None

    router = make_router(session_dep=session_dep, api_key_dep=noop_api_key)
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


def _seed_failed_deployment(engine, run_name: str = "my_model", user_id: str = "user-1"):
    with Session(engine) as s:
        repo = ModelDeploymentRepo(s)
        repo.insert_launching(run_name, user_id=user_id)
        repo.mark_failed(run_name, "MLflow path /mnt/internal/host-7 unreachable")


class TestDeploymentFailureMsgRedaction:
    def test_standard_role_gets_null_failure_msg(self, tmp_path):
        engine = _make_engine(tmp_path)
        _seed_failed_deployment(engine)
        client = _make_client(engine, role="standard")
        resp = client.get("/api/v1/models/deployments")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["failure_msg"] is None
        assert body[0]["failed_at"] is not None

    def test_developer_role_sees_raw_failure_msg(self, tmp_path):
        engine = _make_engine(tmp_path)
        _seed_failed_deployment(engine)
        client = _make_client(engine, role="developer")
        resp = client.get("/api/v1/models/deployments")
        body = resp.json()
        assert body[0]["failure_msg"] == "MLflow path /mnt/internal/host-7 unreachable"

    def test_admin_role_sees_raw_failure_msg(self, tmp_path):
        engine = _make_engine(tmp_path)
        _seed_failed_deployment(engine)
        client = _make_client(engine, role="admin")
        resp = client.get("/api/v1/models/deployments")
        body = resp.json()
        assert body[0]["failure_msg"] == "MLflow path /mnt/internal/host-7 unreachable"

    def test_legacy_no_role_gets_null_failure_msg(self, tmp_path):
        """Legacy/API-key auth never sets request.state.role — must fail closed."""
        engine = _make_engine(tmp_path)
        _seed_failed_deployment(engine)
        client = _make_client(engine, role=None)
        resp = client.get("/api/v1/models/deployments")
        body = resp.json()
        assert body[0]["failure_msg"] is None
