from __future__ import annotations
from collections.abc import Callable
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session
from alphalink.store.repos import PnlSnapshot, PnlSnapshotRepo


def make_router(session_dep: Callable, api_key_dep: Callable) -> APIRouter:
    router = APIRouter()

    @router.get("/pnl", response_model=list[PnlSnapshot])
    def list_pnl(
        since: Optional[str] = Query(default=None, description="YYYY-MM-DD"),
        limit: int = Query(default=100, ge=1, le=1000),
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        rows = PnlSnapshotRepo(session).since(since or "0000-01-01")
        return rows[:limit]

    return router
