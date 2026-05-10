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


class RiskConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    max_positions: int = 5
    daily_loss_halt_pct: float = 0.05


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
        if self.data_provider == "polygon" and not self.polygon_api_key:
            raise ValueError("POLYGON_API_KEY is required when DATA_PROVIDER=polygon")
        if self.t212_env not in ("demo", "live"):
            raise ValueError(f"T212_ENV must be 'demo' or 'live', got {self.t212_env!r}")
        return self
