from __future__ import annotations
from collections.abc import Callable
from fastapi import APIRouter, Depends
from sqlmodel import Session
from alphaTrade.store.repos import Position, PositionRepo


def make_router(session_dep: Callable, api_key_dep: Callable) -> APIRouter:
    router = APIRouter()

    @router.get("/positions", response_model=list[Position])
    def list_positions(
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        return PositionRepo(session).all()

    return router
