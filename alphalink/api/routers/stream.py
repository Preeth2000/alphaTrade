from __future__ import annotations
import asyncio
import json
import os
from collections.abc import AsyncGenerator, Callable
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.engine import Engine
from sqlmodel import Session


def make_router(engine: Engine, api_key_dep: Callable) -> APIRouter:
    from alphalink.api import stream_bus

    router = APIRouter()

    def _check_key(key: str) -> None:
        from alphalink.store.repos import BotSettingsRepo
        with Session(engine) as s:
            db_s = BotSettingsRepo(s).get()
        active_key = (db_s.alphalink_api_key if db_s else "") or os.environ.get("ALPHALINK_API_KEY", "")
        if not active_key:
            return
        if key != active_key:
            raise HTTPException(status_code=403, detail="Invalid API key")

    @router.get("/stream")
    async def stream_events(key: str = Query(default="")):
        _check_key(key)

        async def generate() -> AsyncGenerator[str, None]:
            q = stream_bus.subscribe()
            try:
                yield ": ping\n\n"  # flush headers immediately
                while True:
                    event = await q.get()
                    yield f"data: {json.dumps(event)}\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                stream_bus.unsubscribe(q)

        return StreamingResponse(generate(), media_type="text/event-stream")

    return router
