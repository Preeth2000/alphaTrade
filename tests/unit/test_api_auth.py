from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import create_engine
from alphaTrade.store.db import run_migrations


def _make_app(tmp_path, api_key_env: str = ""):
    db = tmp_path / "test.db"
    run_migrations(db)
    engine = create_engine(f"sqlite:///{db}")
    from alphaTrade.api.auth import make_api_key_dep
    app = FastAPI()
    dep = make_api_key_dep()

    @app.get("/test")
    def _route(_: None = __import__("fastapi").Depends(dep)):
        return {"ok": True}

    return TestClient(app), engine


def test_no_key_configured_allows_all(tmp_path, monkeypatch):
    monkeypatch.delenv("alphaTrade_API_KEY", raising=False)
    client, _ = _make_app(tmp_path)
    resp = client.get("/test")
    assert resp.status_code == 200


def test_wrong_key_returns_403(tmp_path, monkeypatch):
    monkeypatch.setenv("alphaTrade_API_KEY", "secret")
    client, _ = _make_app(tmp_path)
    resp = client.get("/test", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 403


def test_correct_key_returns_200(tmp_path, monkeypatch):
    monkeypatch.setenv("alphaTrade_API_KEY", "secret")
    client, _ = _make_app(tmp_path)
    resp = client.get("/test", headers={"X-API-Key": "secret"})
    assert resp.status_code == 200
