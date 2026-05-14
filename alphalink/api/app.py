from __future__ import annotations
import asyncio
import logging
from sqlalchemy.engine import Engine
from fastapi import FastAPI
import uvicorn
from alphalink.api.auth import make_api_key_dep
from alphalink.api.deps import make_session_dep
from alphalink.health import HealthState

log = logging.getLogger(__name__)


def create_app(engine: Engine, health_state: HealthState) -> FastAPI:
    from alphalink.api.routers import positions, orders, signals, pnl, models, backtest, health, settings

    app = FastAPI(title="alphaLink API", version="1.0")
    session_dep = make_session_dep(engine)
    api_key_dep = make_api_key_dep(engine)

    app.include_router(positions.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(orders.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(signals.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(pnl.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(models.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(backtest.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(health.make_router(health_state, api_key_dep), prefix="/api/v1")
    app.include_router(settings.make_router(session_dep, api_key_dep), prefix="/api/v1")

    return app


async def start_api_server(
    engine: Engine,
    health_state: HealthState,
    port: int = 8081,
) -> uvicorn.Server:
    app = create_app(engine, health_state)
    config = uvicorn.Config(app, host="0.0.0.0", port=port, loop="none", log_level="warning")
    server = uvicorn.Server(config)
    asyncio.create_task(server.serve())
    log.info("API server listening on :%d", port)
    return server
