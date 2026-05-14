from __future__ import annotations
import logging
from sqlalchemy.engine import Engine
from fastapi import FastAPI
from alphalink.api.auth import make_api_key_dep
from alphalink.api.deps import make_session_dep
from alphalink.health import HealthState

log = logging.getLogger(__name__)


def create_app(engine: Engine, health_state: HealthState) -> FastAPI:
    from alphalink.api.routers import positions, orders, signals, health

    app = FastAPI(title="alphaLink API", version="1.0")
    session_dep = make_session_dep(engine)
    api_key_dep = make_api_key_dep(engine)

    app.include_router(positions.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(orders.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(signals.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(health.make_router(health_state, api_key_dep), prefix="/api/v1")

    return app
