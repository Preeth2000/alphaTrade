from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from alphaTrade.store.repos import BacktestRun, BacktestTrade, BacktestRepo


class TriggerRequest(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None
    model_id: Optional[str] = None


class TriggerResponse(BaseModel):
    run_id: int
    status: str


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
        end = req.end or date.today().isoformat()
        if req.start:
            start = req.start
        else:
            try:
                lookback = int(backtest_scheduler._settings.backtest.lookback_days)
            except (AttributeError, TypeError, ValueError):
                lookback = 90
            start = (date.today() - timedelta(days=lookback)).isoformat()
        run_id = await backtest_scheduler.trigger(
            session=session,
            start=start,
            end=end,
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

    return router
