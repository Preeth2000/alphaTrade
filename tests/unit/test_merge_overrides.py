"""Tests for _merge_overrides — DB fields win over YAML for all fields."""
from __future__ import annotations
from datetime import datetime, timezone
from alphaTrade.config import ModelOverride, ModelRetirementOverride, BacktestScheduleOverride
from alphaTrade.store.repos import ModelOverrideRecord
from alphaTrade.main import _merge_overrides


def _db(run_name, **kwargs) -> ModelOverrideRecord:
    return ModelOverrideRecord(run_name=run_name, updated_at=datetime.now(timezone.utc), **kwargs)


def test_db_enabled_wins_over_yaml():
    yaml = {"m1": ModelOverride(enabled=True)}
    db = {"m1": _db("m1", enabled=False)}
    result = _merge_overrides(yaml, db)
    assert result["m1"].enabled is False


def test_db_size_pct_wins_over_yaml():
    yaml = {"m1": ModelOverride(size_pct=0.10)}
    db = {"m1": _db("m1", size_pct=0.05)}
    result = _merge_overrides(yaml, db)
    assert result["m1"].size_pct == 0.05


def test_db_broker_ticker_wins_over_yaml():
    yaml = {"m1": ModelOverride(t212_ticker="AAPL_US_EQ")}
    db = {"m1": _db("m1", broker_ticker="MSFT_US_EQ")}
    result = _merge_overrides(yaml, db)
    assert result["m1"].t212_ticker == "MSFT_US_EQ"


def test_db_safe_mode_wins_over_yaml():
    yaml = {"m1": ModelOverride(safe_mode=True)}
    db = {"m1": _db("m1", safe_mode=False)}
    result = _merge_overrides(yaml, db)
    assert result["m1"].safe_mode is False


def test_db_retirement_fields_win_over_yaml():
    yaml_ret = ModelRetirementOverride(enabled=False, min_win_rate=0.4)
    yaml = {"m1": ModelOverride(retirement=yaml_ret)}
    db = {"m1": _db("m1", retirement_enabled=True, retirement_min_win_rate=0.6)}
    result = _merge_overrides(yaml, db)
    assert result["m1"].retirement.enabled is True
    assert result["m1"].retirement.min_win_rate == 0.6


def test_db_backtest_fields_win_over_yaml():
    yaml_bt = BacktestScheduleOverride(disabled=False, lookback_days=30)
    yaml = {"m1": ModelOverride(backtest=yaml_bt)}
    db = {"m1": _db("m1", backtest_disabled=True, backtest_lookback_days=60)}
    result = _merge_overrides(yaml, db)
    assert result["m1"].backtest.disabled is True
    assert result["m1"].backtest.lookback_days == 60


def test_yaml_fields_preserved_when_db_null():
    yaml = {"m1": ModelOverride(size_pct=0.10, t212_ticker="AAPL_US_EQ")}
    db = {"m1": _db("m1", enabled=False)}  # only enabled set
    result = _merge_overrides(yaml, db)
    assert result["m1"].size_pct == 0.10
    assert result["m1"].t212_ticker == "AAPL_US_EQ"
    assert result["m1"].enabled is False


def test_db_only_model_added_to_result():
    yaml = {}
    db = {"m1": _db("m1", enabled=True, size_pct=0.05)}
    result = _merge_overrides(yaml, db)
    assert "m1" in result
    assert result["m1"].enabled is True
    assert result["m1"].size_pct == 0.05


def test_db_dangerously_allow_pyramid_wins_over_yaml():
    yaml = {"m1": ModelOverride(dangerously_allow_pyramid=False)}
    db = {"m1": _db("m1", dangerously_allow_pyramid=True)}
    result = _merge_overrides(yaml, db)
    assert result["m1"].dangerously_allow_pyramid is True
