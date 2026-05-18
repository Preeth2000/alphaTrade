"""Retirement config API — global and per-model."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlmodel import Session

from alphaTrade.config import ModelOverride, ModelRetirementOverride, Settings
from alphaTrade.risk.performance import _effective_config, _parse_period
from alphaTrade.store.repos import BotSettings, BotSettingsRepo, ModelPerformanceRepo

log = logging.getLogger(__name__)


class GlobalRetirementUpdate(BaseModel):
    enabled: Optional[bool] = None
    lookback_trades: Optional[int] = None
    min_win_rate: Optional[float] = None
    min_rolling_pnl: Optional[float] = None
    min_trades_before_evaluation: Optional[int] = None
    min_evaluation_period: Optional[str] = None

    @field_validator("min_evaluation_period")
    @classmethod
    def _valid_period(cls, v: str | None) -> str | None:
        if v is not None:
            _parse_period(v)
        return v


class GlobalRetirementResponse(BaseModel):
    enabled: bool
    lookback_trades: int
    min_win_rate: float
    min_rolling_pnl: float
    min_trades_before_evaluation: int
    min_evaluation_period: str


class PerModelRetirementUpdate(BaseModel):
    enabled: Optional[bool] = None
    lookback_trades: Optional[int] = None
    min_win_rate: Optional[float] = None
    min_rolling_pnl: Optional[float] = None
    min_trades_before_evaluation: Optional[int] = None
    min_evaluation_period: Optional[str] = None

    @field_validator("min_evaluation_period")
    @classmethod
    def _valid_period(cls, v: str | None) -> str | None:
        if v is not None:
            _parse_period(v)
        return v


class PerModelRetirementResponse(BaseModel):
    run_name: str
    enabled: Optional[bool] = None
    lookback_trades: Optional[int] = None
    min_win_rate: Optional[float] = None
    min_rolling_pnl: Optional[float] = None
    min_trades_before_evaluation: Optional[int] = None
    min_evaluation_period: Optional[str] = None
    effective_enabled: bool
    effective_lookback_trades: int
    effective_min_win_rate: float
    effective_min_rolling_pnl: float
    effective_min_trades_before_evaluation: int
    effective_min_evaluation_period: str


def _persist_retirement_overrides(settings: Settings) -> None:
    path = settings.overrides_path
    raw: dict = yaml.safe_load(path.read_text()) if path.exists() and path.stat().st_size > 0 else {}
    raw.setdefault("models", {})
    for run_name, override in settings.model_overrides.items():
        ret = override.retirement
        ret_dict: dict = {}
        if ret.enabled is not None:
            ret_dict["enabled"] = ret.enabled
        if ret.lookback_trades is not None:
            ret_dict["lookback_trades"] = ret.lookback_trades
        if ret.min_win_rate is not None:
            ret_dict["min_win_rate"] = ret.min_win_rate
        if ret.min_rolling_pnl is not None:
            ret_dict["min_rolling_pnl"] = ret.min_rolling_pnl
        if ret.min_trades_before_evaluation is not None:
            ret_dict["min_trades_before_evaluation"] = ret.min_trades_before_evaluation
        if ret.min_evaluation_period is not None:
            ret_dict["min_evaluation_period"] = ret.min_evaluation_period
        existing = raw["models"].get(run_name, {})
        if ret_dict:
            existing["retirement"] = ret_dict
            raw["models"][run_name] = existing
        elif "retirement" in existing:
            del existing["retirement"]
            if existing:
                raw["models"][run_name] = existing
            else:
                raw["models"].pop(run_name, None)
    path.write_text(yaml.dump(raw, default_flow_style=False))


def make_router(session_dep: Callable, api_key_dep: Callable, settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get("/retirement/config", response_model=GlobalRetirementResponse)
    def get_global_config(_: None = Depends(api_key_dep)):
        cfg = settings.risk.model_retirement
        return GlobalRetirementResponse(
            enabled=cfg.enabled,
            lookback_trades=cfg.lookback_trades,
            min_win_rate=cfg.min_win_rate,
            min_rolling_pnl=cfg.min_rolling_pnl,
            min_trades_before_evaluation=cfg.min_trades_before_evaluation,
            min_evaluation_period=cfg.min_evaluation_period,
        )

    @router.patch("/retirement/config", response_model=GlobalRetirementResponse)
    def patch_global_config(
        update: GlobalRetirementUpdate,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        cfg = settings.risk.model_retirement
        repo = BotSettingsRepo(session)
        bs = repo.get()
        if bs is None:
            bs = BotSettings(id=1)

        for field, val in update.model_dump(exclude_unset=True).items():
            setattr(cfg, field, val)
            setattr(bs, f"retirement_{field}", val)

        repo.upsert(bs)
        return GlobalRetirementResponse(
            enabled=cfg.enabled,
            lookback_trades=cfg.lookback_trades,
            min_win_rate=cfg.min_win_rate,
            min_rolling_pnl=cfg.min_rolling_pnl,
            min_trades_before_evaluation=cfg.min_trades_before_evaluation,
            min_evaluation_period=cfg.min_evaluation_period,
        )

    def _per_model_response(run_name: str) -> PerModelRetirementResponse:
        override = settings.model_overrides.get(run_name, ModelOverride()).retirement
        effective = _effective_config(settings.risk.model_retirement, override)
        return PerModelRetirementResponse(
            run_name=run_name,
            enabled=override.enabled,
            lookback_trades=override.lookback_trades,
            min_win_rate=override.min_win_rate,
            min_rolling_pnl=override.min_rolling_pnl,
            min_trades_before_evaluation=override.min_trades_before_evaluation,
            min_evaluation_period=override.min_evaluation_period,
            effective_enabled=effective.enabled,
            effective_lookback_trades=effective.lookback_trades,
            effective_min_win_rate=effective.min_win_rate,
            effective_min_rolling_pnl=effective.min_rolling_pnl,
            effective_min_trades_before_evaluation=effective.min_trades_before_evaluation,
            effective_min_evaluation_period=effective.min_evaluation_period,
        )

    @router.get("/models/{run_name}/retirement", response_model=PerModelRetirementResponse)
    def get_per_model(run_name: str, _: None = Depends(api_key_dep)):
        return _per_model_response(run_name)

    @router.patch("/models/{run_name}/retirement", response_model=PerModelRetirementResponse)
    def patch_per_model(
        run_name: str,
        update: PerModelRetirementUpdate,
        _: None = Depends(api_key_dep),
    ):
        if run_name not in settings.model_overrides:
            settings.model_overrides[run_name] = ModelOverride()
        override = settings.model_overrides[run_name].retirement
        for field, val in update.model_dump(exclude_unset=True).items():
            setattr(override, field, val)
        _persist_retirement_overrides(settings)
        return _per_model_response(run_name)

    @router.delete("/models/{run_name}/retirement")
    def delete_per_model(run_name: str, _: None = Depends(api_key_dep)):
        if run_name in settings.model_overrides:
            settings.model_overrides[run_name].retirement = ModelRetirementOverride()
            _persist_retirement_overrides(settings)
        return {"deleted": True, "run_name": run_name}

    @router.post("/models/{run_name}/unretire")
    def unretire_model(
        run_name: str,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        repo = ModelPerformanceRepo(session)
        perf = repo.get(run_name)
        if perf is None:
            raise HTTPException(status_code=404, detail=f"No performance record for {run_name!r}")
        perf.retired = False
        perf.retired_at = None
        perf.first_trade_at = None
        perf.trade_count = 0
        perf.win_count = 0
        perf.rolling_pnl = 0.0
        perf.rolling_trades_json = "[]"
        repo.update(perf)
        return {"unretired": True, "run_name": run_name}

    return router
