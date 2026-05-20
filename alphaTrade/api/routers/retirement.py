"""Retirement config API — global and per-model."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlmodel import Session

from alphaTrade.config import ModelRetirementOverride, Settings
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

    def _per_model_response(run_name: str, session: Session) -> PerModelRetirementResponse:
        from alphaTrade.store.repos import ModelOverrideRepo
        rec = ModelOverrideRepo(session).get(run_name)
        override = ModelRetirementOverride(
            enabled=rec.retirement_enabled if rec else None,
            lookback_trades=rec.retirement_lookback_trades if rec else None,
            min_win_rate=rec.retirement_min_win_rate if rec else None,
            min_rolling_pnl=rec.retirement_min_rolling_pnl if rec else None,
            min_trades_before_evaluation=rec.retirement_min_trades_before_evaluation if rec else None,
            min_evaluation_period=rec.retirement_min_evaluation_period if rec else None,
        )
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
    def get_per_model(
        run_name: str,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        return _per_model_response(run_name, session)

    @router.patch("/models/{run_name}/retirement", response_model=PerModelRetirementResponse)
    def patch_per_model(
        run_name: str,
        update: PerModelRetirementUpdate,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        from alphaTrade.store.repos import ModelOverrideRepo, ModelOverrideRecord
        repo = ModelOverrideRepo(session)
        rec = repo.get(run_name) or ModelOverrideRecord(run_name=run_name)
        field_map = {
            "enabled": "retirement_enabled",
            "lookback_trades": "retirement_lookback_trades",
            "min_win_rate": "retirement_min_win_rate",
            "min_rolling_pnl": "retirement_min_rolling_pnl",
            "min_trades_before_evaluation": "retirement_min_trades_before_evaluation",
            "min_evaluation_period": "retirement_min_evaluation_period",
        }
        for field, val in update.model_dump(exclude_unset=True).items():
            setattr(rec, field_map[field], val)
        repo.upsert(rec)
        return _per_model_response(run_name, session)

    @router.delete("/models/{run_name}/retirement")
    def delete_per_model(
        run_name: str,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        from alphaTrade.store.repos import ModelOverrideRepo
        repo = ModelOverrideRepo(session)
        rec = repo.get(run_name)
        if rec is not None:
            for col in ("retirement_enabled", "retirement_lookback_trades", "retirement_min_win_rate",
                        "retirement_min_rolling_pnl", "retirement_min_trades_before_evaluation",
                        "retirement_min_evaluation_period"):
                setattr(rec, col, None)
            repo.upsert(rec)
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
