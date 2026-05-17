from __future__ import annotations
import asyncio
import logging
import time
from sqlalchemy.engine import Engine
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from alphaTrade.api.auth import make_api_key_dep
from alphaTrade.api.deps import make_session_dep
from alphaTrade.health import HealthState

log = logging.getLogger(__name__)


def create_app(engine: Engine, health_state: HealthState, registry=None, backtest_scheduler=None) -> FastAPI:
    from alphaTrade.api.routers import positions, orders, signals, pnl, models, backtest, health, settings, equity, trades, stream, kill_switch, verify

    app = FastAPI(title="alphaTrade API", version="1.0")

    @app.middleware("http")
    async def _log_requests(request: Request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        ms = (time.perf_counter() - t0) * 1000
        log.info("%s %s %d %.1fms", request.method, request.url.path, response.status_code, ms)
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "PUT", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    session_dep = make_session_dep(engine)
    api_key_dep = make_api_key_dep(engine)

    app.include_router(positions.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(orders.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(signals.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(pnl.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(models.make_router(session_dep, api_key_dep, registry), prefix="/api/v1")
    app.include_router(backtest.make_router(session_dep, api_key_dep, backtest_scheduler), prefix="/api/v1")
    app.include_router(health.make_router(health_state, api_key_dep), prefix="/api/v1")
    app.include_router(settings.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(equity.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(trades.make_router(session_dep, api_key_dep), prefix="/api/v1")
    app.include_router(stream.make_router(engine, api_key_dep), prefix="/api/v1")
    app.include_router(kill_switch.make_router(api_key_dep), prefix="/api/v1")
    app.include_router(verify.make_router(api_key_dep), prefix="/api/v1")

    return app


async def start_api_server(
    engine: Engine,
    health_state: HealthState,
    port: int = 8081,
    registry=None,
    backtest_scheduler=None,
) -> uvicorn.Server:
    app = create_app(engine, health_state, registry, backtest_scheduler)
    config = uvicorn.Config(app, host="0.0.0.0", port=port, loop="none", log_level="info")
    server = uvicorn.Server(config)
    asyncio.create_task(server.serve())
    log.info("API server listening on :%d", port)
    return server
