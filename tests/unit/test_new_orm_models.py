"""Verify new ORM models create + round-trip via SQLite."""
from datetime import datetime

import pytest
from sqlmodel import Session, select

from alphaTrade.store.db import get_engine
from alphaTrade.store.repos import (
    TradeJournal, PnlSnapshot, ModelPerformance,
    SectorCache, BacktestRun, TradeJournalRepo, PnlSnapshotRepo, ModelPerformanceRepo,
    SectorCacheRepo, BacktestRepo,
)


@pytest.fixture
def engine(tmp_path):
    import alphaTrade.store.db as _db
    _db._engine = None
    eng = get_engine(tmp_path / "test.db")
    yield eng
    _db._engine = None


def test_trade_journal_round_trip(engine):
    now = datetime.utcnow()
    with Session(engine) as s:
        entry = TradeJournal(
            model_id="model_a", ticker="AAPL",
            entry_price=150.0, exit_price=160.0, quantity=10.0,
            entry_time=now, exit_time=now,
            exit_reason="SIGNAL_SELL",
            realized_pnl=100.0, pnl_pct=0.066,
        )
        s.add(entry)
        s.commit()
        s.refresh(entry)
    assert entry.id is not None
    assert entry.exit_reason == "SIGNAL_SELL"


def test_model_performance_defaults(engine):
    with Session(engine) as s:
        perf = ModelPerformance(model_id="model_x", last_updated=datetime.utcnow())
        s.add(perf)
        s.commit()
        s.refresh(perf)
    assert perf.retired is False
    assert perf.trade_count == 0
    assert perf.rolling_trades_json == []


def test_sector_cache_round_trip(engine):
    with Session(engine) as s:
        sc = SectorCache(yf_ticker="AAPL", sector="Technology")
        s.add(sc)
        s.commit()
        s.refresh(sc)
    assert sc.sector == "Technology"


def test_backtest_run_round_trip(engine):
    with Session(engine) as s:
        run = BacktestRun(start_date="2024-01-01", end_date="2024-12-31")
        s.add(run)
        s.commit()
        s.refresh(run)
    assert run.id is not None


def test_trade_journal_repo_save_and_query(engine):
    now = datetime.utcnow()
    with Session(engine) as s:
        repo = TradeJournalRepo(s)
        entry = repo.save(TradeJournal(
            model_id="model_a", ticker="AAPL",
            entry_price=100.0, exit_price=110.0, quantity=5.0,
            entry_time=now, exit_time=now,
            exit_reason="SIGNAL_SELL",
            realized_pnl=50.0, pnl_pct=0.1,
        ))
        assert entry.id is not None
        rows = repo.by_model("model_a")
        assert len(rows) == 1
        since_rows = repo.since(now)
        assert len(since_rows) == 1
        since_str_rows = repo.since("2000-01-01")
        assert len(since_str_rows) == 1


def test_model_performance_repo_get_or_create(engine):
    with Session(engine) as s:
        repo = ModelPerformanceRepo(s)
        perf = repo.get_or_create("model_b")
        assert perf.model_id == "model_b"
        assert perf.retired is False
        perf2 = repo.get_or_create("model_b")
        assert perf2.id == perf.id


def test_model_performance_repo_is_retired(engine):
    with Session(engine) as s:
        repo = ModelPerformanceRepo(s)
        assert repo.is_retired("model_c") is False
        perf = repo.get_or_create("model_c")
        perf.retired = True
        repo.update(perf)
    with Session(engine) as s:
        repo = ModelPerformanceRepo(s)
        assert repo.is_retired("model_c") is True


def test_sector_cache_repo_put_and_get(engine):
    with Session(engine) as s:
        repo = SectorCacheRepo(s)
        repo.put("MSFT", "Technology")
        row = repo.get("MSFT")
        assert row is not None
        assert row.sector == "Technology"
        repo.put("MSFT", "Software")
    with Session(engine) as s:
        repo = SectorCacheRepo(s)
        assert repo.get("MSFT").sector == "Software"


def test_pnl_snapshot_repo_upsert(engine):
    with Session(engine) as s:
        repo = PnlSnapshotRepo(s)
        repo.upsert(PnlSnapshot(
            date="2026-05-11",
            total_equity=10500.0, day_pnl=500.0, day_pnl_pct=0.05,
            realized_pnl=300.0, unrealized_pnl=200.0,
        ))
        repo.upsert(PnlSnapshot(
            date="2026-05-11",
            total_equity=10600.0, day_pnl=600.0, day_pnl_pct=0.06,
            realized_pnl=400.0, unrealized_pnl=200.0,
        ))
    with Session(engine) as s:
        rows = list(s.exec(select(PnlSnapshot)).all())
    assert len(rows) == 1
    assert rows[0].total_equity == 10600.0


def test_backtest_repo_create_run_and_record_trade(engine):
    now = datetime.utcnow()
    with Session(engine) as s:
        repo = BacktestRepo(s)
        run_id = repo.create_run(start="2024-01-01", end="2024-12-31")
        assert isinstance(run_id, int)
        repo.record_trade(
            run_id=run_id,
            model_id="model_a",
            side="BUY",
            entry_bar=10, exit_bar=15,
            entry_time=now, exit_time=now,
            entry_price=150.0, exit_price=160.0,
            quantity=10.0, exit_reason="OCO_TP",
            realized_pnl=100.0,
        )
        trades = repo.trades_for_run(run_id)
    assert len(trades) == 1
    assert trades[0].exit_reason == "OCO_TP"
