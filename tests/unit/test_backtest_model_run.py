"""Tests for BacktestModelRun ORM model and BacktestRepo methods."""
from __future__ import annotations
import pytest
from sqlmodel import create_engine, Session
from alphaTrade.store.db import run_migrations
from alphaTrade.store.repos import BacktestRepo, BacktestModelRun


@pytest.fixture()
def session(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(db)
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as s:
        yield s


def test_record_model_run_creates_row(session):
    repo = BacktestRepo(session)
    run_id = repo.create_run(start="2026-04-01", end="2026-05-01")
    repo.record_model_run(
        run_id=run_id,
        model_id="AAPL_Transformer_test1",
        ticker="AAPL",
        interval="1d",
        trade_count=0,
        status="ran",
        error_msg="",
    )
    rows = repo.model_runs_for_run(run_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.model_id == "AAPL_Transformer_test1"
    assert row.ticker == "AAPL"
    assert row.interval == "1d"
    assert row.trade_count == 0
    assert row.status == "ran"
    assert row.error_msg == ""


def test_record_model_run_multiple_models(session):
    repo = BacktestRepo(session)
    run_id = repo.create_run(start="2026-04-01", end="2026-05-01")
    repo.record_model_run(run_id=run_id, model_id="m1", ticker="AAPL", interval="1d", trade_count=5, status="ran", error_msg="")
    repo.record_model_run(run_id=run_id, model_id="m2", ticker="AAPL", interval="1m", trade_count=0, status="no_data", error_msg="not enough bars")
    rows = repo.model_runs_for_run(run_id)
    assert len(rows) == 2
    statuses = {r.model_id: r.status for r in rows}
    assert statuses["m1"] == "ran"
    assert statuses["m2"] == "no_data"


def test_model_runs_for_run_empty(session):
    repo = BacktestRepo(session)
    run_id = repo.create_run(start="2026-04-01", end="2026-05-01")
    rows = repo.model_runs_for_run(run_id)
    assert rows == []


def test_model_runs_scoped_to_run(session):
    repo = BacktestRepo(session)
    run_id_a = repo.create_run(start="2026-04-01", end="2026-05-01")
    run_id_b = repo.create_run(start="2026-04-01", end="2026-05-01")
    repo.record_model_run(run_id=run_id_a, model_id="m1", ticker="AAPL", interval="1d", trade_count=1, status="ran", error_msg="")
    repo.record_model_run(run_id=run_id_b, model_id="m2", ticker="AAPL", interval="1m", trade_count=2, status="ran", error_msg="")
    assert len(repo.model_runs_for_run(run_id_a)) == 1
    assert len(repo.model_runs_for_run(run_id_b)) == 1


# Engine integration tests
from unittest.mock import MagicMock, patch
from pathlib import Path
import pandas as pd
from alphaTrade.backtest.engine import run_backtest
from alphaTrade.config import BacktestConfig
from sqlalchemy import create_engine as create_engine_sa


def _make_manifest(run_name="m1", ticker="AAPL", interval="1d", window=3):
    m = MagicMock()
    m.run_name = run_name
    m.ticker = ticker
    m.interval = interval
    m.window = window
    m.feature_names = []
    return m


def _make_df(n=20):
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "Open": [100.0] * n,
        "High": [105.0] * n,
        "Low": [95.0] * n,
        "Close": [102.0] * n,
        "Volume": [1000] * n,
    }, index=idx)


def test_run_backtest_records_model_run_zero_trades(tmp_path):
    """Engine records a BacktestModelRun row even when model produces 0 trades."""
    db = tmp_path / "test.db"
    run_migrations(db)
    engine_db = create_engine(f"sqlite:///{db}")

    manifest = _make_manifest()
    mock_model = MagicMock()
    cfg = BacktestConfig()

    provider = MagicMock()
    provider.fetch_ohlcv_range.return_value = _make_df(20)

    with patch("alphaTrade.backtest.engine.scan_models", return_value=[(manifest, mock_model)]):
        with patch("alphaTrade.backtest.engine._infer", return_value="HOLD"):
            with Session(engine_db) as session:
                run_backtest(
                    session=session,
                    models_dir=Path("/fake"),
                    start="2026-01-10",
                    end="2026-01-20",
                    cfg=cfg,
                    provider=provider,
                )

    with Session(engine_db) as session:
        repo = BacktestRepo(session)
        runs = repo.list_runs()
        assert len(runs) == 1
        model_runs = repo.model_runs_for_run(runs[0].id)
        assert len(model_runs) == 1
        mr = model_runs[0]
        assert mr.model_id == "m1"
        assert mr.ticker == "AAPL"
        assert mr.interval == "1d"
        assert mr.trade_count == 0
        assert mr.status == "ran"


def test_run_backtest_records_no_data_status(tmp_path):
    """Engine records no_data status when provider returns None."""
    db = tmp_path / "test.db"
    run_migrations(db)
    engine_db = create_engine(f"sqlite:///{db}")

    manifest = _make_manifest()
    mock_model = MagicMock()
    cfg = BacktestConfig()
    provider = MagicMock()
    provider.fetch_ohlcv_range.return_value = None  # simulate no data

    with patch("alphaTrade.backtest.engine.scan_models", return_value=[(manifest, mock_model)]):
        with Session(engine_db) as session:
            run_backtest(
                session=session,
                models_dir=Path("/fake"),
                start="2026-01-10",
                end="2026-01-20",
                cfg=cfg,
                provider=provider,
            )

    with Session(engine_db) as session:
        repo = BacktestRepo(session)
        runs = repo.list_runs()
        model_runs = repo.model_runs_for_run(runs[0].id)
        assert len(model_runs) == 1
        assert model_runs[0].status == "no_data"
        assert model_runs[0].trade_count == 0
