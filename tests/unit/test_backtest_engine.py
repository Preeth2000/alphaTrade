"""Unit tests for backtest engine core logic and reporter."""
import math
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

from alphaTrade.backtest.engine import _simulate_fill, _check_sl_tp, BacktestState


def test_simulate_fill_buy_adds_slippage():
    """Fill price for BUY = open * (1 + slippage_bps/10000)."""
    price = _simulate_fill("BUY", open_price=100.0, slippage_bps=10)
    assert abs(price - 100.10) < 1e-9


def test_simulate_fill_sell_subtracts_slippage():
    price = _simulate_fill("SELL", open_price=100.0, slippage_bps=10)
    assert abs(price - 99.90) < 1e-9


def test_check_sl_tp_no_position():
    """No position → nothing triggered."""
    result = _check_sl_tp(state=None, high=110.0, low=90.0)
    assert result is None


def test_check_sl_tp_sl_hit():
    state = BacktestState(
        side="BUY", entry_price=100.0, quantity=10.0,
        sl_price=95.0, tp_price=115.0, entry_bar=0, entry_time=pd.Timestamp("2024-01-01"), model_id="m1"
    )
    result = _check_sl_tp(state=state, high=110.0, low=93.0)
    assert result == ("SL", 95.0)


def test_check_sl_tp_tp_hit():
    state = BacktestState(
        side="BUY", entry_price=100.0, quantity=10.0,
        sl_price=95.0, tp_price=115.0, entry_bar=0, entry_time=pd.Timestamp("2024-01-01"), model_id="m1"
    )
    result = _check_sl_tp(state=state, high=116.0, low=98.0)
    assert result == ("TP", 115.0)


def test_check_sl_tp_no_hit():
    state = BacktestState(
        side="BUY", entry_price=100.0, quantity=10.0,
        sl_price=95.0, tp_price=115.0, entry_bar=0, entry_time=pd.Timestamp("2024-01-01"), model_id="m1"
    )
    result = _check_sl_tp(state=state, high=110.0, low=98.0)
    assert result is None


def test_backtest_state_pnl_long():
    state = BacktestState(
        side="BUY", entry_price=100.0, quantity=5.0,
        sl_price=95.0, tp_price=115.0, entry_bar=0, entry_time=pd.Timestamp("2024-01-01"), model_id="m1"
    )
    assert state.pnl(exit_price=110.0) == pytest.approx(50.0)


def test_backtest_state_pnl_short():
    state = BacktestState(
        side="SELL", entry_price=100.0, quantity=5.0,
        sl_price=105.0, tp_price=85.0, entry_bar=0, entry_time=pd.Timestamp("2024-01-01"), model_id="m1"
    )
    assert state.pnl(exit_price=90.0) == pytest.approx(50.0)


# Reporter tests
from alphaTrade.backtest.reporter import compute_summary, format_text


def test_compute_summary_empty():
    s = compute_summary([], initial_equity=10_000)
    assert s["total_trades"] == 0
    assert s["win_rate"] == 0.0


def test_compute_summary_basic():
    trades = [
        {"realized_pnl": 100.0, "model_id": "m1"},
        {"realized_pnl": -50.0, "model_id": "m1"},
        {"realized_pnl": 200.0, "model_id": "m2"},
    ]
    s = compute_summary(trades, initial_equity=10_000)
    assert s["total_trades"] == 3
    assert s["total_pnl"] == pytest.approx(250.0)
    assert s["win_rate"] == pytest.approx(2/3, rel=1e-3)
    assert "m1" in s["by_model"]
    assert "m2" in s["by_model"]


def test_compute_summary_max_drawdown():
    # Gains 100, then loses 200: peak=10100, trough=9900 → dd = 200/10100
    trades = [
        {"realized_pnl": 100.0, "model_id": "m1"},
        {"realized_pnl": -200.0, "model_id": "m1"},
    ]
    s = compute_summary(trades, initial_equity=10_000)
    expected_dd = 200 / 10_100 * 100
    assert abs(s["max_drawdown_pct"] - round(expected_dd, 3)) < 0.001


def test_format_text_contains_summary():
    trades = [{"realized_pnl": 50.0, "model_id": "m1"}]
    s = compute_summary(trades, initial_equity=10_000)
    text = format_text(s)
    assert "Total trades" in text
    assert "Win rate" in text
