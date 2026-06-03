"""Verify new config classes parse correctly."""
from alphaTrade.config import (
    AlertsConfig, AlertSlackConfig, AlertEmailConfig,
    BacktestConfig, RiskConfig,
)


def test_alerts_config_defaults():
    cfg = AlertsConfig()
    assert cfg.slack.enabled is False
    assert cfg.email.enabled is False
    assert cfg.slack.min_level == "WARNING"
    assert cfg.email.min_level == "CRITICAL"


def test_slack_config_from_dict():
    cfg = AlertSlackConfig(**{"enabled": True, "webhook_url": "https://hooks.slack.com/x", "min_level": "INFO"})
    assert cfg.enabled is True
    assert cfg.webhook_url == "https://hooks.slack.com/x"


def test_email_config_from_dict():
    cfg = AlertEmailConfig(**{
        "enabled": True, "smtp_host": "smtp.gmail.com",
        "to_addrs": ["a@b.com"], "from_addr": "bot@example.com",
    })
    assert cfg.to_addrs == ["a@b.com"]
    assert cfg.from_addr == "bot@example.com"


def test_backtest_config_defaults():
    cfg = BacktestConfig()
    assert cfg.slippage_bps == 5
    assert cfg.initial_equity == 10000.0
    assert cfg.default_size_pct == 0.10
    assert cfg.sl_pct is None
    assert cfg.tp_pct is None


def test_backtest_config_with_sl_tp():
    cfg = BacktestConfig(sl_pct=5.0, tp_pct=10.0)
    assert cfg.sl_pct == 5.0
    assert cfg.tp_pct == 10.0


def test_risk_config_defaults():
    cfg = RiskConfig()
    assert cfg.max_positions == 5
    assert cfg.sizing_mode == "fixed"
    assert cfg.portfolio_mode == "unbalanced"
    assert cfg.model_retirement.enabled is False
    assert cfg.balanced.max_sector_pct == 0.33
    assert cfg.unbalanced.max_per_sector == 3


def test_risk_config_with_retirement():
    cfg = RiskConfig(**{
        "max_positions": 3,
        "sizing_mode": "atr",
        "model_retirement": {"enabled": True, "lookback_trades": 15},
    })
    assert cfg.max_positions == 3
    assert cfg.sizing_mode == "atr"
    assert cfg.model_retirement.enabled is True
    assert cfg.model_retirement.lookback_trades == 15


def test_risk_config_balanced_mode():
    cfg = RiskConfig(**{"portfolio_mode": "balanced", "balanced": {"max_sector_pct": 0.25}})
    assert cfg.portfolio_mode == "balanced"
    assert cfg.balanced.max_sector_pct == 0.25


def test_risk_config_unbalanced_overrides():
    cfg = RiskConfig(**{
        "portfolio_mode": "unbalanced",
        "unbalanced": {"max_per_sector": 2, "sector_overrides": {"technology": 4}},
    })
    assert cfg.unbalanced.max_per_sector == 2
    assert cfg.unbalanced.sector_overrides["technology"] == 4


def test_t212_throttle_config_defaults():
    from alphaTrade.config import T212ThrottleConfig
    cfg = T212ThrottleConfig()
    assert cfg.orders_stop_min_gap_secs == 2.0
    assert cfg.orders_limit_min_gap_secs == 2.0
    assert cfg.orders_market_min_gap_secs == 1.2
    assert cfg.account_cash_min_gap_secs == 5.0
    assert cfg.orders_cancel_min_gap_secs == 1.2
    assert cfg.portfolio_min_gap_secs == 1.0
    assert cfg.orders_status_min_gap_secs == 1.0


def test_executors_config_nested():
    from alphaTrade.config import ExecutorsConfig
    cfg = ExecutorsConfig()
    assert cfg.trading212.throttle.orders_stop_min_gap_secs == 2.0


def test_settings_has_executors():
    from alphaTrade.config import Settings
    s = Settings()
    assert s.executors.trading212.throttle.account_cash_min_gap_secs == 5.0


def test_risk_config_queue_fields():
    from alphaTrade.config import RiskConfig
    cfg = RiskConfig()
    assert cfg.order_stale_window_multiplier == 0.5
    assert cfg.order_queue_max_depth == 50


def test_backtest_config_simulate_oco_lag_default_false():
    from alphaTrade.config import BacktestConfig
    cfg = BacktestConfig()
    assert cfg.simulate_oco_lag is False
