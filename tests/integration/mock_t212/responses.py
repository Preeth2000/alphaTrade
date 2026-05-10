"""respx mock handlers for Trading212 API endpoints."""
from __future__ import annotations

import re

import respx
import httpx

DEMO_BASE = "https://demo.trading212.com/api/v0"

ACCOUNT_SUMMARY = {
    "cash": {"availableToTrade": 9000.0, "reservedForOrders": 0.0},
    "totalValue": 10000.0,
}

POSITIONS: list[dict] = []  # no open positions by default

INSTRUMENTS = [
    {"ticker": "AAPL_US_EQ", "shortName": "AAPL", "name": "Apple Inc.", "type": "STOCK"},
    {"ticker": "MSFT_US_EQ", "shortName": "MSFT", "name": "Microsoft Corp.", "type": "STOCK"},
]

ORDER_RESPONSE = {
    "id": "order-001",
    "ticker": "AAPL_US_EQ",
    "quantity": 1.0,
    "fillPrice": 175.50,
    "status": "FILLED",
}

STOP_ORDER_RESPONSE = {
    "id": "stop-001",
    "ticker": "AAPL_US_EQ",
    "status": "PENDING",
}

LIMIT_ORDER_RESPONSE = {
    "id": "limit-001",
    "ticker": "AAPL_US_EQ",
    "status": "PENDING",
}

ORDER_STATUS_FILLED = {"id": "stop-001", "status": "FILLED"}
ORDER_STATUS_PENDING = {"id": "limit-001", "status": "PENDING"}


def mount(router: respx.MockRouter) -> None:
    """Register all T212 mock routes on a respx router."""
    router.get(f"{DEMO_BASE}/equity/account/summary").mock(
        return_value=httpx.Response(200, json=ACCOUNT_SUMMARY)
    )
    router.get(f"{DEMO_BASE}/equity/positions").mock(
        return_value=httpx.Response(200, json=POSITIONS)
    )
    router.get(f"{DEMO_BASE}/equity/metadata/instruments").mock(
        return_value=httpx.Response(200, json=INSTRUMENTS)
    )
    router.post(f"{DEMO_BASE}/equity/orders/market").mock(
        return_value=httpx.Response(200, json=ORDER_RESPONSE)
    )
    router.post(f"{DEMO_BASE}/equity/orders/stop").mock(
        return_value=httpx.Response(200, json=STOP_ORDER_RESPONSE)
    )
    router.post(f"{DEMO_BASE}/equity/orders/limit").mock(
        return_value=httpx.Response(200, json=LIMIT_ORDER_RESPONSE)
    )
    router.get(f"{DEMO_BASE}/equity/orders/stop-001").mock(
        return_value=httpx.Response(200, json=ORDER_STATUS_FILLED)
    )
    router.get(f"{DEMO_BASE}/equity/orders/limit-001").mock(
        return_value=httpx.Response(200, json=ORDER_STATUS_PENDING)
    )
    router.delete(url__regex=rf"^{re.escape(DEMO_BASE)}/equity/orders/[^/?]+$").mock(
        return_value=httpx.Response(204)
    )
