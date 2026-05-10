from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from aiohttp.test_utils import TestClient, TestServer

from alphalink.health import HealthState, make_app


@pytest.fixture
async def health_client():
    state = HealthState()
    app = make_app(state)
    client = TestClient(TestServer(app))
    await client.start_server()
    yield client, state
    await client.close()


async def test_healthz_no_tick(health_client):
    client, _ = health_client
    resp = await client.get("/healthz")
    assert resp.status == 503
    assert "no tick yet" in await resp.text()


async def test_healthz_stale(health_client):
    client, state = health_client
    state.last_tick_at = datetime.now(timezone.utc) - timedelta(seconds=7201)
    state.longest_interval_seconds = 3600
    resp = await client.get("/healthz")
    assert resp.status == 503
    assert "stale" in await resp.text()


async def test_healthz_ok(health_client):
    client, state = health_client
    state.last_tick_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    state.longest_interval_seconds = 3600
    resp = await client.get("/healthz")
    assert resp.status == 200
    assert await resp.text() == "ok"


async def test_readyz_no_models(health_client):
    client, state = health_client
    state.t212_ok = True
    resp = await client.get("/readyz")
    assert resp.status == 503
    assert "no models loaded" in await resp.text()


async def test_readyz_t212_down(health_client):
    client, state = health_client
    state.models_loaded = True
    state.t212_ok = False
    resp = await client.get("/readyz")
    assert resp.status == 503
    assert "t212 unreachable" in await resp.text()


async def test_readyz_ok(health_client):
    client, state = health_client
    state.models_loaded = True
    state.t212_ok = True
    resp = await client.get("/readyz")
    assert resp.status == 200
    assert await resp.text() == "ok"
