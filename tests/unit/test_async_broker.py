# tests/unit/test_async_broker.py
"""Tests for AsyncBroker queue management."""
from __future__ import annotations
import asyncio
import itertools
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from alphaTrade.broker.order_queue import OrderRequest, OrderResult, order_priority
from alphaTrade.broker.throttle import EndpointThrottle
from alphaTrade.broker.async_broker import AsyncBroker


def _throttle() -> EndpointThrottle:
    return EndpointThrottle({})  # zero gaps for tests


def _t212() -> MagicMock:
    t212 = MagicMock()
    t212.place_market_order.return_value = {"id": "ord-1", "fillPrice": 101.0}
    t212.place_stop_order.return_value = {"id": "stop-1"}
    t212.place_limit_order.return_value = {"id": "limit-1"}
    return t212


def _make_request(
    ticker="AAPL",
    side="BUY",
    interval="1m",
    signal_ts=None,
    bar_close_iso="2026-01-01T09:00:00Z",
) -> OrderRequest:
    return OrderRequest(
        t212_ticker=ticker,
        side=side,
        quantity=1.0,
        client_order_id=f"cid-{ticker}-{side}",
        interval=interval,
        signal_ts=signal_ts or datetime.utcnow(),
        yf_ticker=ticker,
        stop_loss_pct=0.02,
        take_profit_pct=0.05,
        entry_price=100.0,
        run_name="model-a",
        bar_close_iso=bar_close_iso,
    )


def test_enqueue_adds_to_queue():
    broker = AsyncBroker(t212=_t212(), throttle=_throttle())
    broker.enqueue(_make_request())
    assert broker._queue.qsize() == 1


def test_enqueue_dedup_same_bar_same_ticker_side():
    broker = AsyncBroker(t212=_t212(), throttle=_throttle())
    req1 = _make_request(bar_close_iso="2026-01-01T09:00:00Z")
    req2 = _make_request(bar_close_iso="2026-01-01T09:00:00Z")
    broker.enqueue(req1)
    broker.enqueue(req2)
    assert broker._queue.qsize() == 1  # second dropped


def test_enqueue_allows_different_bar_close():
    broker = AsyncBroker(t212=_t212(), throttle=_throttle())
    req1 = _make_request(bar_close_iso="2026-01-01T09:00:00Z")
    req2 = _make_request(bar_close_iso="2026-01-01T09:01:00Z")
    broker.enqueue(req1)
    broker.enqueue(req2)
    assert broker._queue.qsize() == 2


def test_enqueue_respects_max_queue_depth_evicts_lowest_priority():
    broker = AsyncBroker(t212=_t212(), throttle=_throttle(), max_queue_depth=2)
    # Fill queue with low-priority orders (1d BUYs)
    broker.enqueue(_make_request(ticker="AAA", interval="1d", bar_close_iso="2026-01-01T09:00:00Z"))
    broker.enqueue(_make_request(ticker="BBB", interval="1d", bar_close_iso="2026-01-01T09:00:00Z"))
    assert broker._queue.qsize() == 2
    # Now enqueue a high-priority SELL — should evict a 1d BUY
    broker.enqueue(_make_request(ticker="CCC", side="SELL", interval="1d", bar_close_iso="2026-01-01T09:00:00Z"))
    assert broker._queue.qsize() == 2  # still 2, one 1d evicted


@pytest.mark.asyncio
async def test_stale_order_dropped_by_drain():
    broker = AsyncBroker(t212=_t212(), throttle=_throttle(), stale_window_multiplier=0.5)
    callback_results = []

    async def cb(result: OrderResult) -> None:
        callback_results.append(result)

    broker.set_callback(cb)

    stale_ts = datetime.utcnow() - timedelta(seconds=200)  # 1m stale window = 30s; 200s >> 30s
    req = _make_request(signal_ts=stale_ts, interval="1m")
    broker.enqueue(req)

    await broker.start_drain()
    await asyncio.sleep(0.1)
    await broker.stop_drain()

    assert len(callback_results) == 1
    assert callback_results[0].status == "stale_dropped"


@pytest.mark.asyncio
async def test_sell_drains_before_buy():
    """SELL order must be processed before BUY even if BUY was enqueued first."""
    t212 = _t212()
    broker = AsyncBroker(t212=t212, throttle=_throttle())
    processed_order: list[str] = []

    async def cb(result: OrderResult) -> None:
        processed_order.append(result.request.side)

    broker.set_callback(cb)

    buy_req = _make_request(ticker="AAPL", side="BUY", interval="1m", bar_close_iso="2026-01-01T09:00:00Z")
    sell_req = _make_request(ticker="TSLA", side="SELL", interval="1m", bar_close_iso="2026-01-01T09:00:00Z")

    broker.enqueue(buy_req)
    broker.enqueue(sell_req)

    await broker.start_drain()
    await asyncio.sleep(0.2)
    await broker.stop_drain()

    assert processed_order[0] == "SELL"
    assert processed_order[1] == "BUY"


@pytest.mark.asyncio
async def test_filled_order_triggers_callback():
    t212 = _t212()
    broker = AsyncBroker(t212=t212, throttle=_throttle())
    results: list[OrderResult] = []

    async def cb(result: OrderResult) -> None:
        results.append(result)

    broker.set_callback(cb)
    broker.enqueue(_make_request())

    await broker.start_drain()
    await asyncio.sleep(0.2)
    await broker.stop_drain()

    assert len(results) == 1
    assert results[0].status == "filled"
    assert results[0].fill_price == 101.0


@pytest.mark.asyncio
async def test_buy_places_oco_orders():
    t212 = _t212()
    broker = AsyncBroker(t212=t212, throttle=_throttle())
    results: list[OrderResult] = []

    async def cb(result: OrderResult) -> None:
        results.append(result)

    broker.set_callback(cb)
    broker.enqueue(_make_request(side="BUY"))

    await broker.start_drain()
    await asyncio.sleep(0.2)
    await broker.stop_drain()

    t212.place_stop_order.assert_called_once()
    t212.place_limit_order.assert_called_once()
    assert results[0].stop_order_id == "stop-1"
    assert results[0].limit_order_id == "limit-1"
