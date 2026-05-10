"""Integration test: OCO stop/limit orders submitted after BUY fill.

Runs offline — mocked T212 via respx.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from alphalink.broker.t212_client import T212Client
from tests.integration.mock_t212.responses import mount


@respx.mock
def test_oco_client_endpoints_via_mock() -> None:
    """Verify T212Client places stop/limit orders and cancels via mock endpoints."""
    mount(respx.mock)

    client = T212Client(api_key="test-key", env="demo")

    # Simulate a filled BUY at 175.50 with 2% SL and 5% TP
    fill_price = 175.50
    qty = 1.0
    sl_price = fill_price * 0.98   # 171.99
    tp_price = fill_price * 1.05   # 184.275

    stop_resp = client.place_stop_order("AAPL_US_EQ", qty, sl_price)
    limit_resp = client.place_limit_order("AAPL_US_EQ", qty, tp_price)

    assert stop_resp["id"] == "stop-001"
    assert stop_resp["status"] == "PENDING"
    assert limit_resp["id"] == "limit-001"
    assert limit_resp["status"] == "PENDING"

    # Verify get_order and cancel_order also work via mock
    stop_status = client.get_order("stop-001")
    assert stop_status["status"] == "FILLED"

    client.cancel_order("limit-001")  # must not raise


@respx.mock
def test_stop_order_body_sent_correctly() -> None:
    """Verify request body contains correct keys and negated quantity."""
    route = respx.post("https://demo.trading212.com/api/v0/equity/orders/stop").mock(
        return_value=httpx.Response(200, json={"id": "stop-001", "status": "PENDING"})
    )

    client = T212Client(api_key="test-key", env="demo")
    client.place_stop_order("AAPL_US_EQ", 2.0, 170.0)

    sent = json.loads(route.calls.last.request.content)
    assert sent["ticker"] == "AAPL_US_EQ"
    assert sent["quantity"] == -2.0   # negated for SELL convention
    assert sent["stopPrice"] == 170.0
    assert "limitPrice" not in sent


@respx.mock
def test_limit_order_body_sent_correctly() -> None:
    """Verify request body contains correct keys and negated quantity."""
    route = respx.post("https://demo.trading212.com/api/v0/equity/orders/limit").mock(
        return_value=httpx.Response(200, json={"id": "limit-001", "status": "PENDING"})
    )

    client = T212Client(api_key="test-key", env="demo")
    client.place_limit_order("AAPL_US_EQ", 2.0, 185.0)

    sent = json.loads(route.calls.last.request.content)
    assert sent["ticker"] == "AAPL_US_EQ"
    assert sent["quantity"] == -2.0   # negated for SELL convention
    assert sent["limitPrice"] == 185.0
    assert "stopPrice" not in sent


@respx.mock
def test_cancel_order_after_stop_submitted() -> None:
    """cancel_order works correctly via mock — used for orphaned-leg cleanup."""
    DEMO_BASE_URL = "https://demo.trading212.com/api/v0"
    respx.post(f"{DEMO_BASE_URL}/equity/orders/stop").mock(
        return_value=httpx.Response(200, json={"id": "stop-orphan", "status": "PENDING"})
    )
    respx.delete(
        url__regex=rf"^https://demo\.trading212\.com/api/v0/equity/orders/[^/?]+$"
    ).mock(return_value=httpx.Response(204))

    client = T212Client(api_key="test-key", env="demo")
    stop_resp = client.place_stop_order("AAPL_US_EQ", 1.0, 170.0)
    assert stop_resp["id"] == "stop-orphan"
    client.cancel_order(stop_resp["id"])  # must not raise — cleanup of orphaned leg
