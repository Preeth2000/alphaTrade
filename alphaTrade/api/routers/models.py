from __future__ import annotations
from collections.abc import Callable
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select
from alphaTrade.store.repos import (
    InstrumentCacheRepo,
    ModelPerformance,
    ModelPerformanceRepo,
    ModelOverrideRecord,
    ModelOverrideRepo,
    Signal,
)


class ModelOverrideUpdate(BaseModel):
    enabled: Optional[bool] = None
    broker_ticker: Optional[str] = None
    size_pct: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    cooldown_bars: Optional[int] = None


class ModelOverrideResponse(BaseModel):
    run_name: str
    enabled: Optional[bool] = None
    broker_ticker: Optional[str] = None
    size_pct: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    cooldown_bars: Optional[int] = None
    updated_at: Optional[datetime] = None
    resolved_ticker: Optional[str] = None
    """Effective broker ticker the tick loop will use. None = resolves at tick time via yaml/cache/API."""


class ModelSummary(BaseModel):
    run_name: str
    ticker: Optional[str] = None
    interval: Optional[str] = None
    model_arch: Optional[str] = None
    n_features: Optional[int] = None
    active: bool
    resolved_ticker: Optional[str] = None
    trade_count: int = 0
    win_count: int = 0
    rolling_pnl: float = 0.0
    retired: bool = False
    retired_at: Optional[datetime] = None
    last_updated: Optional[datetime] = None


def _resolve_ticker(session: Session, run_name: str, broker_ticker_override: Optional[str]) -> Optional[str]:
    if broker_ticker_override:
        return broker_ticker_override
    sig = session.exec(
        select(Signal).where(Signal.run_name == run_name).order_by(Signal.ts.desc()).limit(1)
    ).first()
    if sig:
        cached = InstrumentCacheRepo(session).get(sig.ticker)
        if cached:
            return cached.t212_ticker
    return None


def _to_override_response(record: ModelOverrideRecord, session: Session) -> ModelOverrideResponse:
    return ModelOverrideResponse(
        run_name=record.run_name,
        enabled=record.enabled,
        broker_ticker=record.broker_ticker,
        size_pct=record.size_pct,
        stop_loss_pct=record.stop_loss_pct,
        take_profit_pct=record.take_profit_pct,
        cooldown_bars=record.cooldown_bars,
        updated_at=record.updated_at,
        resolved_ticker=_resolve_ticker(session, record.run_name, record.broker_ticker),
    )


def make_router(session_dep: Callable, api_key_dep: Callable, registry=None) -> APIRouter:
    router = APIRouter()

    @router.get("/models", response_model=list[ModelSummary])
    def list_models(
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        perf_by_id: dict[str, ModelPerformance] = {
            p.model_id: p for p in ModelPerformanceRepo(session).all()
        }
        override_by_id: dict[str, ModelOverrideRecord] = {
            r.run_name: r for r in ModelOverrideRepo(session).all()
        }

        seen: set[str] = set()
        results: list[ModelSummary] = []

        # Active models from registry first
        if registry is not None:
            for run_name, (manifest, _) in registry.by_run_name.items():
                perf = perf_by_id.get(run_name)
                ov = override_by_id.get(run_name)
                results.append(ModelSummary(
                    run_name=run_name,
                    ticker=manifest.ticker,
                    interval=manifest.interval,
                    model_arch=manifest.model_arch,
                    n_features=manifest.n_features,
                    active=True,
                    resolved_ticker=_resolve_ticker(session, run_name, ov.broker_ticker if ov else None),
                    trade_count=perf.trade_count if perf else 0,
                    win_count=perf.win_count if perf else 0,
                    rolling_pnl=perf.rolling_pnl if perf else 0.0,
                    retired=perf.retired if perf else False,
                    retired_at=perf.retired_at if perf else None,
                    last_updated=perf.last_updated if perf else None,
                ))
                seen.add(run_name)

        # DB-only models (removed from disk or retired) not in registry
        for run_name, perf in perf_by_id.items():
            if run_name not in seen:
                ov = override_by_id.get(run_name)
                results.append(ModelSummary(
                    run_name=run_name,
                    active=False,
                    resolved_ticker=_resolve_ticker(session, run_name, ov.broker_ticker if ov else None),
                    trade_count=perf.trade_count,
                    win_count=perf.win_count,
                    rolling_pnl=perf.rolling_pnl,
                    retired=perf.retired,
                    retired_at=perf.retired_at,
                    last_updated=perf.last_updated,
                ))

        return results

    @router.get("/models/{run_name}/overrides", response_model=ModelOverrideResponse)
    def get_overrides(
        run_name: str,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        record = ModelOverrideRepo(session).get(run_name) or ModelOverrideRecord(run_name=run_name)
        return _to_override_response(record, session)

    @router.put("/models/{run_name}/overrides", response_model=ModelOverrideResponse)
    def set_overrides(
        run_name: str,
        update: ModelOverrideUpdate,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        repo = ModelOverrideRepo(session)
        existing = repo.get(run_name) or ModelOverrideRecord(run_name=run_name)
        for field, val in update.model_dump(exclude_unset=True).items():
            setattr(existing, field, val)
        saved = repo.upsert(existing)
        return _to_override_response(saved, session)

    @router.delete("/models/{run_name}/overrides")
    def delete_overrides(
        run_name: str,
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        deleted = ModelOverrideRepo(session).delete(run_name)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"No overrides found for {run_name!r}")
        return {"deleted": True, "run_name": run_name}

    return router
