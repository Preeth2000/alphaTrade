"""Verify trade journal entries written on position close."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session, select

from alphaTrade.broker.oco_monitor import monitor_oco
from alphaTrade.store.db import get_engine
from alphaTrade.store.repos import TradeJournal
from alphaTrade.main import build_sell_journal_entry
from alphaTrade.store.repos import Position


@pytest.fixture
def engine(tmp_path):
    import alphaTrade.store.db as _db
    _db._engine = None
    eng = get_engine(tmp_path / "test.db")
    yield eng
    _db._engine = None


def make_t212(stop_status: str, limit_status: str, fill_price: float = 155.0):
    t212 = MagicMock()
    t212.get_order.side_effect = lambda order_id: {
        "status": stop_status if order_id == "stop-1" else limit_status,
        "fillPrice": fill_price,
    }
    t212.cancel_order.return_value = {}
    return t212


async def test_oco_sl_writes_trade_journal(engine):
    t212 = make_t212(stop_status="FILLED", limit_status="WORKING", fill_price=145.0)
    entry_time = datetime.utcnow() - timedelta(hours=2)

    await monitor_oco(
        t212=t212,
        t212_ticker="AAPL_US_EQ",
        stop_order_id="stop-1",
        limit_order_id="limit-1",
        engine=engine,
        cooldown_td=timedelta(hours=1),
        entry_price=150.0,
        sl_price=145.0,
        tp_price=160.0,
        quantity=10.0,
        model_id="model_a",
        entry_time=entry_time,
        poll_interval_s=0.0,
    )

    with Session(engine) as s:
        entries = list(s.exec(select(TradeJournal)).all())

    assert len(entries) == 1
    assert entries[0].exit_reason == "OCO_SL"
    assert entries[0].ticker == "AAPL_US_EQ"
    assert entries[0].model_id == "model_a"
    assert entries[0].entry_price == 150.0
    assert entries[0].realized_pnl < 0


async def test_oco_tp_writes_trade_journal(engine):
    t212 = make_t212(stop_status="WORKING", limit_status="FILLED", fill_price=160.0)

    await monitor_oco(
        t212=t212,
        t212_ticker="AAPL_US_EQ",
        stop_order_id="stop-1",
        limit_order_id="limit-1",
        engine=engine,
        cooldown_td=timedelta(hours=1),
        entry_price=150.0,
        sl_price=145.0,
        tp_price=160.0,
        quantity=10.0,
        model_id="model_a",
        entry_time=datetime.utcnow(),
        poll_interval_s=0.0,
    )

    with Session(engine) as s:
        entries = list(s.exec(select(TradeJournal)).all())

    assert entries[0].exit_reason == "OCO_TP"
    assert entries[0].realized_pnl > 0


def test_build_sell_journal_entry():
    now = datetime.utcnow()
    pos = Position(
        t212_ticker="AAPL_US_EQ",
        quantity=10.0,
        avg_entry=150.0,
        opened_at=now - timedelta(hours=3),
    )
    entry = build_sell_journal_entry(
        model_id="model_a",
        t212_ticker="AAPL_US_EQ",
        exit_price=160.0,
        quantity=10.0,
        position=pos,
    )
    assert entry.exit_reason == "SIGNAL_SELL"
    assert entry.entry_price == 150.0
    assert entry.exit_price == 160.0
    assert entry.realized_pnl == pytest.approx(100.0)
    assert entry.pnl_pct == pytest.approx(0.0667, rel=1e-2)
