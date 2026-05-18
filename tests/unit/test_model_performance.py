"""Tests for rolling model performance tracking and retirement logic."""
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock
import asyncio

import pytest
from sqlmodel import Session

from alphaTrade.config import ModelRetirementConfig
from alphaTrade.risk.performance import record_trade, check_retirement
from alphaTrade.store.db import get_engine
from alphaTrade.store.repos import ModelPerformanceRepo


@pytest.fixture
def engine(tmp_path):
    import alphaTrade.store.db as _db
    _db._engine = None
    eng = get_engine(tmp_path / "test.db")
    yield eng
    _db._engine = None


def test_record_trade_increments_counts(engine):
    cfg = ModelRetirementConfig(enabled=True, lookback_trades=5)
    with Session(engine) as s:
        record_trade(s, model_id="model_a", realized_pnl=50.0, cfg=cfg)
    with Session(engine) as s:
        perf = ModelPerformanceRepo(s).get_or_create("model_a")
    assert perf.trade_count == 1
    assert perf.win_count == 1
    assert perf.rolling_pnl == 50.0


def test_record_trade_loss(engine):
    cfg = ModelRetirementConfig(enabled=True, lookback_trades=5)
    with Session(engine) as s:
        record_trade(s, model_id="model_a", realized_pnl=-30.0, cfg=cfg)
    with Session(engine) as s:
        perf = ModelPerformanceRepo(s).get_or_create("model_a")
    assert perf.win_count == 0
    assert perf.rolling_pnl == -30.0


def test_rolling_window_trims_to_lookback(engine):
    cfg = ModelRetirementConfig(enabled=True, lookback_trades=3)
    with Session(engine) as s:
        for pnl in [10.0, 20.0, 30.0, 40.0]:
            record_trade(s, model_id="model_a", realized_pnl=pnl, cfg=cfg)
    with Session(engine) as s:
        perf = ModelPerformanceRepo(s).get_or_create("model_a")
        trades = json.loads(perf.rolling_trades_json)
    assert len(trades) == 3
    assert trades == [20.0, 30.0, 40.0]


def test_check_retirement_below_win_rate(engine):
    cfg = ModelRetirementConfig(enabled=True, lookback_trades=5, min_win_rate=0.6, min_rolling_pnl=-9999)
    with Session(engine) as s:
        for pnl in [-10.0, -20.0, 10.0, -5.0, -8.0]:
            record_trade(s, model_id="model_a", realized_pnl=pnl, cfg=cfg)
    with Session(engine) as s:
        retired = check_retirement(s, model_id="model_a", cfg=cfg)
    assert retired is True


def test_check_retirement_below_rolling_pnl(engine):
    cfg = ModelRetirementConfig(enabled=True, lookback_trades=3, min_win_rate=0.0, min_rolling_pnl=-50.0)
    with Session(engine) as s:
        for pnl in [-20.0, -20.0, -20.0]:
            record_trade(s, model_id="model_a", realized_pnl=pnl, cfg=cfg)
    with Session(engine) as s:
        retired = check_retirement(s, model_id="model_a", cfg=cfg)
    assert retired is True


def test_check_retirement_not_triggered_when_disabled(engine):
    cfg = ModelRetirementConfig(enabled=False)
    with Session(engine) as s:
        for pnl in [-100.0] * 10:
            record_trade(s, model_id="model_a", realized_pnl=pnl, cfg=cfg)
    with Session(engine) as s:
        retired = check_retirement(s, model_id="model_a", cfg=cfg)
    assert retired is False


def test_check_retirement_not_triggered_when_not_enough_trades(engine):
    cfg = ModelRetirementConfig(enabled=True, lookback_trades=10, min_win_rate=0.9)
    with Session(engine) as s:
        record_trade(s, model_id="model_a", realized_pnl=-50.0, cfg=cfg)
    with Session(engine) as s:
        retired = check_retirement(s, model_id="model_a", cfg=cfg)
    assert retired is False


def test_registry_skips_retired_model(engine, tmp_path):
    manifest_a = MagicMock(); manifest_a.run_name = "model_a"; manifest_a.interval = "1d"
    model_a = MagicMock()
    manifest_b = MagicMock(); manifest_b.run_name = "model_b"; manifest_b.interval = "1d"
    model_b = MagicMock()

    with Session(engine) as s:
        repo = ModelPerformanceRepo(s)
        perf = repo.get_or_create("model_a")
        perf.retired = True
        repo.update(perf)

    with patch("alphaTrade.model_registry.scan_models", return_value=[(manifest_a, model_a), (manifest_b, model_b)]):
        from alphaTrade.model_registry import ModelRegistry
        registry = ModelRegistry(engine=engine)
        asyncio.run(registry.refresh(Path("/fake"), {}))

    assert "model_a" not in registry.by_run_name
    assert "model_b" in registry.by_run_name


def test_first_trade_at_set_on_first_trade(engine):
    cfg = ModelRetirementConfig(enabled=False)
    with Session(engine) as s:
        record_trade(s, model_id="model_x", realized_pnl=10.0, cfg=cfg)
        perf = ModelPerformanceRepo(s).get_or_create("model_x")
        assert perf.first_trade_at is not None

def test_first_trade_at_not_updated_on_subsequent_trades(engine):
    cfg = ModelRetirementConfig(enabled=False)
    with Session(engine) as s:
        record_trade(s, model_id="model_x", realized_pnl=10.0, cfg=cfg)
        perf = ModelPerformanceRepo(s).get_or_create("model_x")
        first = perf.first_trade_at
    with Session(engine) as s:
        record_trade(s, model_id="model_x", realized_pnl=20.0, cfg=cfg)
        perf = ModelPerformanceRepo(s).get_or_create("model_x")
        assert perf.first_trade_at == first
