from __future__ import annotations
from collections.abc import Callable
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session
from alphaTrade.store.repos import BacktestRun, BacktestTrade, BacktestRepo


def make_router(session_dep: Callable, api_key_dep: Callable) -> APIRouter:
    router = APIRouter()

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
