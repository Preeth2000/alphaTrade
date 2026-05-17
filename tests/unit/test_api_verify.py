from __future__ import annotations
import pytest
import respx
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import create_engine
from alphaTrade.store.db import run_migrations
from alphaTrade.api.auth import make_api_key_dep
from alphaTrade.store.repos import BotSettings, BotSettingsRepo


def _make_client(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(db)
    engine = create_engine(f"sqlite:///{db}")
    api_key_dep = make_api_key_dep(engine)
    from alphaTrade.api.routers.verify import make_router
    app = FastAPI()
    app.include_router(make_router(api_key_dep), prefix="/api/v1")
    return TestClient(app)


# --- T212 ---

@respx.mock
def test_t212_demo_valid(tmp_path):
    respx.get("https://demo.trading212.com/api/v0/equity/account/summary").mock(
        return_value=httpx.Response(200, json={"cash": {"free": 1000.0}, "pieCash": 0.0})
    )
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/t212", json={
        "account": "demo", "api_key": "test-key", "secret_key": "test-secret"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["account"] == "demo"
    assert "details" in data


@respx.mock
def test_t212_demo_invalid_credentials(tmp_path):
    respx.get("https://demo.trading212.com/api/v0/equity/account/summary").mock(
        return_value=httpx.Response(401, json={"message": "Unauthorized"})
    )
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/t212", json={
        "account": "demo", "api_key": "bad-key", "secret_key": "bad-secret"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is False
    assert "error" in data
    assert "401" in data["error"]


@respx.mock
def test_t212_invest_uses_live_url(tmp_path):
    respx.get("https://live.trading212.com/api/v0/equity/account/summary").mock(
        return_value=httpx.Response(200, json={"cash": {"free": 500.0}, "pieCash": 0.0})
    )
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/t212", json={
        "account": "invest", "api_key": "key", "secret_key": "secret"
    })
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


@respx.mock
def test_t212_isa_uses_live_url(tmp_path):
    respx.get("https://live.trading212.com/api/v0/equity/account/summary").mock(
        return_value=httpx.Response(200, json={"cash": {"free": 200.0}, "pieCash": 0.0})
    )
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/t212", json={
        "account": "isa", "api_key": "key", "secret_key": "secret"
    })
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


@respx.mock
def test_t212_network_error_returns_invalid(tmp_path):
    respx.get("https://demo.trading212.com/api/v0/equity/account/summary").mock(
        side_effect=httpx.ConnectError("timeout")
    )
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/t212", json={
        "account": "demo", "api_key": "key", "secret_key": "secret"
    })
    assert resp.status_code == 200
    assert resp.json()["valid"] is False


@respx.mock
def test_t212_requires_api_key(tmp_path):
    from sqlmodel import Session
    db = tmp_path / "test.db"
    run_migrations(db)
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as s:
        BotSettingsRepo(s).upsert(BotSettings(id=1, alphaTrade_api_key="secret"))
    api_key_dep = make_api_key_dep(engine)
    from alphaTrade.api.routers.verify import make_router
    app = FastAPI()
    app.include_router(make_router(api_key_dep), prefix="/api/v1")
    client = TestClient(app)
    resp = client.post("/api/v1/verify/t212", json={
        "account": "demo", "api_key": "t212key", "secret_key": "t212secret"
    }, headers={"X-API-Key": "wrong"})
    assert resp.status_code == 403


# --- Polygon ---

@respx.mock
def test_polygon_valid_returns_details(tmp_path):
    exchanges = [{"id": 1, "name": "NYSE"}, {"id": 2, "name": "NASDAQ"}]
    respx.get("https://api.polygon.io/v1/meta/exchanges").mock(
        return_value=httpx.Response(
            200,
            json=exchanges,
            headers={"X-RateLimit-Limit": "5"},
        )
    )
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/polygon", json={"api_key": "good-key"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["details"]["exchanges_count"] == 2
    assert data["details"]["rate_limit"] == "5"


@respx.mock
def test_polygon_invalid_key(tmp_path):
    respx.get("https://api.polygon.io/v1/meta/exchanges").mock(
        return_value=httpx.Response(403, json={"status": "ERROR", "error": "Forbidden"})
    )
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/polygon", json={"api_key": "bad-key"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is False
    assert "error" in data
    assert "403" in data["error"]


@respx.mock
def test_polygon_network_error(tmp_path):
    respx.get("https://api.polygon.io/v1/meta/exchanges").mock(
        side_effect=httpx.ConnectError("unreachable")
    )
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/polygon", json={"api_key": "key"})
    assert resp.status_code == 200
    assert resp.json()["valid"] is False


@respx.mock
def test_polygon_no_rate_limit_header(tmp_path):
    respx.get("https://api.polygon.io/v1/meta/exchanges").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "name": "NYSE"}])
    )
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/polygon", json={"api_key": "key"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["details"]["rate_limit"] is None
    assert data["details"]["exchanges_count"] == 1


@respx.mock
def test_polygon_requires_api_key(tmp_path):
    from sqlmodel import Session
    from alphaTrade.store.repos import BotSettings, BotSettingsRepo
    db = tmp_path / "test.db"
    run_migrations(db)
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as s:
        BotSettingsRepo(s).upsert(BotSettings(id=1, alphaTrade_api_key="secret"))
    api_key_dep = make_api_key_dep(engine)
    from alphaTrade.api.routers.verify import make_router
    app = FastAPI()
    app.include_router(make_router(api_key_dep), prefix="/api/v1")
    client = TestClient(app)
    resp = client.post("/api/v1/verify/polygon", json={"api_key": "poly-key"},
                       headers={"X-API-Key": "wrong"})
    assert resp.status_code == 403


# --- alphaTrade API key ---

def test_alphatrade_key_no_key_configured_returns_valid(tmp_path, monkeypatch):
    monkeypatch.delenv("alphaTrade_API_KEY", raising=False)
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/alphatrade-key")
    assert resp.status_code == 200
    assert resp.json() == {"valid": True}


def test_alphatrade_key_correct_key_returns_valid(tmp_path, monkeypatch):
    monkeypatch.setenv("alphaTrade_API_KEY", "secret")
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/alphatrade-key", headers={"X-API-Key": "secret"})
    assert resp.status_code == 200
    assert resp.json() == {"valid": True}


def test_alphatrade_key_wrong_key_returns_403(tmp_path, monkeypatch):
    monkeypatch.setenv("alphaTrade_API_KEY", "secret")
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/alphatrade-key", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 403


def test_alphatrade_key_missing_header_returns_403(tmp_path, monkeypatch):
    monkeypatch.setenv("alphaTrade_API_KEY", "secret")
    client = _make_client(tmp_path)
    resp = client.post("/api/v1/verify/alphatrade-key")
    assert resp.status_code == 403
