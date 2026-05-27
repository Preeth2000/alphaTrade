"""Unit tests for _t212_credentials account selection."""
from __future__ import annotations

import os
from unittest.mock import MagicMock

from alphaTrade.config import Settings
from alphaTrade.main import _t212_credentials, apply_bot_settings
from alphaTrade.store.repos import BotSettings


def _make_db_settings(**kwargs) -> BotSettings:
    defaults = dict(
        t212_active_account="demo",
        t212_demo_api_key="demo-key",
        t212_demo_secret_key="demo-secret",
        t212_invest_api_key="invest-key",
        t212_invest_secret_key="invest-secret",
        t212_isa_api_key="isa-key",
        t212_isa_secret_key="isa-secret",
    )
    defaults.update(kwargs)
    return BotSettings(id=1, **defaults)


def test_demo_account_returns_demo_key():
    db_s = _make_db_settings(t212_active_account="demo")
    key, secret, env = _t212_credentials(db_s)
    assert key == "demo-key"
    assert secret == "demo-secret"
    assert env == "demo"


def test_invest_account_returns_invest_key():
    db_s = _make_db_settings(t212_active_account="invest")
    key, secret, env = _t212_credentials(db_s)
    assert key == "invest-key"
    assert secret == "invest-secret"
    assert env == "live"


def test_isa_account_returns_isa_key():
    db_s = _make_db_settings(t212_active_account="isa")
    key, secret, env = _t212_credentials(db_s)
    assert key == "isa-key"
    assert secret == "isa-secret"
    assert env == "live"


def test_empty_active_account_defaults_to_demo():
    db_s = _make_db_settings(t212_active_account="")
    key, secret, env = _t212_credentials(db_s)
    assert env == "demo"
    assert key == "demo-key"


def test_none_active_account_defaults_to_demo():
    db_s = _make_db_settings(t212_active_account=None)
    key, secret, env = _t212_credentials(db_s)
    assert env == "demo"


# Tests for apply_bot_settings with new risk/backtest/sizing fields


def _make_settings_for_apply(tmp_path) -> Settings:
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text("")
    os.environ.setdefault("ALPHATRADE_API_KEY", "test-key")
    return Settings(overrides_path=overrides, state_db_path=tmp_path / "state.db")


def test_apply_bot_settings_sizing_mode(tmp_path):
    settings = _make_settings_for_apply(tmp_path)
    db_s = BotSettings(id=1, sizing_mode="atr")
    apply_bot_settings(db_s, settings, [MagicMock()], [MagicMock()])
    assert settings.risk.sizing_mode == "atr"


def test_apply_bot_settings_portfolio_mode(tmp_path):
    settings = _make_settings_for_apply(tmp_path)
    db_s = BotSettings(id=1, portfolio_mode="balanced")
    apply_bot_settings(db_s, settings, [MagicMock()], [MagicMock()])
    assert settings.risk.portfolio_mode == "balanced"


def test_apply_bot_settings_order_stale_window(tmp_path):
    settings = _make_settings_for_apply(tmp_path)
    db_s = BotSettings(id=1, order_stale_window_multiplier=0.75, order_queue_max_depth=10)
    apply_bot_settings(db_s, settings, [MagicMock()], [MagicMock()])
    assert settings.risk.order_stale_window_multiplier == 0.75
    assert settings.risk.order_queue_max_depth == 10


def test_apply_bot_settings_atr(tmp_path):
    settings = _make_settings_for_apply(tmp_path)
    db_s = BotSettings(id=1, atr_risk_pct=0.02, atr_multiplier=3.0)
    apply_bot_settings(db_s, settings, [MagicMock()], [MagicMock()])
    assert settings.risk.atr.risk_pct == 0.02
    assert settings.risk.atr.atr_multiplier == 3.0


def test_apply_bot_settings_vix(tmp_path):
    settings = _make_settings_for_apply(tmp_path)
    db_s = BotSettings(id=1, vix_base_size_pct=0.03, vix_scalar=25.0, vix_max_size_pct=0.20)
    apply_bot_settings(db_s, settings, [MagicMock()], [MagicMock()])
    assert settings.risk.vix.base_size_pct == 0.03
    assert settings.risk.vix.vix_scalar == 25.0
    assert settings.risk.vix.max_size_pct == 0.20


def test_apply_bot_settings_balanced(tmp_path):
    settings = _make_settings_for_apply(tmp_path)
    db_s = BotSettings(id=1, balanced_max_sector_pct=0.50)
    apply_bot_settings(db_s, settings, [MagicMock()], [MagicMock()])
    assert settings.risk.balanced.max_sector_pct == 0.50


def test_apply_bot_settings_unbalanced(tmp_path):
    settings = _make_settings_for_apply(tmp_path)
    db_s = BotSettings(id=1, unbalanced_max_per_sector=5, unbalanced_sector_overrides={"Technology": 7})
    apply_bot_settings(db_s, settings, [MagicMock()], [MagicMock()])
    assert settings.risk.unbalanced.max_per_sector == 5
    assert settings.risk.unbalanced.sector_overrides == {"Technology": 7}


def test_apply_bot_settings_backtest(tmp_path):
    settings = _make_settings_for_apply(tmp_path)
    db_s = BotSettings(
        id=1,
        backtest_slippage_bps=10,
        backtest_initial_equity=50000.0,
        backtest_cron="0 3 * * *",
        backtest_lookback_days=60,
        backtest_simulate_oco_lag=True,
        backtest_oco_stop_gap_secs=3.0,
        backtest_commission_per_trade=1.50,
        backtest_default_size_pct=0.05,
        backtest_sl_pct=0.02,
        backtest_tp_pct=0.04,
        backtest_schedule_enabled=False,
        backtest_oco_limit_gap_secs=1.5,
    )
    apply_bot_settings(db_s, settings, [MagicMock()], [MagicMock()])
    assert settings.backtest.slippage_bps == 10
    assert settings.backtest.initial_equity == 50000.0
    assert settings.backtest.cron == "0 3 * * *"
    assert settings.backtest.lookback_days == 60
    assert settings.backtest.simulate_oco_lag is True
    assert settings.backtest.oco_stop_gap_secs == 3.0
    assert settings.backtest.commission_per_trade == 1.50
    assert settings.backtest.default_size_pct == 0.05
    assert settings.backtest.sl_pct == 0.02
    assert settings.backtest.tp_pct == 0.04
    assert settings.backtest.schedule_enabled is False
    assert settings.backtest.oco_limit_gap_secs == 1.5


def test_apply_bot_settings_null_fields_not_overwrite(tmp_path):
    settings = _make_settings_for_apply(tmp_path)
    settings.risk.sizing_mode = "vix"
    db_s = BotSettings(id=1)  # all new fields None
    apply_bot_settings(db_s, settings, [MagicMock()], [MagicMock()])
    assert settings.risk.sizing_mode == "vix"  # unchanged
