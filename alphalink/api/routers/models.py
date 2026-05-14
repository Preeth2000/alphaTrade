from __future__ import annotations
from collections.abc import Callable
from fastapi import APIRouter, Depends
from sqlmodel import Session
from alphalink.store.repos import ModelPerformance, ModelPerformanceRepo


def make_router(session_dep: Callable, api_key_dep: Callable) -> APIRouter:
    router = APIRouter()

    @router.get("/models", response_model=list[ModelPerformance])
    def list_models(
        session: Session = Depends(session_dep),
        _: None = Depends(api_key_dep),
    ):
        return ModelPerformanceRepo(session).all()

    return router
