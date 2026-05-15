from __future__ import annotations
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session
from alphaTrade.store.repos import EquityCurve, EquityRepo


def make_router(session_dep: Callable, api_key_dep: Callable) -> APIRouter:
    router = APIRouter()

    @router.get("/equity-curve", response_model=list[EquityCurve])
    def get_equity_curve(
        since: Optional[datetime] = Query(default=None),
        limit: int = Query(default=500, ge=1, le=5000),
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        cutoff = since if since is not None else datetime.utcnow() - timedelta(days=30)
        return EquityRepo(session).since(cutoff, limit)

    return router
