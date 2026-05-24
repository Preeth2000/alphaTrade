"""Async Redis client factory."""
from __future__ import annotations

import logging

import redis.asyncio as aioredis

from alphaTrade.config import RedisConfig

log = logging.getLogger(__name__)


async def create_redis(cfg: RedisConfig) -> aioredis.Redis | None:
    if not cfg.enabled:
        log.info("Redis disabled via config")
        return None
    client: aioredis.Redis = aioredis.from_url(cfg.url, decode_responses=True)
    try:
        await client.ping()
        log.info("Redis connected: %s", cfg.url)
    except Exception as exc:
        log.warning("Redis unavailable (%s) — falling back to in-process mode", exc)
        await client.aclose()
        return None
    return client
