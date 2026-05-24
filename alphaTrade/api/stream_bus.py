"""Module-level pub/sub for SSE event broadcasting.

In-process mode (default): events go directly to subscriber queues.
Redis mode: events published to Redis channel; a background listener
fans them out to local queues. Enables multi-replica SSE.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

log = logging.getLogger(__name__)

CHANNEL = "alphaTrade:stream"

_subscribers: set[asyncio.Queue] = set()
_redis: Any = None  # redis.asyncio.Redis | None
_listener_task: asyncio.Task | None = None


def configure(redis_client: Any) -> None:
    """Wire in a Redis client. Call once at startup from the running event loop."""
    global _redis, _listener_task
    _redis = redis_client
    if _redis is not None:
        _listener_task = asyncio.get_event_loop().create_task(_redis_listener())
        log.info("stream_bus: Redis pub/sub enabled on channel '%s'", CHANNEL)


async def shutdown() -> None:
    """Cancel background listener and close pubsub. Call at graceful shutdown."""
    global _listener_task
    if _listener_task is not None:
        _listener_task.cancel()
        try:
            await _listener_task
        except asyncio.CancelledError:
            pass
        _listener_task = None


async def _redis_listener() -> None:
    pubsub = _redis.pubsub()
    await pubsub.subscribe(CHANNEL)
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                event = json.loads(message["data"])
            except (ValueError, TypeError):
                continue
            for q in _subscribers:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass
    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.unsubscribe(CHANNEL)
        await pubsub.aclose()


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def publish(event: dict[str, Any]) -> None:
    if _redis is not None:
        asyncio.get_event_loop().create_task(
            _redis.publish(CHANNEL, json.dumps(event))
        )
    else:
        for q in _subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
