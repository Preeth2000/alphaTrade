from __future__ import annotations
import textwrap
from pathlib import Path
from alphaTrade.config import Settings


def _write_overrides(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "overrides.yaml"
    p.write_text(textwrap.dedent(content))
    return p


def test_backtest_schedule_defaults():
    from alphaTrade.config import BacktestConfig
    cfg = BacktestConfig()
    assert cfg.schedule_enabled is True
    assert cfg.cron == "0 2 * * *"
    assert cfg.lookback_days == 30


def test_backtest_schedule_from_yaml(tmp_path):
    _write_overrides(tmp_path, """
        backtest:
          schedule_enabled: false
          cron: "0 6 * * *"
          lookback_days: 60
    """)
    s = Settings(overrides_path=tmp_path / "overrides.yaml", state_db_path=tmp_path / "s.db")
    assert s.backtest.schedule_enabled is False
    assert s.backtest.cron == "0 6 * * *"
    assert s.backtest.lookback_days == 60


def test_model_backtest_override_disabled(tmp_path):
    _write_overrides(tmp_path, """
        models:
          MSFT_v1:
            backtest:
              disabled: true
    """)
    s = Settings(overrides_path=tmp_path / "overrides.yaml", state_db_path=tmp_path / "s.db")
    assert s.model_overrides["MSFT_v1"].backtest.disabled is True


def test_model_backtest_override_cron(tmp_path):
    _write_overrides(tmp_path, """
        models:
          AAPL_v1:
            backtest:
              cron: "0 4 * * *"
              lookback_days: 14
    """)
    s = Settings(overrides_path=tmp_path / "overrides.yaml", state_db_path=tmp_path / "s.db")
    ov = s.model_overrides["AAPL_v1"].backtest
    assert ov.cron == "0 4 * * *"
    assert ov.lookback_days == 14


def test_model_backtest_defaults_not_disabled(tmp_path):
    _write_overrides(tmp_path, """
        models:
          AAPL_v1:
            enabled: true
    """)
    s = Settings(overrides_path=tmp_path / "overrides.yaml", state_db_path=tmp_path / "s.db")
    assert s.model_overrides["AAPL_v1"].backtest.disabled is False
    assert s.model_overrides["AAPL_v1"].backtest.cron is None
