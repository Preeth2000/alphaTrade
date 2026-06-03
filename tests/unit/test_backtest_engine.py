"""Unit tests for backtest engine core logic and reporter."""
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

from alphaTrade.backtest.engine import _simulate_fill, _check_sl_tp, BacktestState, _infer


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
from alphaTrade.backtest.reporter import compute_summary, format_text  # noqa: E402


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


# _infer guardrail tests

def _make_manifest_for_infer():
    from alphaTrade.adapter.manifest import Manifest
    return Manifest(
        manifest_version="1.0",
        run_name="test",
        model_arch="mlp",
        opset=17,
        git_sha="abc",
        model_hash="unknown",
        output_format="logits",
        input_shape=[20],
        classes=["BUY", "SELL", "HOLD"],
        class_indices={"BUY": 0, "SELL": 1, "HOLD": 2},
        feature_names=["RSI", "ATR"],
        n_features=2,
        window=10,
        normalize="none",
        norm_stats={},
        ticker="AAPL",
        interval="1d",
    )


def test_infer_returns_hold_when_insufficient_data():
    manifest = _make_manifest_for_infer()
    mock_model = MagicMock()
    df = pd.DataFrame({"Open": [1]*5, "High": [2]*5, "Low": [0.5]*5, "Close": [1.5]*5, "Volume": [1000]*5})
    with patch("alphaTrade.backtest.engine.compute_features", return_value=df[["Open", "High"]]):
        result = _infer(manifest, mock_model, df)
    assert result == "HOLD"
    mock_model.run.assert_not_called()


def test_infer_propagates_exception_from_compute_features():
    """Broken feature pipeline must propagate — not silently return HOLD."""
    manifest = _make_manifest_for_infer()
    mock_model = MagicMock()
    df = pd.DataFrame({"Close": [1.0] * 20})
    with patch("alphaTrade.backtest.engine.compute_features", side_effect=RuntimeError("talib broken")):
        with pytest.raises(RuntimeError, match="talib broken"):
            _infer(manifest, mock_model, df)


def test_infer_propagates_exception_from_model_run():
    """Broken ONNX model must propagate — not silently return HOLD."""
    manifest = _make_manifest_for_infer()
    df = pd.DataFrame(
        {"RSI": [50.0] * 20, "ATR": [1.0] * 20},
        index=pd.date_range("2024-01-01", periods=20),
    )
    mock_model = MagicMock()
    mock_model.run.side_effect = ValueError("shape mismatch")
    with patch("alphaTrade.backtest.engine.compute_features", return_value=df):
        with patch("alphaTrade.backtest.engine.normalize", return_value=df):
            with patch("alphaTrade.backtest.engine.build_input", return_value=None):
                with pytest.raises(ValueError, match="shape mismatch"):
                    _infer(manifest, mock_model, df)
