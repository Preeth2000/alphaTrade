"""Integration test: OCO stop/limit orders submitted after BUY fill.

Runs offline — mocked T212 via respx, mocked yfinance fixture.
Requires alphaGen reference artifact at ../../alphaGen/artifacts/aapl_daily_mlp_example/
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from tests.integration.mock_t212.responses import mount

ALPHALINK_ROOT = Path(__file__).parent.parent.parent
ARTIFACT_DIR = ALPHALINK_ROOT.parent / "alphaGen" / "artifacts" / "aapl_daily_mlp_example"
FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _skip_if_missing():
    if not (ARTIFACT_DIR / "manifest.json").exists():
        pytest.skip("alphaGen reference artifact not found")
    try:
        import talib  # noqa: F401
    except ImportError:
        pytest.skip("TA-Lib not installed")


@respx.mock
def test_oco_orders_submitted_after_buy() -> None:
    """After a BUY fill, T212 stop and limit orders must both be submitted."""
    _skip_if_missing()
    mount(respx.mock)

    from alphalink.broker.t212_client import T212Client

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

    from alphalink.broker.t212_client import T212Client
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

    from alphalink.broker.t212_client import T212Client
    client = T212Client(api_key="test-key", env="demo")
    client.place_limit_order("AAPL_US_EQ", 2.0, 185.0)

    sent = json.loads(route.calls.last.request.content)
    assert sent["ticker"] == "AAPL_US_EQ"
    assert sent["quantity"] == -2.0   # negated for SELL convention
    assert sent["limitPrice"] == 185.0
    assert "stopPrice" not in sent
