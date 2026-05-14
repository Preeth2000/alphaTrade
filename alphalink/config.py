"""Pydantic settings + overrides.yaml loader."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelOverride(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    enabled: bool = True
    t212_ticker: Optional[str] = None
    size_pct: Optional[float] = None


class ModelRetirementConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    enabled: bool = False
    lookback_trades: int = 20
    min_win_rate: float = 0.4
    min_rolling_pnl: float = -500.0
    auto_reload: bool = True


class BalancedPortfolioConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    max_sector_pct: float = 0.33


class UnbalancedPortfolioConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    max_per_sector: int = 3
    sector_overrides: dict[str, int] = {}


class AtrSizingConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    risk_pct: float = 0.01
    atr_multiplier: float = 2.0


class VixSizingConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    base_size_pct: float = 0.05
    vix_scalar: float = 20.0
    max_size_pct: float = 0.15


class AlertSlackConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    enabled: bool = False
    webhook_url: str = ""
    min_level: str = "WARNING"


class AlertEmailConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    from_addr: str = ""
    to_addrs: list[str] = []
    min_level: str = "CRITICAL"


class AlertsConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    slack: Optional[AlertSlackConfig] = AlertSlackConfig()
    email: Optional[AlertEmailConfig] = AlertEmailConfig()


class BacktestConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    slippage_bps: int = 5
    commission_per_trade: float = 0.0
    initial_equity: float = 10000.0
    default_size_pct: float = 0.10
    sl_pct: Optional[float] = None
    tp_pct: Optional[float] = None


class RiskConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    max_positions: int = 5
    daily_loss_halt_pct: float = 0.05
    sizing_mode: str = "fixed"          # fixed | atr | vix
    portfolio_mode: str = "unbalanced"  # balanced | unbalanced
    model_retirement: ModelRetirementConfig = ModelRetirementConfig()
    balanced: BalancedPortfolioConfig = BalancedPortfolioConfig()
    unbalanced: UnbalancedPortfolioConfig = UnbalancedPortfolioConfig()
    atr: AtrSizingConfig = AtrSizingConfig()
    vix: VixSizingConfig = VixSizingConfig()


class Defaults(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    size_pct: float = 0.10
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.05
    cooldown_bars: int = 3
    extended_hours: bool = False  # set True to allow ticks outside NYSE regular hours


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    t212_api_key: str
    t212_env: str = "demo"
    data_provider: str = "yfinance"
    polygon_api_key: str = ""
    models_dir: Path = Path("./models")
    state_db_path: Path = Path("./state.db")
    overrides_path: Path = Path("./overrides.yaml")
    webhook_url: str = ""
    webhook_level: str = "WARNING"
    log_file: Path = Path("./alphalink.log")

    # Populated from overrides.yaml after load
    defaults: Defaults = Defaults()
    risk: RiskConfig = RiskConfig()
    model_overrides: dict[str, ModelOverride] = {}
    alerts: AlertsConfig = AlertsConfig()
    backtest: BacktestConfig = BacktestConfig()

    @field_validator("webhook_level")
    @classmethod
    def _valid_webhook_level(cls, v: str) -> str:
        import logging as _logging
        if v.upper() not in _logging._nameToLevel:
            raise ValueError(f"webhook_level must be a valid log level name, got {v!r}")
        return v.upper()

    @model_validator(mode="after")
    def _load_overrides(self) -> "Settings":
        if self.overrides_path.exists():
            raw: dict[str, Any] = yaml.safe_load(self.overrides_path.read_text()) or {}
            if "defaults" in raw:
                self.defaults = Defaults(**raw["defaults"])
            if "risk" in raw:
                self.risk = RiskConfig(**raw["risk"])
            if "models" in raw:
                self.model_overrides = {
                    run_name: ModelOverride(**(cfg or {}))
                    for run_name, cfg in raw["models"].items()
                }
            if "alerts" in raw:
                self.alerts = AlertsConfig(**raw["alerts"])
            if "backtest" in raw:
                self.backtest = BacktestConfig(**raw["backtest"])
        if self.data_provider == "polygon" and not self.polygon_api_key:
            raise ValueError("POLYGON_API_KEY is required when DATA_PROVIDER=polygon")
        if self.t212_env not in ("demo", "live"):
            raise ValueError(f"T212_ENV must be 'demo' or 'live', got {self.t212_env!r}")
        return self
