import pytest
from fastapi.testclient import TestClient
from sqlmodel import create_engine, Session
from alphalink.store.db import run_migrations
from alphalink.health import HealthState


def _engine(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(db)
    return create_engine(f"sqlite:///{db}")


def _client(engine, health_state=None):
    from alphalink.api.app import create_app
    return TestClient(create_app(engine, health_state or HealthState()))


# --- Positions ---

def test_positions_empty(tmp_path):
    client = _client(_engine(tmp_path))
    resp = client.get("/api/v1/positions")
    assert resp.status_code == 200
    assert resp.json() == []


def test_positions_returns_rows(tmp_path):
    engine = _engine(tmp_path)
    from alphalink.store.repos import Position, PositionRepo
    from datetime import datetime
    with Session(engine) as s:
        PositionRepo(s).upsert(Position(
            t212_ticker="AAPL_US_EQ", quantity=10.0, avg_entry=150.0,
            opened_at=datetime(2026, 1, 1),
        ))
    resp = _client(engine).get("/api/v1/positions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["t212_ticker"] == "AAPL_US_EQ"


# --- Health ---

def test_health_returns_state(tmp_path):
    state = HealthState()
    state.t212_ok = True
    state.models_loaded = True
    resp = _client(_engine(tmp_path), state).get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["t212_ok"] is True
    assert body["models_loaded"] is True
    assert body["last_tick_at"] is None
