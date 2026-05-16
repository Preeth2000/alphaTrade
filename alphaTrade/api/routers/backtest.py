from __future__ import annotations

from collections.abc import Callable
from typing import Optional

from apscheduler.triggers.cron import CronTrigger as _CronTrigger
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlmodel import Session

from alphaTrade.store.repos import BacktestRun, BacktestTrade, BacktestRepo


class TriggerRequest(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None
    model_id: Optional[str] = None


class TriggerResponse(BaseModel):
    run_id: int
    status: str


class GlobalScheduleUpdate(BaseModel):
    schedule_enabled: Optional[bool] = None
    cron: Optional[str] = None
    lookback_days: Optional[int] = None

    @field_validator("cron")
    @classmethod
    def _valid_cron(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                _CronTrigger.from_crontab(v)
            except Exception:
                raise ValueError(f"Invalid cron expression: {v!r}")
        return v


class ModelScheduleUpdate(BaseModel):
    disabled: Optional[bool] = None
    cron: Optional[str] = None
    lookback_days: Optional[int] = None

    @field_validator("cron")
    @classmethod
    def _valid_cron(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                _CronTrigger.from_crontab(v)
            except Exception:
                raise ValueError(f"Invalid cron expression: {v!r}")
        return v


def make_router(session_dep: Callable, api_key_dep: Callable, backtest_scheduler=None) -> APIRouter:
    router = APIRouter()

    @router.post("/backtest/trigger", response_model=TriggerResponse, status_code=202)
    async def trigger_backtest(
        req: TriggerRequest,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        if backtest_scheduler is None:
            raise HTTPException(status_code=503, detail="Backtest scheduler not available")
        run_id = await backtest_scheduler.trigger(
            session=session,
            start=req.start,
            end=req.end,
            model_filter=req.model_id,
        )
        return TriggerResponse(run_id=run_id, status="queued")

    @router.get("/backtest/runs", response_model=list[BacktestRun])
    def list_runs(
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        return BacktestRepo(session).list_runs()

    @router.get("/backtest/runs/{run_id}", response_model=BacktestRun)
    def get_run(
        run_id: int,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        run = session.get(BacktestRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return run

    @router.get("/backtest/runs/{run_id}/trades", response_model=list[BacktestTrade])
    def list_trades(
        run_id: int,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        if session.get(BacktestRun, run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return BacktestRepo(session).trades_for_run(run_id)

    @router.get("/backtest/schedule")
    def get_schedule(_: None = Depends(api_key_dep)):
        if backtest_scheduler is None:
            raise HTTPException(status_code=503, detail="Backtest scheduler not available")
        return backtest_scheduler.get_status()

    @router.patch("/backtest/schedule")
    def patch_schedule(
        req: GlobalScheduleUpdate,
        _: None = Depends(api_key_dep),
    ):
        if backtest_scheduler is None:
            raise HTTPException(status_code=503, detail="Backtest scheduler not available")
        backtest_scheduler.update_global(
            schedule_enabled=req.schedule_enabled,
            cron=req.cron,
            lookback_days=req.lookback_days,
        )
        return backtest_scheduler.get_status()

    @router.get("/backtest/schedule/{model_id}")
    def get_model_schedule(model_id: str, _: None = Depends(api_key_dep)):
        if backtest_scheduler is None:
            raise HTTPException(status_code=503, detail="Backtest scheduler not available")
        return backtest_scheduler.get_model_status(model_id)

    @router.patch("/backtest/schedule/{model_id}")
    def patch_model_schedule(
        model_id: str,
        req: ModelScheduleUpdate,
        _: None = Depends(api_key_dep),
    ):
        if backtest_scheduler is None:
            raise HTTPException(status_code=503, detail="Backtest scheduler not available")
        backtest_scheduler.update_model(
            model_id,
            disabled=req.disabled,
            cron=req.cron,
            lookback_days=req.lookback_days,
        )
        return backtest_scheduler.get_model_status(model_id)

    return router
