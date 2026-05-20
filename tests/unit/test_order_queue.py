# tests/unit/test_order_queue.py
"""Tests for OrderRequest, OrderResult, and order_priority."""
from __future__ import annotations
from datetime import datetime
import pytest
from alphaTrade.broker.order_queue import OrderRequest, OrderResult, order_priority


def test_sell_always_priority_zero():
    assert order_priority("SELL", "1m") == 0
    assert order_priority("SELL", "1d") == 0


def test_buy_priority_by_interval_short_first():
    p_1m = order_priority("BUY", "1m")
    p_5m = order_priority("BUY", "5m")
    p_1h = order_priority("BUY", "1h")
    p_1d = order_priority("BUY", "1d")
    assert p_1m < p_5m < p_1h < p_1d


def test_sell_beats_all_buys():
    sell_p = order_priority("SELL", "1m")
    buy_p = order_priority("BUY", "1m")
    assert sell_p < buy_p


def test_unknown_interval_falls_back_to_middle():
    p = order_priority("BUY", "3m")
    assert 1 <= p <= 8


def _make_request(ticker="AAPL", side="BUY", interval="1m") -> OrderRequest:
    return OrderRequest(
        t212_ticker=ticker,
        side=side,
        quantity=1.0,
        client_order_id="cid-001",
        interval=interval,
        signal_ts=datetime.utcnow(),
        yf_ticker=ticker,
        stop_loss_pct=0.02,
        take_profit_pct=0.05,
        entry_price=100.0,
        run_name="model-a",
        bar_close_iso="2026-01-01T09:00:00Z",
    )


def test_order_request_fields():
    req = _make_request()
    assert req.t212_ticker == "AAPL"
    assert req.side == "BUY"


def test_order_result_default_fields():
    req = _make_request()
    result = OrderResult(request=req, status="filled")
    assert result.fill_price is None
    assert result.stop_order_id == ""
    assert result.error == ""
