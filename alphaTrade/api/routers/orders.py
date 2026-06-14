from __future__ import annotations
from collections.abc import Callable
from datetime import datetime, timedelta
from alphaTrade.utils import utcnow
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session
from alphaTrade.store.repos import Order, OrderRepo


def make_router(session_dep: Callable, api_key_dep: Callable) -> APIRouter:
    router = APIRouter()

    @router.get("/orders", response_model=list[Order])
    def list_orders(
        since: Optional[datetime] = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        cutoff = since if since is not None else utcnow() - timedelta(hours=24)
        return OrderRepo(session).since(cutoff, limit)

    return router
