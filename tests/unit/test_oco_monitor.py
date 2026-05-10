"""Unit tests for OCO monitor. Uses in-memory SQLite and mocked T212Client."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, SQLModel, create_engine

from alphalink.broker.oco_monitor import monitor_oco
from alphalink.store.repos import Position, PositionRepo


@pytest.fixture(autouse=True)
def _patch_wh():
    with patch("alphalink.broker.oco_monitor.wh"):
        yield


def _make_engine():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_position(engine, ticker: str = "AAPL_US_EQ", qty: float = 1.0) -> None:
    with Session(engine) as s:
        PositionRepo(s).upsert(Position(t212_ticker=ticker, quantity=qty, avg_entry=175.0))


def _make_t212(stop_statuses: list[str], limit_statuses: list[str]) -> MagicMock:
    """Return a mocked T212Client whose get_order iterates through provided statuses."""
    t212 = MagicMock()
    stop_iter = iter(stop_statuses)
    limit_iter = iter(limit_statuses)

    def _get_order(order_id: str) -> dict:
        if order_id == "stop-001":
            return {"status": next(stop_iter)}
        return {"status": next(limit_iter)}

    t212.get_order.side_effect = _get_order
    return t212


async def test_stop_fills_cancels_limit_and_closes_position() -> None:
    engine = _make_engine()
    _seed_position(engine)
    t212 = _make_t212(stop_statuses=["FILLED"], limit_statuses=["PENDING"])

    await monitor_oco(
        t212=t212,
        t212_ticker="AAPL_US_EQ",
        stop_order_id="stop-001",
        limit_order_id="limit-001",
        engine=engine,
        cooldown_td=timedelta(hours=1),
        poll_interval_s=0,
    )

    t212.cancel_order.assert_called_once_with("limit-001")

    with Session(engine) as s:
        pos = PositionRepo(s).get("AAPL_US_EQ")
    assert pos is not None
    assert pos.quantity == 0
    assert pos.cooldown_until_ts is not None


async def test_limit_fills_cancels_stop_and_closes_position() -> None:
    engine = _make_engine()
    _seed_position(engine)
    t212 = _make_t212(stop_statuses=["PENDING"], limit_statuses=["FILLED"])

    await monitor_oco(
        t212=t212,
        t212_ticker="AAPL_US_EQ",
        stop_order_id="stop-001",
        limit_order_id="limit-001",
        engine=engine,
        cooldown_td=timedelta(hours=1),
        poll_interval_s=0,
    )

    t212.cancel_order.assert_called_once_with("stop-001")

    with Session(engine) as s:
        pos = PositionRepo(s).get("AAPL_US_EQ")
    assert pos.quantity == 0


async def test_both_cancelled_externally_exits_loop() -> None:
    engine = _make_engine()
    _seed_position(engine)
    t212 = _make_t212(stop_statuses=["CANCELLED"], limit_statuses=["CANCELLED"])

    await monitor_oco(
        t212=t212,
        t212_ticker="AAPL_US_EQ",
        stop_order_id="stop-001",
        limit_order_id="limit-001",
        engine=engine,
        cooldown_td=timedelta(hours=1),
        poll_interval_s=0,
    )

    # When stop leg dies, limit leg gets cancelled as protective measure
    t212.cancel_order.assert_called_once_with("limit-001")

    # Position should still be open (no fill, so no close)
    with Session(engine) as s:
        pos = PositionRepo(s).get("AAPL_US_EQ")
    assert pos is not None
    assert pos.quantity == 1.0


async def test_poll_error_continues_loop_then_fills() -> None:
    """First poll raises, second poll returns FILLED — loop must not crash."""
    engine = _make_engine()
    _seed_position(engine)

    t212 = MagicMock()
    call_count = 0

    def _get_order(order_id: str) -> dict:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("network error")
        if order_id == "stop-001":
            return {"status": "FILLED"}
        return {"status": "PENDING"}

    t212.get_order.side_effect = _get_order

    await monitor_oco(
        t212=t212,
        t212_ticker="AAPL_US_EQ",
        stop_order_id="stop-001",
        limit_order_id="limit-001",
        engine=engine,
        cooldown_td=timedelta(hours=1),
        poll_interval_s=0,
    )

    with Session(engine) as s:
        pos = PositionRepo(s).get("AAPL_US_EQ")
    assert pos.quantity == 0


async def test_cancel_failure_still_closes_position() -> None:
    """Even if cancel_order raises, position must still be closed."""
    engine = _make_engine()
    _seed_position(engine)
    t212 = _make_t212(stop_statuses=["FILLED"], limit_statuses=["PENDING"])
    t212.cancel_order.side_effect = RuntimeError("cancel failed")

    await monitor_oco(
        t212=t212,
        t212_ticker="AAPL_US_EQ",
        stop_order_id="stop-001",
        limit_order_id="limit-001",
        engine=engine,
        cooldown_td=timedelta(hours=1),
        poll_interval_s=0,
    )

    with Session(engine) as s:
        pos = PositionRepo(s).get("AAPL_US_EQ")
    assert pos.quantity == 0


async def test_stop_cancelled_cancels_limit_leg() -> None:
    """One leg dies without filling — surviving leg gets cancelled, position stays open."""
    engine = _make_engine()
    _seed_position(engine)
    t212 = _make_t212(stop_statuses=["CANCELLED"], limit_statuses=["PENDING"])

    await monitor_oco(
        t212=t212,
        t212_ticker="AAPL_US_EQ",
        stop_order_id="stop-001",
        limit_order_id="limit-001",
        engine=engine,
        cooldown_td=timedelta(hours=1),
        poll_interval_s=0,
    )

    # Limit leg should be cancelled since stop died
    t212.cancel_order.assert_called_once_with("limit-001")

    # Position stays open (no fill happened)
    with Session(engine) as s:
        pos = PositionRepo(s).get("AAPL_US_EQ")
    assert pos is not None
    assert pos.quantity == 1.0
