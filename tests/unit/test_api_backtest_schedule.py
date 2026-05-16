from __future__ import annotations
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from sqlmodel import create_engine
from alphaTrade.store.db import run_migrations
from alphaTrade.health import HealthState


def _engine(tmp_path):
    db = tmp_path / "t.db"
    run_migrations(db)
    return create_engine(f"sqlite:///{db}")


def _scheduler(schedule_enabled=True, cron="0 2 * * *", lookback_days=30, jobs=None):
    s = MagicMock()
    s.get_status.return_value = {
        "schedule_enabled": schedule_enabled,
        "cron": cron,
        "lookback_days": lookback_days,
        "jobs": jobs or [],
    }
    s.get_model_status.return_value = {
        "model_id": "AAPL_v1",
        "disabled": False,
        "effective_cron": cron,
        "effective_lookback_days": lookback_days,
        "next_run_time": None,
    }
    return s


def _client(engine, scheduler):
    from alphaTrade.api.app import create_app
    return TestClient(create_app(engine, HealthState(), backtest_scheduler=scheduler))


def test_get_schedule_returns_status(tmp_path):
    sched = _scheduler(jobs=[{"id": "backtest_AAPL_v1", "next_run_time": None}])
    resp = _client(_engine(tmp_path), sched).get("/api/v1/backtest/schedule")
    assert resp.status_code == 200
    data = resp.json()
    assert data["schedule_enabled"] is True
    assert len(data["jobs"]) == 1


def test_patch_schedule_calls_update_global(tmp_path):
    sched = _scheduler()
    client = _client(_engine(tmp_path), sched)
    resp = client.patch("/api/v1/backtest/schedule", json={"schedule_enabled": False})
    assert resp.status_code == 200
    sched.update_global.assert_called_once_with(
        schedule_enabled=False, cron=None, lookback_days=None
    )


def test_patch_schedule_invalid_cron_returns_422(tmp_path):
    sched = _scheduler()
    client = _client(_engine(tmp_path), sched)
    resp = client.patch("/api/v1/backtest/schedule", json={"cron": "not-a-cron"})
    assert resp.status_code == 422


def test_get_model_schedule(tmp_path):
    sched = _scheduler()
    resp = _client(_engine(tmp_path), sched).get("/api/v1/backtest/schedule/AAPL_v1")
    assert resp.status_code == 200
    assert resp.json()["model_id"] == "AAPL_v1"


def test_patch_model_schedule_calls_update_model(tmp_path):
    sched = _scheduler()
    client = _client(_engine(tmp_path), sched)
    resp = client.patch("/api/v1/backtest/schedule/AAPL_v1", json={"disabled": True})
    assert resp.status_code == 200
    sched.update_model.assert_called_once_with(
        "AAPL_v1", disabled=True, cron=None, lookback_days=None
    )


def test_get_schedule_no_scheduler_returns_503(tmp_path):
    client = _client(_engine(tmp_path), None)
    resp = client.get("/api/v1/backtest/schedule")
    assert resp.status_code == 503
