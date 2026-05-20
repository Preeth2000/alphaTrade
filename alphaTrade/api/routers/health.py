from __future__ import annotations
from collections.abc import Callable
from fastapi import APIRouter, Depends
from alphaTrade.health import HealthState


def make_router(health_state: HealthState, api_key_dep: Callable) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    def get_health(_: None = Depends(api_key_dep)):
        return {
            "last_tick_at": (
                health_state.last_tick_at.isoformat()
                if health_state.last_tick_at else None
            ),
            "t212_ok": health_state.t212_ok,
            "t212_configured": health_state.t212_configured,
            "trading_ready": health_state.t212_ok and health_state.t212_configured and health_state.models_loaded,
            "models_loaded": health_state.models_loaded,
            "longest_interval_seconds": health_state.longest_interval_seconds,
        }

    return router
