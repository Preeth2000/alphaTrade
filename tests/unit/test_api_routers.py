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


# --- Orders ---

from datetime import datetime, timedelta


def test_orders_empty_defaults_24h(tmp_path):
    resp = _client(_engine(tmp_path)).get("/api/v1/orders")
    assert resp.status_code == 200
    assert resp.json() == []


def test_orders_since_filters(tmp_path):
    engine = _engine(tmp_path)
    from alphalink.store.repos import Order
    with Session(engine) as s:
        s.add(Order(ts=datetime(2020, 1, 1), t212_ticker="OLD", side="BUY", quantity=1.0, status="filled"))
        s.add(Order(ts=datetime(2026, 1, 1), t212_ticker="NEW", side="BUY", quantity=1.0, status="filled"))
        s.commit()
    resp = _client(engine).get("/api/v1/orders?since=2025-01-01T00:00:00&limit=50")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["t212_ticker"] == "NEW"


# --- Signals ---

def test_signals_since_filters(tmp_path):
    engine = _engine(tmp_path)
    from alphalink.store.repos import Signal
    with Session(engine) as s:
        s.add(Signal(ts=datetime(2020, 1, 1), run_name="r", ticker="OLD", signal="BUY"))
        s.add(Signal(ts=datetime(2026, 1, 1), run_name="r", ticker="NEW", signal="SELL"))
        s.commit()
    resp = _client(engine).get("/api/v1/signals?since=2025-01-01T00:00:00")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["ticker"] == "NEW"
