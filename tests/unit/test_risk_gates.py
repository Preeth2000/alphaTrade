"""Tests for risk gate pipeline."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock


from alphaTrade.risk.gates import run_gates
from alphaTrade.store.repos import Position


def _mock_repo(positions: list[Position] | None = None, ticker_pos: Position | None = None):
    repo = MagicMock()
    repo.all.return_value = positions or []
    repo.get.return_value = ticker_pos
    return repo


NOW = datetime(2026, 5, 10, 12, 0, 0)


def test_hold_always_rejected():
    repo = _mock_repo()
    r = run_gates("HOLD", "AAPL_US_EQ", repo, max_positions=5, daily_loss_halted=False, now=NOW)
    assert not r.approved
    assert "HOLD" in r.reason


def test_drawdown_halt_blocks_buy():
    repo = _mock_repo()
    r = run_gates("BUY", "AAPL_US_EQ", repo, max_positions=5, daily_loss_halted=True, now=NOW)
    assert not r.approved
    assert "halt" in r.reason


def test_buy_approved():
    repo = _mock_repo(positions=[], ticker_pos=None)
    r = run_gates("BUY", "AAPL_US_EQ", repo, max_positions=5, daily_loss_halted=False, now=NOW)
    assert r.approved


def test_buy_blocked_max_positions():
    positions = [MagicMock() for _ in range(5)]
    repo = _mock_repo(positions=positions, ticker_pos=None)
    r = run_gates("BUY", "AAPL_US_EQ", repo, max_positions=5, daily_loss_halted=False, now=NOW)
    assert not r.approved
    assert "max positions" in r.reason


def test_buy_blocked_already_long():
    pos = Position(t212_ticker="AAPL_US_EQ", quantity=10.0, avg_entry=150.0)
    repo = _mock_repo(positions=[pos], ticker_pos=pos)
    r = run_gates("BUY", "AAPL_US_EQ", repo, max_positions=5, daily_loss_halted=False, now=NOW)
    assert not r.approved
    assert "pyramiding" in r.reason


def test_sell_blocked_no_position():
    repo = _mock_repo(positions=[], ticker_pos=None)
    r = run_gates("SELL", "AAPL_US_EQ", repo, max_positions=5, daily_loss_halted=False, now=NOW)
    assert not r.approved


def test_sell_approved_with_position():
    pos = Position(t212_ticker="AAPL_US_EQ", quantity=10.0, avg_entry=150.0)
    repo = _mock_repo(positions=[pos], ticker_pos=pos)
    r = run_gates("SELL", "AAPL_US_EQ", repo, max_positions=5, daily_loss_halted=False, now=NOW)
    assert r.approved


def test_cooldown_blocks():
    pos = Position(
        t212_ticker="AAPL_US_EQ",
        quantity=0,
        avg_entry=0,
        cooldown_until_ts=NOW + timedelta(hours=1),
    )
    repo = _mock_repo(positions=[], ticker_pos=pos)
    r = run_gates("BUY", "AAPL_US_EQ", repo, max_positions=5, daily_loss_halted=False, now=NOW)
    assert not r.approved
    assert "cooldown" in r.reason


def test_cooldown_expired_allows():
    pos = Position(
        t212_ticker="AAPL_US_EQ",
        quantity=0,
        avg_entry=0,
        cooldown_until_ts=NOW - timedelta(hours=1),
    )
    repo = _mock_repo(positions=[], ticker_pos=pos)
    r = run_gates("BUY", "AAPL_US_EQ", repo, max_positions=5, daily_loss_halted=False, now=NOW)
    assert r.approved
