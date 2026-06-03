from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from aiohttp import web

log = logging.getLogger(__name__)


@dataclass
class HealthState:
    # Not thread-safe: mutate only from the asyncio event loop thread.
    # longest_interval_seconds must be set before the first tick.
    last_tick_at: datetime | None = None
    longest_interval_seconds: int = 3600
    t212_ok: bool = False
    t212_configured: bool = False
    models_loaded: bool = False
    provider_ok: bool | None = None
    provider_name: str | None = None
    alphatrade_ok: bool | None = None


def _cors_headers() -> dict:
    return {"Access-Control-Allow-Origin": "*"}


def make_app(state: HealthState) -> web.Application:
    app = web.Application()

    async def healthz(request: web.Request) -> web.Response:
        if state.last_tick_at is None:
            return web.Response(status=503, text="no tick yet", headers=_cors_headers())
        now = datetime.now(timezone.utc)
        age = (now - state.last_tick_at).total_seconds()
        threshold = state.longest_interval_seconds * 2
        if age > threshold:
            return web.Response(status=503, text=f"stale: {age:.0f}s > {threshold:.0f}s", headers=_cors_headers())
        return web.Response(status=200, text="ok", headers=_cors_headers())

    async def readyz(request: web.Request) -> web.Response:
        return web.Response(status=200, text="ok", headers=_cors_headers())

    app.router.add_get("/healthz", healthz)
    app.router.add_get("/readyz", readyz)
    return app


async def start_health_server(state: HealthState, port: int = 8080) -> web.AppRunner:
    app = make_app(state)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    try:
        await site.start()
    except Exception:
        await runner.cleanup()
        raise
    log.info("Health server listening on :%d", port)
    return runner
