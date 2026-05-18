"""Tests for ModelRetirementOverride merge logic and period parser."""
import pytest
from alphaTrade.config import ModelRetirementConfig, ModelRetirementOverride
from alphaTrade.risk.performance import _effective_config, _parse_period
from datetime import timedelta


def _global():
    return ModelRetirementConfig(
        enabled=True,
        lookback_trades=20,
        min_win_rate=0.4,
        min_rolling_pnl=-500.0,
        min_evaluation_period="30d",
        min_trades_before_evaluation=5,
    )


def test_none_per_model_fields_fall_back_to_global():
    cfg = _effective_config(_global(), ModelRetirementOverride())
    assert cfg.enabled is True
    assert cfg.lookback_trades == 20
    assert cfg.min_win_rate == 0.4
    assert cfg.min_rolling_pnl == -500.0
    assert cfg.min_evaluation_period == "30d"
    assert cfg.min_trades_before_evaluation == 5


def test_non_none_per_model_fields_override_global():
    override = ModelRetirementOverride(
        min_win_rate=0.3,
        min_rolling_pnl=-200.0,
        lookback_trades=10,
        min_trades_before_evaluation=3,
        min_evaluation_period="60d",
    )
    cfg = _effective_config(_global(), override)
    assert cfg.min_win_rate == 0.3
    assert cfg.min_rolling_pnl == -200.0
    assert cfg.lookback_trades == 10
    assert cfg.min_trades_before_evaluation == 3
    assert cfg.min_evaluation_period == "60d"
    # non-overridden fields still come from global
    assert cfg.enabled is True


def test_per_model_enabled_false_overrides_global_true():
    override = ModelRetirementOverride(enabled=False)
    cfg = _effective_config(_global(), override)
    assert cfg.enabled is False


def test_parse_period_days():
    assert _parse_period("30d") == timedelta(days=30)
    assert _parse_period("7d") == timedelta(days=7)
    assert _parse_period("1d") == timedelta(days=1)


def test_parse_period_invalid_raises():
    with pytest.raises(ValueError, match="Invalid period format"):
        _parse_period("1m")
    with pytest.raises(ValueError, match="Invalid period format"):
        _parse_period("30")
    with pytest.raises(ValueError, match="Invalid period format"):
        _parse_period("")
