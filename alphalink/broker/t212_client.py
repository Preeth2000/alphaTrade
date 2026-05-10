"""Trading212 HTTP client. Auth via T212_API_KEY header. demo/live via T212_ENV."""
from __future__ import annotations

from typing import Any

import httpx


_BASE_URLS = {
    "demo": "https://demo.trading212.com/api/v0",
    "live": "https://live.trading212.com/api/v0",
}


class T212Client:
    def __init__(self, api_key: str, env: str = "demo") -> None:
        if env not in _BASE_URLS:
            raise ValueError(f"T212_ENV must be 'demo' or 'live', got {env!r}")
        self._base = _BASE_URLS[env]
        self._headers = {"Authorization": api_key}

    def _get(self, path: str, **params: Any) -> Any:
        url = f"{self._base}{path}"
        r = httpx.get(url, headers=self._headers, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        url = f"{self._base}{path}"
        r = httpx.post(url, headers=self._headers, json=body, timeout=30)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str) -> None:
        url = f"{self._base}{path}"
        r = httpx.delete(url, headers=self._headers, timeout=30)
        r.raise_for_status()

    def get_account_summary(self) -> dict[str, Any]:
        """Returns account summary including cash and totalValue."""
        return self._get("/equity/account/summary")

    def get_total_equity(self) -> float:
        """Return total portfolio value (cash + positions)."""
        summary = self.get_account_summary()
        # T212 response: {"cash": {"availableToTrade": ..., ...}, "totalValue": ...}
        return float(summary.get("totalValue", summary.get("cash", {}).get("availableToTrade", 0)))

    def get_positions(self) -> list[dict[str, Any]]:
        """Returns list of open positions."""
        return self._get("/equity/positions")

    def get_instruments(self) -> list[dict[str, Any]]:
        return self._get("/equity/metadata/instruments")

    def place_market_order(
        self,
        instrument_ticker: str,
        quantity: float,
    ) -> dict[str, Any]:
        """Submit MARKET order. T212 API does not support SL/TP on market orders."""
        body: dict[str, Any] = {
            "ticker": instrument_ticker,
            "quantity": quantity,
        }
        return self._post("/equity/orders/market", body)

    def place_stop_order(
        self,
        instrument_ticker: str,
        quantity: float,
        stop_price: float,
    ) -> dict[str, Any]:
        """Submit STOP SELL order. Used as SL leg of OCO after a BUY fill."""
        body: dict[str, Any] = {
            "ticker": instrument_ticker,
            "quantity": quantity,
            "stopPrice": stop_price,
        }
        return self._post("/equity/orders/stop", body)

    def place_limit_order(
        self,
        instrument_ticker: str,
        quantity: float,
        limit_price: float,
    ) -> dict[str, Any]:
        """Submit LIMIT SELL order. Used as TP leg of OCO after a BUY fill."""
        body: dict[str, Any] = {
            "ticker": instrument_ticker,
            "quantity": quantity,
            "limitPrice": limit_price,
        }
        return self._post("/equity/orders/limit", body)

    def get_order(self, order_id: str) -> dict[str, Any]:
        """Fetch order status by T212 order ID."""
        return self._get(f"/equity/orders/{order_id}")

    def cancel_order(self, order_id: str) -> None:
        """Cancel an open order by ID. Raises HTTPStatusError on failure."""
        self._delete(f"/equity/orders/{order_id}")
