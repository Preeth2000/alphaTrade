from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock
import pytest
from fastapi.testclient import TestClient
from sqlmodel import create_engine
from alphaTrade.store.db import run_migrations
from alphaTrade.health import HealthState


def _engine(tmp_path):
    db = tmp_path / "t.db"
    run_migrations(db)
    return create_engine(f"sqlite:///{db}")


def _client(engine, scheduler):
    from alphaTrade.api.app import create_app
    return TestClient(create_app(engine, HealthState(), backtest_scheduler=scheduler))


def test_trigger_returns_run_id_and_queued(tmp_path):
    scheduler = MagicMock()
    scheduler.trigger = AsyncMock(return_value=42)
    client = _client(_engine(tmp_path), scheduler)
    resp = client.post("/api/v1/backtest/trigger", json={})
    assert resp.status_code == 202
    data = resp.json()
    assert data["run_id"] == 42
    assert data["status"] == "queued"


def test_trigger_with_explicit_dates(tmp_path):
    scheduler = MagicMock()
    scheduler.trigger = AsyncMock(return_value=7)
    client = _client(_engine(tmp_path), scheduler)
    resp = client.post("/api/v1/backtest/trigger", json={"start": "2025-01-01", "end": "2025-03-31"})
    assert resp.status_code == 202
    assert resp.json()["run_id"] == 7


def test_trigger_with_model_id(tmp_path):
    scheduler = MagicMock()
    scheduler.trigger = AsyncMock(return_value=3)
    client = _client(_engine(tmp_path), scheduler)
    resp = client.post("/api/v1/backtest/trigger", json={"model_id": "AAPL_v1"})
    assert resp.status_code == 202
    call_kwargs = scheduler.trigger.call_args.kwargs
    assert call_kwargs.get("model_filter") == "AAPL_v1"


def test_trigger_without_scheduler_returns_503(tmp_path):
    client = _client(_engine(tmp_path), None)
    resp = client.post("/api/v1/backtest/trigger", json={})
    assert resp.status_code == 503
