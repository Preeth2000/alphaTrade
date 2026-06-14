"""Trading212 HTTP client. Auth via HTTP Basic Auth (api_key:secret_key). demo/live via T212_ENV."""
from __future__ import annotations

import time
from typing import Any

import httpx

from alphaTrade.metrics import t212_request_latency_seconds, t212_requests_total

_BASE_URLS = {
    "demo": "https://demo.trading212.com/api/v0",
    "live": "https://live.trading212.com/api/v0",
}

_MAX_RETRIES = 3
_DEFAULT_429_SLEEP = 60
_NO_RETRY_CODES = {401, 403}


def _handle_429(response: httpx.Response, attempt: int) -> None:
    if attempt >= _MAX_RETRIES:
        response.raise_for_status()
    try:
        sleep_secs = int(response.headers.get("Retry-After", _DEFAULT_429_SLEEP))
    except (ValueError, TypeError):
        sleep_secs = _DEFAULT_429_SLEEP
    time.sleep(sleep_secs)


class T212Client:
    QUANTITY_PRECISION = 4  # T212 rejects quantities with more than 4 decimal places

    def __init__(self, api_key: str, secret_key: str = "", env: str = "demo") -> None:
        if env not in _BASE_URLS:
            raise ValueError(f"T212_ENV must be 'demo' or 'live', got {env!r}")
        self._api_key = api_key
        self._env = env
        self._base = _BASE_URLS[env]
        _auth = httpx.BasicAuth(api_key, secret_key) if secret_key else None
        _headers = {} if secret_key else {"Authorization": api_key}
        self._http = httpx.Client(
            auth=_auth,
            headers=_headers,
            timeout=30,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "T212Client":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get(self, path: str, *, route: str | None = None, **params: Any) -> Any:
        url = f"{self._base}{path}"
        _route = route or path
        for attempt in range(1, _MAX_RETRIES + 1):
            _t0 = time.perf_counter()
            try:
                r = self._http.get(url, params=params)
                if r.status_code == 429:
                    t212_requests_total.labels(endpoint=_route, status="429").inc()
                    t212_request_latency_seconds.labels(endpoint=_route).observe(time.perf_counter() - _t0)
                    _handle_429(r, attempt)
                    continue
                r.raise_for_status()
                t212_requests_total.labels(endpoint=_route, status=str(r.status_code)).inc()
                t212_request_latency_seconds.labels(endpoint=_route).observe(time.perf_counter() - _t0)
                return r.json()
            except httpx.HTTPStatusError as exc:
                t212_requests_total.labels(endpoint=_route, status=str(exc.response.status_code)).inc()
                t212_request_latency_seconds.labels(endpoint=_route).observe(time.perf_counter() - _t0)
                if exc.response.status_code in _NO_RETRY_CODES or attempt == _MAX_RETRIES:
                    raise
                time.sleep(min(2 ** attempt, 10))
            except (httpx.TransportError, httpx.ConnectError, httpx.NetworkError):
                t212_requests_total.labels(endpoint=_route, status="error").inc()
                t212_request_latency_seconds.labels(endpoint=_route).observe(time.perf_counter() - _t0)
                if attempt == _MAX_RETRIES:
                    raise
                time.sleep(min(2 ** attempt, 10))

    def _post(self, path: str, body: dict[str, Any], *, route: str | None = None) -> Any:
        url = f"{self._base}{path}"
        _route = route or path
        for attempt in range(1, _MAX_RETRIES + 1):
            _t0 = time.perf_counter()
            try:
                r = self._http.post(url, json=body)
                if r.status_code == 429:
                    t212_requests_total.labels(endpoint=_route, status="429").inc()
                    t212_request_latency_seconds.labels(endpoint=_route).observe(time.perf_counter() - _t0)
                    _handle_429(r, attempt)
                    continue
                r.raise_for_status()
                t212_requests_total.labels(endpoint=_route, status=str(r.status_code)).inc()
                t212_request_latency_seconds.labels(endpoint=_route).observe(time.perf_counter() - _t0)
                return r.json()
            except httpx.HTTPStatusError as exc:
                t212_requests_total.labels(endpoint=_route, status=str(exc.response.status_code)).inc()
                t212_request_latency_seconds.labels(endpoint=_route).observe(time.perf_counter() - _t0)
                if exc.response.status_code in _NO_RETRY_CODES or attempt == _MAX_RETRIES:
                    raise
                time.sleep(min(2 ** attempt, 10))
            except (httpx.TransportError, httpx.ConnectError, httpx.NetworkError):
                t212_requests_total.labels(endpoint=_route, status="error").inc()
                t212_request_latency_seconds.labels(endpoint=_route).observe(time.perf_counter() - _t0)
                if attempt == _MAX_RETRIES:
                    raise
                time.sleep(min(2 ** attempt, 10))

    def _delete(self, path: str, *, route: str | None = None) -> None:
        url = f"{self._base}{path}"
        _route = route or path
        for attempt in range(1, _MAX_RETRIES + 1):
            _t0 = time.perf_counter()
            try:
                r = self._http.delete(url)
                if r.status_code == 429:
                    t212_requests_total.labels(endpoint=_route, status="429").inc()
                    t212_request_latency_seconds.labels(endpoint=_route).observe(time.perf_counter() - _t0)
                    _handle_429(r, attempt)
                    continue
                r.raise_for_status()
                t212_requests_total.labels(endpoint=_route, status=str(r.status_code)).inc()
                t212_request_latency_seconds.labels(endpoint=_route).observe(time.perf_counter() - _t0)
                return
            except httpx.HTTPStatusError as exc:
                t212_requests_total.labels(endpoint=_route, status=str(exc.response.status_code)).inc()
                t212_request_latency_seconds.labels(endpoint=_route).observe(time.perf_counter() - _t0)
                if exc.response.status_code in _NO_RETRY_CODES or attempt == _MAX_RETRIES:
                    raise
                time.sleep(min(2 ** attempt, 10))
            except (httpx.TransportError, httpx.ConnectError, httpx.NetworkError):
                t212_requests_total.labels(endpoint=_route, status="error").inc()
                t212_request_latency_seconds.labels(endpoint=_route).observe(time.perf_counter() - _t0)
                if attempt == _MAX_RETRIES:
                    raise
                time.sleep(min(2 ** attempt, 10))

    def get_account_summary(self) -> dict[str, Any]:
        """Returns account summary including cash and totalValue."""
        return self._get("/equity/account/summary")

    def get_total_equity(self) -> float:
        """Return total portfolio value (cash + positions).

        Raises ValueError if totalValue is absent — caller must skip the tick rather
        than trade on cash-only equity which excludes open position value.
        """
        summary = self.get_account_summary()
        if "totalValue" not in summary:
            raise ValueError(
                f"T212 account summary missing 'totalValue' — response keys: {list(summary.keys())}"
            )
        return float(summary["totalValue"])

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
        """Submit STOP SELL leg. quantity must be positive; negated internally for T212 SELL convention."""
        body: dict[str, Any] = {
            "ticker": instrument_ticker,
            "quantity": -abs(quantity),
            "stopPrice": stop_price,
        }
        return self._post("/equity/orders/stop", body)

    def place_limit_order(
        self,
        instrument_ticker: str,
        quantity: float,
        limit_price: float,
    ) -> dict[str, Any]:
        """Submit LIMIT SELL leg. quantity must be positive; negated internally for T212 SELL convention."""
        body: dict[str, Any] = {
            "ticker": instrument_ticker,
            "quantity": -abs(quantity),
            "limitPrice": limit_price,
        }
        return self._post("/equity/orders/limit", body)

    def get_order(self, order_id: str) -> dict[str, Any]:
        """Fetch order status by T212 order ID."""
        return self._get(f"/equity/orders/{order_id}", route="/equity/orders/{id}")

    def cancel_order(self, order_id: str) -> None:
        """Cancel an open order by ID. Raises HTTPStatusError on failure."""
        self._delete(f"/equity/orders/{order_id}", route="/equity/orders/{id}")
