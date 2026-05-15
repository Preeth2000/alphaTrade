from __future__ import annotations
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session
from alphaTrade.store.repos import TradeJournal, TradeJournalRepo


def make_router(session_dep: Callable, api_key_dep: Callable) -> APIRouter:
    router = APIRouter()

    @router.get("/trades", response_model=list[TradeJournal])
    def list_trades(
        since: Optional[datetime] = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        model_id: Optional[str] = Query(default=None),
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        cutoff = since if since is not None else datetime.utcnow() - timedelta(days=30)
        repo = TradeJournalRepo(session)
        if model_id:
            return repo.by_model(model_id, limit)
        return repo.since(cutoff)[:limit]

    return router
