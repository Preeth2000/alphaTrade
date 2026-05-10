"""Pydantic settings + overrides.yaml loader."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import model_validator
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
    webhook_levels: str = "WARNING"

    # Populated from overrides.yaml after load
    defaults: Defaults = Defaults()
    risk: RiskConfig = RiskConfig()
    model_overrides: dict[str, ModelOverride] = {}

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
