"""Pydantic settings + overrides.yaml loader."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BacktestScheduleOverride(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    disabled: bool = False
    cron: Optional[str] = None
    lookback_days: Optional[int] = None


class ModelRetirementConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    enabled: bool = False
    lookback_trades: int = 20
    min_win_rate: float = 0.4
    min_rolling_pnl: float = -500.0
    auto_reload: bool = True
    min_evaluation_period: str = "30d"
    min_trades_before_evaluation: int = 5


class ModelRetirementOverride(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    enabled: Optional[bool] = None
    lookback_trades: Optional[int] = None
    min_win_rate: Optional[float] = None
    min_rolling_pnl: Optional[float] = None
    min_trades_before_evaluation: Optional[int] = None
    min_evaluation_period: Optional[str] = None


class ModelOverride(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    enabled: bool = True
    t212_ticker: Optional[str] = None
    size_pct: Optional[float] = None
    safe_mode: Optional[bool] = None                  # per-model override; None = use global RiskConfig value
    dangerously_allow_pyramid: Optional[bool] = None  # per-model override; None = use global RiskConfig value
    backtest: BacktestScheduleOverride = BacktestScheduleOverride()
    retirement: ModelRetirementOverride = ModelRetirementOverride()


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


class T212ThrottleConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    orders_market_min_gap_secs: float = 1.2
    orders_stop_min_gap_secs: float = 2.0
    orders_limit_min_gap_secs: float = 2.0
    orders_cancel_min_gap_secs: float = 1.2
    account_cash_min_gap_secs: float = 5.0
    portfolio_min_gap_secs: float = 1.0
    orders_status_min_gap_secs: float = 1.0


class T212ExecutorConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    throttle: T212ThrottleConfig = T212ThrottleConfig()


class ExecutorsConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    trading212: T212ExecutorConfig = T212ExecutorConfig()


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

    @field_validator("to_addrs", mode="before")
    @classmethod
    def _parse_to_addrs(cls, v: Any) -> Any:
        """Accept a comma-separated string or a JSON array from env vars."""
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                import json
                return json.loads(v)
            return [addr.strip() for addr in v.split(",") if addr.strip()]
        return v


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
    schedule_enabled: bool = True
    cron: str = "0 2 * * *"
    lookback_days: int = 30
    simulate_oco_lag: bool = False
    oco_stop_gap_secs: float = 2.0
    oco_limit_gap_secs: float = 2.0


class RiskConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    max_positions: int = 5
    daily_loss_halt_pct: float = 0.05
    sizing_mode: str = "fixed"          # fixed | atr | vix
    portfolio_mode: str = "unbalanced"  # balanced | unbalanced
    safe_mode: bool = True              # True = no pyramiding anywhere
    dangerously_allow_pyramid: bool = False  # global fallback for short-interval pyramid override
    order_stale_window_multiplier: float = 0.5
    order_queue_max_depth: int = 50
    consensus_min_confidence: float = 0.0  # 0 = disabled; e.g. 0.5 to require ≥50% probability
    consensus_min_margin: float = 0.0      # 0 = disabled; e.g. 0.1 to require 10pp lead over runner-up
    model_retirement: ModelRetirementConfig = ModelRetirementConfig()
    balanced: BalancedPortfolioConfig = BalancedPortfolioConfig()
    unbalanced: UnbalancedPortfolioConfig = UnbalancedPortfolioConfig()
    atr: AtrSizingConfig = AtrSizingConfig()
    vix: VixSizingConfig = VixSizingConfig()


class RedisConfig(BaseModel):
    url: str = "redis://localhost:6379/0"
    enabled: bool = True


class MinioConfig(BaseModel):
    endpoint: str = "localhost:9000"
    access_key: str = "minioadmin"
    secret_key: str = "minioadmin"
    bucket: str = "models"
    secure: bool = False


class ModelSyncConfig(BaseModel):
    enabled: bool = True
    user: str = "default"
    account: str = "default"
    poll_interval: int = 60         # seconds
    max_versions: int = 5           # -1 = unbounded
    max_download_attempts: int = 3  # retries before marking deployment failed


class Defaults(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    size_pct: float = 0.10
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.05
    cooldown_bars: int = 3
    extended_hours: bool = False  # set True to allow ticks outside NYSE regular hours


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_nested_delimiter="__", protected_namespaces=())

    t212_active_account: str = "demo"  # "demo" | "invest" | "isa"
    t212_demo_api_key: str = ""
    t212_demo_secret_key: str = ""
    t212_invest_api_key: str = ""
    t212_invest_secret_key: str = ""
    t212_isa_api_key: str = ""
    t212_isa_secret_key: str = ""
    data_provider: str = "yfinance"
    polygon_api_key: str = ""
    models_dir: Path = Path("./models")
    database_url: str = ""  # set to postgresql+psycopg2://... to use Postgres; empty = SQLite fallback
    state_db_path: Path = Path("./state.db")  # used only when database_url is empty
    overrides_path: Path = Path("./overrides.yaml")
    webhook_url: str = ""
    webhook_level: str = "WARNING"
    log_file: Optional[Path] = Path("./alphaTrade.log")
    api_port: int = 8081
    auth_mode: str = "legacy"  # "jwt" | "legacy" — set AUTH_MODE=jwt for alphaKey JWT enforcement
    redis: RedisConfig = RedisConfig()
    minio: MinioConfig = MinioConfig()
    model_sync: ModelSyncConfig = ModelSyncConfig()

    # Populated from overrides.yaml after load
    defaults: Defaults = Defaults()
    risk: RiskConfig = RiskConfig()
    model_overrides: dict[str, ModelOverride] = {}
    alerts: AlertsConfig = AlertsConfig()
    backtest: BacktestConfig = BacktestConfig()
    executors: ExecutorsConfig = ExecutorsConfig()

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
            if "executors" in raw:
                self.executors = ExecutorsConfig(**raw["executors"])
        if self.data_provider == "polygon" and not self.polygon_api_key:
            raise ValueError("POLYGON_API_KEY is required when DATA_PROVIDER=polygon")
        if self.t212_active_account not in ("demo", "invest", "isa"):
            raise ValueError(f"t212_active_account must be 'demo', 'invest', or 'isa', got {self.t212_active_account!r}")
        return self
