from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from aiohttp import web

log = logging.getLogger(__name__)


@dataclass
class HealthState:
    last_tick_at: datetime | None = None
    longest_interval_seconds: int = 3600
    t212_ok: bool = False
    models_loaded: bool = False


def make_app(state: HealthState) -> web.Application:
    app = web.Application()

    async def healthz(request: web.Request) -> web.Response:
        if state.last_tick_at is None:
            return web.Response(status=503, text="no tick yet")
        now = datetime.now(timezone.utc)
        age = (now - state.last_tick_at).total_seconds()
        threshold = state.longest_interval_seconds * 2
        if age > threshold:
            return web.Response(status=503, text=f"stale: {age:.0f}s > {threshold:.0f}s")
        return web.Response(status=200, text="ok")

    async def readyz(request: web.Request) -> web.Response:
        if not state.models_loaded:
            return web.Response(status=503, text="no models loaded")
        if not state.t212_ok:
            return web.Response(status=503, text="t212 unreachable")
        return web.Response(status=200, text="ok")

    app.router.add_get("/healthz", healthz)
    app.router.add_get("/readyz", readyz)
    return app


async def start_health_server(state: HealthState, port: int = 8080) -> web.AppRunner:
    app = make_app(state)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("Health server listening on :%d", port)
    return runner
