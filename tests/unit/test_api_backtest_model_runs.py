"""Tests for GET /backtest/runs/{run_id}/models endpoint."""
from __future__ import annotations
from fastapi.testclient import TestClient
from sqlmodel import create_engine, Session
from alphaTrade.store.db import run_migrations
from alphaTrade.store.repos import BacktestRepo
from alphaTrade.health import HealthState


def _engine(tmp_path):
    db = tmp_path / "t.db"
    run_migrations(db)
    return create_engine(f"sqlite:///{db}")


def _client(engine):
    from alphaTrade.api.app import create_app
    return TestClient(create_app(engine, HealthState(), backtest_scheduler=None))


def test_get_model_runs_empty(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        run_id = BacktestRepo(session).create_run(start="2026-04-01", end="2026-05-01")
    client = _client(engine)
    resp = client.get(f"/api/v1/backtest/runs/{run_id}/models", headers={"X-API-Key": ""})
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_model_runs_returns_rows(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        repo = BacktestRepo(session)
        run_id = repo.create_run(start="2026-04-01", end="2026-05-01")
        repo.record_model_run(run_id=run_id, model_id="AAPL_Transformer_test1", ticker="AAPL", interval="1d", trade_count=0, status="ran", error_msg="")
        repo.record_model_run(run_id=run_id, model_id="AAPL_Transformer_test2", ticker="AAPL", interval="1m", trade_count=3, status="ran", error_msg="")
    client = _client(engine)
    resp = client.get(f"/api/v1/backtest/runs/{run_id}/models", headers={"X-API-Key": ""})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    by_id = {r["model_id"]: r for r in data}
    assert by_id["AAPL_Transformer_test1"]["trade_count"] == 0
    assert by_id["AAPL_Transformer_test1"]["status"] == "ran"
    assert by_id["AAPL_Transformer_test2"]["trade_count"] == 3
    assert by_id["AAPL_Transformer_test2"]["interval"] == "1m"


def test_get_model_runs_404_for_missing_run(tmp_path):
    client = _client(_engine(tmp_path))
    resp = client.get("/api/v1/backtest/runs/9999/models", headers={"X-API-Key": ""})
    assert resp.status_code == 404


def test_get_model_runs_scoped_to_run(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        repo = BacktestRepo(session)
        run_a = repo.create_run(start="2026-04-01", end="2026-05-01")
        run_b = repo.create_run(start="2026-04-01", end="2026-05-01")
        repo.record_model_run(run_id=run_a, model_id="m1", ticker="AAPL", interval="1d", trade_count=1, status="ran", error_msg="")
        repo.record_model_run(run_id=run_b, model_id="m2", ticker="AAPL", interval="1m", trade_count=2, status="ran", error_msg="")
    client = _client(engine)
    resp_a = client.get(f"/api/v1/backtest/runs/{run_a}/models", headers={"X-API-Key": ""})
    assert len(resp_a.json()) == 1
    assert resp_a.json()[0]["model_id"] == "m1"
