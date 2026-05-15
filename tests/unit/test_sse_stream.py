"""Tests for SSE streaming endpoint and stream_bus pub/sub."""
from __future__ import annotations
import asyncio
import json
import pytest
from fastapi.testclient import TestClient
from sqlmodel import create_engine, Session
from alphaTrade.store.db import run_migrations
from alphaTrade.health import HealthState


def _engine(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(db)
    return create_engine(f"sqlite:///{db}")


def _client(engine, health_state=None):
    from alphaTrade.api.app import create_app
    return TestClient(create_app(engine, health_state or HealthState()))


# ---------------------------------------------------------------------------
# stream_bus unit tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_bus():
    """Reset stream_bus subscriber set between tests."""
    from alphaTrade.api import stream_bus
    stream_bus._subscribers.clear()
    yield
    stream_bus._subscribers.clear()


@pytest.mark.asyncio
async def test_publish_delivers_to_subscriber():
    from alphaTrade.api import stream_bus
    q = stream_bus.subscribe()
    stream_bus.publish({"type": "tick_complete", "interval": "1m"})
    event = q.get_nowait()
    assert event["type"] == "tick_complete"
    assert event["interval"] == "1m"


@pytest.mark.asyncio
async def test_multiple_subscribers_both_receive():
    from alphaTrade.api import stream_bus
    q1 = stream_bus.subscribe()
    q2 = stream_bus.subscribe()
    stream_bus.publish({"type": "signal_fired", "ticker": "AAPL"})
    assert q1.get_nowait()["ticker"] == "AAPL"
    assert q2.get_nowait()["ticker"] == "AAPL"


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery():
    from alphaTrade.api import stream_bus
    q = stream_bus.subscribe()
    stream_bus.unsubscribe(q)
    stream_bus.publish({"type": "tick_complete"})
    assert q.empty()


# ---------------------------------------------------------------------------
# HTTP endpoint tests (async httpx client against ASGI app)
# ---------------------------------------------------------------------------

def test_stream_route_registered(tmp_path):
    from alphaTrade.api.app import create_app
    app = create_app(_engine(tmp_path), HealthState())
    paths = {route.path for route in app.routes}
    assert "/api/v1/stream" in paths


@pytest.mark.asyncio
async def test_stream_returns_text_event_stream(tmp_path):
    """StreamingResponse from /stream endpoint has text/event-stream media type."""
    from alphaTrade.api.routers.stream import make_router
    engine = _engine(tmp_path)
    router = make_router(engine, lambda: None)
    stream_route = next(r for r in router.routes if r.path == "/stream")
    response = await stream_route.endpoint(key="")
    assert response.media_type == "text/event-stream"
    await response.body_iterator.aclose()


def test_stream_auth_rejects_bad_key(tmp_path, monkeypatch):
    monkeypatch.setenv("alphaTrade_API_KEY", "secret")
    client = _client(_engine(tmp_path))
    resp = client.get("/api/v1/stream?key=wrong")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_stream_auth_no_key_configured_allows_all(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from alphaTrade.api.routers.stream import make_router
    monkeypatch.delenv("alphaTrade_API_KEY", raising=False)
    engine = _engine(tmp_path)
    router = make_router(engine, lambda: None)
    stream_route = next(r for r in router.routes if r.path == "/stream")
    # Should not raise
    response = await stream_route.endpoint(key="")
    assert response.status_code == 200
    await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_stream_auth_correct_key_passes(tmp_path, monkeypatch):
    from alphaTrade.api.routers.stream import make_router
    monkeypatch.setenv("alphaTrade_API_KEY", "secret")
    engine = _engine(tmp_path)
    router = make_router(engine, lambda: None)
    stream_route = next(r for r in router.routes if r.path == "/stream")
    response = await stream_route.endpoint(key="secret")
    assert response.status_code == 200
    await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_stream_emits_signal_fired_event(tmp_path):
    """Events published to stream_bus appear as SSE data frames in the generator."""
    from alphaTrade.api import stream_bus
    from alphaTrade.api.routers.stream import make_router

    engine = _engine(tmp_path)
    router = make_router(engine, lambda: None)
    stream_route = next(r for r in router.routes if r.path == "/stream")
    response = await stream_route.endpoint(key="")

    received = []
    gen = response.body_iterator

    # Consume ping frame
    ping = await gen.__anext__()
    assert ping.startswith(":")

    # Publish and consume event
    stream_bus.publish({"type": "signal_fired", "ticker": "TSLA", "signal": "BUY"})
    frame = await gen.__anext__()
    assert frame.startswith("data:")
    received.append(json.loads(frame[5:].strip()))

    await gen.aclose()

    assert received[0]["type"] == "signal_fired"
    assert received[0]["ticker"] == "TSLA"
