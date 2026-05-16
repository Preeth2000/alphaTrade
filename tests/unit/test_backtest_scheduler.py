from __future__ import annotations
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from sqlmodel import create_engine
from alphaTrade.store.db import run_migrations
from alphaTrade.config import BacktestConfig, ModelOverride, BacktestScheduleOverride


def _engine(tmp_path):
    db = tmp_path / "t.db"
    run_migrations(db)
    return create_engine(f"sqlite:///{db}")


def _settings(tmp_path, **backtest_kwargs):
    from alphaTrade.config import Settings
    cfg = BacktestConfig(**backtest_kwargs)
    s = MagicMock(spec=Settings)
    s.backtest = cfg
    s.model_overrides = {}
    s.overrides_path = tmp_path / "overrides.yaml"
    return s


@pytest.mark.asyncio
async def test_scheduler_adds_job_per_model(tmp_path):
    from alphaTrade.scheduler.backtest_scheduler import BacktestScheduler
    engine = _engine(tmp_path)
    settings = _settings(tmp_path, schedule_enabled=True, cron="0 2 * * *", lookback_days=30)
    manifest = MagicMock(); manifest.run_name = "AAPL_v1"
    sched = BacktestScheduler(engine=engine, settings=settings, models=[(manifest, MagicMock())], models_dir=Path("models"))
    sched.start()
    try:
        jobs = sched.get_status()["jobs"]
        assert any(j["id"] == "backtest_AAPL_v1" for j in jobs)
    finally:
        sched.shutdown()


@pytest.mark.asyncio
async def test_scheduler_skips_disabled_model(tmp_path):
    from alphaTrade.scheduler.backtest_scheduler import BacktestScheduler
    engine = _engine(tmp_path)
    settings = _settings(tmp_path, schedule_enabled=True, cron="0 2 * * *", lookback_days=30)
    settings.model_overrides = {"AAPL_v1": ModelOverride(backtest=BacktestScheduleOverride(disabled=True))}
    manifest = MagicMock(); manifest.run_name = "AAPL_v1"
    sched = BacktestScheduler(engine=engine, settings=settings, models=[(manifest, MagicMock())], models_dir=Path("models"))
    sched.start()
    try:
        jobs = sched.get_status()["jobs"]
        assert not any(j["id"] == "backtest_AAPL_v1" for j in jobs)
    finally:
        sched.shutdown()


@pytest.mark.asyncio
async def test_scheduler_disabled_globally_starts_no_jobs(tmp_path):
    from alphaTrade.scheduler.backtest_scheduler import BacktestScheduler
    engine = _engine(tmp_path)
    settings = _settings(tmp_path, schedule_enabled=False, cron="0 2 * * *", lookback_days=30)
    manifest = MagicMock(); manifest.run_name = "AAPL_v1"
    sched = BacktestScheduler(engine=engine, settings=settings, models=[(manifest, MagicMock())], models_dir=Path("models"))
    sched.start()
    try:
        assert sched.get_status()["schedule_enabled"] is False
        assert sched.get_status()["jobs"] == []
    finally:
        sched.shutdown()


@pytest.mark.asyncio
async def test_update_global_reschedules_jobs(tmp_path):
    from alphaTrade.scheduler.backtest_scheduler import BacktestScheduler
    engine = _engine(tmp_path)
    settings = _settings(tmp_path, schedule_enabled=True, cron="0 2 * * *", lookback_days=30)
    manifest = MagicMock(); manifest.run_name = "AAPL_v1"
    sched = BacktestScheduler(engine=engine, settings=settings, models=[(manifest, MagicMock())], models_dir=Path("models"))
    sched.start()
    try:
        sched.update_global(schedule_enabled=True, cron="0 6 * * *", lookback_days=None)
        assert sched._settings.backtest.cron == "0 6 * * *"
        jobs = sched.get_status()["jobs"]
        assert any(j["id"] == "backtest_AAPL_v1" for j in jobs)
    finally:
        sched.shutdown()


@pytest.mark.asyncio
async def test_update_global_disable_removes_all_jobs(tmp_path):
    from alphaTrade.scheduler.backtest_scheduler import BacktestScheduler
    engine = _engine(tmp_path)
    settings = _settings(tmp_path, schedule_enabled=True, cron="0 2 * * *", lookback_days=30)
    manifest = MagicMock(); manifest.run_name = "AAPL_v1"
    sched = BacktestScheduler(engine=engine, settings=settings, models=[(manifest, MagicMock())], models_dir=Path("models"))
    sched.start()
    try:
        sched.update_global(schedule_enabled=False, cron=None, lookback_days=None)
        assert sched.get_status()["jobs"] == []
    finally:
        sched.shutdown()


@pytest.mark.asyncio
async def test_update_model_disable_removes_job(tmp_path):
    from alphaTrade.scheduler.backtest_scheduler import BacktestScheduler
    engine = _engine(tmp_path)
    settings = _settings(tmp_path, schedule_enabled=True, cron="0 2 * * *", lookback_days=30)
    manifest = MagicMock(); manifest.run_name = "AAPL_v1"
    sched = BacktestScheduler(engine=engine, settings=settings, models=[(manifest, MagicMock())], models_dir=Path("models"))
    sched.start()
    try:
        sched.update_model("AAPL_v1", disabled=True, cron=None, lookback_days=None)
        jobs = sched.get_status()["jobs"]
        assert not any(j["id"] == "backtest_AAPL_v1" for j in jobs)
    finally:
        sched.shutdown()
