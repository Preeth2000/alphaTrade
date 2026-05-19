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
