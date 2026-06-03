"""Tests for T212 client rate-limit handling (429) and HTTP retry logic."""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx

from alphaTrade.broker.t212_client import T212Client

DEMO_BASE = "https://demo.trading212.com/api/v0"


@pytest.fixture
def client():
    return T212Client(api_key="test-key", env="demo")


# ---------------------------------------------------------------------------
# 429 rate-limit handling
# ---------------------------------------------------------------------------

class TestRateLimitHandling:
    @respx.mock
    def test_429_with_retry_after_header_retries_once(self, client):
        """On 429 with Retry-After, client sleeps and retries, returns success."""
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "rate limit"})
            return httpx.Response(200, json={"totalValue": 10000.0, "cash": {}})

        respx.get(f"{DEMO_BASE}/equity/account/summary").mock(side_effect=handler)

        with patch("time.sleep") as mock_sleep:
            result = client.get_account_summary()

        assert result["totalValue"] == 10000.0
        assert call_count == 2
        mock_sleep.assert_called_once_with(0)

    @respx.mock
    def test_429_uses_retry_after_seconds(self, client):
        """Retry-After header value used as sleep duration."""
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429, headers={"Retry-After": "5"}, json={})
            return httpx.Response(200, json={"totalValue": 1.0, "cash": {}})

        respx.get(f"{DEMO_BASE}/equity/account/summary").mock(side_effect=handler)

        with patch("time.sleep") as mock_sleep:
            client.get_account_summary()

        mock_sleep.assert_called_once_with(5)

    @respx.mock
    def test_429_without_retry_after_uses_default(self, client):
        """Missing Retry-After header: falls back to default sleep."""
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429, json={})
            return httpx.Response(200, json={"totalValue": 1.0, "cash": {}})

        respx.get(f"{DEMO_BASE}/equity/account/summary").mock(side_effect=handler)

        with patch("time.sleep") as mock_sleep:
            client.get_account_summary()

        mock_sleep.assert_called_once()
        sleep_val = mock_sleep.call_args[0][0]
        assert sleep_val > 0

    @respx.mock
    def test_persistent_429_raises_after_max_retries(self, client):
        """Persistent 429 beyond max retries raises HTTPStatusError."""
        respx.get(f"{DEMO_BASE}/equity/account/summary").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "0"}, json={})
        )

        with patch("time.sleep"):
            with pytest.raises(httpx.HTTPStatusError):
                client.get_account_summary()

    @respx.mock
    def test_post_429_retries(self, client):
        """429 on POST also retries."""
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429, headers={"Retry-After": "0"}, json={})
            return httpx.Response(200, json={"id": "order-1", "status": "FILLED"})

        respx.post(f"{DEMO_BASE}/equity/orders/market").mock(side_effect=handler)

        with patch("time.sleep"):
            result = client.place_market_order("AAPL_US_EQ", 1.0)

        assert result["status"] == "FILLED"
        assert call_count == 2


# ---------------------------------------------------------------------------
# Exponential backoff retries (transient errors)
# ---------------------------------------------------------------------------

class TestExponentialBackoffRetries:
    @respx.mock
    def test_transient_500_retries_and_succeeds(self, client):
        """5xx transient error retried up to 3 attempts."""
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return httpx.Response(500, json={"error": "server error"})
            return httpx.Response(200, json={"totalValue": 5000.0, "cash": {}})

        respx.get(f"{DEMO_BASE}/equity/account/summary").mock(side_effect=handler)

        with patch("time.sleep"):
            result = client.get_account_summary()

        assert result["totalValue"] == 5000.0
        assert call_count == 3

    @respx.mock
    def test_persistent_500_raises_after_3_attempts(self, client):
        """Persistent 5xx raises after max retries."""
        respx.get(f"{DEMO_BASE}/equity/account/summary").mock(
            return_value=httpx.Response(500, json={})
        )

        with patch("time.sleep"):
            with pytest.raises(httpx.HTTPStatusError):
                client.get_account_summary()

    @respx.mock
    def test_401_not_retried(self, client):
        """401 Unauthorized never retried — config error, not transient."""
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(401, json={"error": "unauthorized"})

        respx.get(f"{DEMO_BASE}/equity/account/summary").mock(side_effect=handler)

        with pytest.raises(httpx.HTTPStatusError):
            client.get_account_summary()

        assert call_count == 1

    @respx.mock
    def test_403_not_retried(self, client):
        """403 Forbidden never retried."""
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(403, json={"error": "forbidden"})

        respx.get(f"{DEMO_BASE}/equity/account/summary").mock(side_effect=handler)

        with pytest.raises(httpx.HTTPStatusError):
            client.get_account_summary()

        assert call_count == 1

    @respx.mock
    def test_network_error_retried(self, client):
        """Network-level errors (ConnectError) retried."""
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.ConnectError("connection refused")
            return httpx.Response(200, json={"totalValue": 1.0, "cash": {}})

        respx.get(f"{DEMO_BASE}/equity/account/summary").mock(side_effect=handler)

        with patch("time.sleep"):
            client.get_account_summary()

        assert call_count == 3
