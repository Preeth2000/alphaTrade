# tests/test_backtest_oco_lag.py
"""Tests for per-bar OCO lag simulation in backtest engine."""
from __future__ import annotations
import pytest
from alphaTrade.backtest.engine import _compute_protected_fraction, _check_sl_tp_with_lag


def test_protected_fraction_zero_lag():
    # No lag = full bar protection
    frac = _compute_protected_fraction(queue_position=0, stop_gap=2.0, limit_gap=2.0, bar_duration_secs=60)
    assert frac == pytest.approx(1.0)


def test_protected_fraction_partial_lag():
    # Position 1 = 4s lag in 60s bar = 56/60 ≈ 0.933 protected
    frac = _compute_protected_fraction(queue_position=1, stop_gap=2.0, limit_gap=2.0, bar_duration_secs=60)
    assert frac == pytest.approx(56 / 60, rel=0.01)


def test_protected_fraction_full_bar_lag():
    # Lag >= bar duration → zero protection
    frac = _compute_protected_fraction(queue_position=15, stop_gap=2.0, limit_gap=2.0, bar_duration_secs=60)
    assert frac == pytest.approx(0.0)


def test_check_sl_tp_with_lag_full_protection_same_as_normal():
    # protected_fraction=1.0 should behave identically to _check_sl_tp
    from alphaTrade.backtest.engine import _check_sl_tp, BacktestState
    from datetime import datetime
    state = BacktestState(
        side="BUY", entry_price=100.0, quantity=1.0,
        sl_price=95.0, tp_price=110.0, entry_bar=0,
        entry_time=datetime.utcnow(), model_id="m",
    )
    # SL hit: low=94 < sl=95
    normal = _check_sl_tp(state, high=105.0, low=94.0)
    with_lag = _check_sl_tp_with_lag(state, high=105.0, low=94.0, protected_fraction=1.0)
    assert normal == with_lag == ("SL", 95.0)


def test_check_sl_tp_with_lag_zero_protection_misses_sl():
    # protected_fraction=0.0: no range = no SL/TP trigger
    from alphaTrade.backtest.engine import BacktestState, _check_sl_tp_with_lag
    from datetime import datetime
    state = BacktestState(
        side="BUY", entry_price=100.0, quantity=1.0,
        sl_price=95.0, tp_price=110.0, entry_bar=0,
        entry_time=datetime.utcnow(), model_id="m",
    )
    result = _check_sl_tp_with_lag(state, high=115.0, low=80.0, protected_fraction=0.0)
    assert result is None


def test_check_sl_tp_with_lag_partial_protection():
    # Bar open=100, low=90 (drops 10), protected_fraction=0.5 → adjusted_low = 100 - 5 = 95
    # SL at 94: 95 > 94 → not triggered
    from alphaTrade.backtest.engine import BacktestState, _check_sl_tp_with_lag
    from datetime import datetime
    state = BacktestState(
        side="BUY", entry_price=100.0, quantity=1.0,
        sl_price=94.0, tp_price=115.0, entry_bar=0,
        entry_time=datetime.utcnow(), model_id="m",
    )
    # open=100, low=90, fraction=0.5 → adjusted_low=95 → SL@94 not hit
    result = _check_sl_tp_with_lag(state, high=105.0, low=90.0, protected_fraction=0.5, bar_open=100.0)
    assert result is None
