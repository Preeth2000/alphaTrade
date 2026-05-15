"""Tests for T212Client stop/limit/get_order/cancel_order methods."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from alphaTrade.broker.t212_client import T212Client

DEMO_BASE = "https://demo.trading212.com/api/v0"


@pytest.fixture
def client() -> T212Client:
    return T212Client(api_key="test-key", env="demo")


@respx.mock
def test_place_stop_order(client: T212Client) -> None:
    route = respx.post(f"{DEMO_BASE}/equity/orders/stop").mock(
        return_value=httpx.Response(200, json={"id": "stop-001", "status": "PENDING"})
    )
    result = client.place_stop_order("AAPL_US_EQ", 1.0, 170.0)
    assert result["id"] == "stop-001"
    assert result["status"] == "PENDING"
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"ticker": "AAPL_US_EQ", "quantity": -1.0, "stopPrice": 170.0}


@respx.mock
def test_place_limit_order(client: T212Client) -> None:
    route = respx.post(f"{DEMO_BASE}/equity/orders/limit").mock(
        return_value=httpx.Response(200, json={"id": "limit-001", "status": "PENDING"})
    )
    result = client.place_limit_order("AAPL_US_EQ", 1.0, 185.0)
    assert result["id"] == "limit-001"
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"ticker": "AAPL_US_EQ", "quantity": -1.0, "limitPrice": 185.0}


@respx.mock
def test_get_order(client: T212Client) -> None:
    respx.get(f"{DEMO_BASE}/equity/orders/stop-001").mock(
        return_value=httpx.Response(200, json={"id": "stop-001", "status": "FILLED"})
    )
    result = client.get_order("stop-001")
    assert result["status"] == "FILLED"


@respx.mock
def test_cancel_order_does_not_raise(client: T212Client) -> None:
    respx.delete(f"{DEMO_BASE}/equity/orders/stop-001").mock(
        return_value=httpx.Response(204)
    )
    client.cancel_order("stop-001")  # must not raise


@respx.mock
def test_cancel_order_raises_on_non_2xx(client: T212Client) -> None:
    respx.delete(f"{DEMO_BASE}/equity/orders/stop-001").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        client.cancel_order("stop-001")
