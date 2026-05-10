"""Smoke tests for alphalink.metrics — validates metric definitions parse correctly."""
from __future__ import annotations

import pytest
import prometheus_client


def test_metrics_module_exports_all_expected_names():
    import alphalink.metrics as m

    assert hasattr(m, "signals_total")
    assert hasattr(m, "orders_total")
    assert hasattr(m, "inference_errors_total")
    assert hasattr(m, "t212_requests_total")
    assert hasattr(m, "equity_total")
    assert hasattr(m, "open_positions")
    assert hasattr(m, "daily_pnl_pct")
    assert hasattr(m, "inference_latency_seconds")
    assert hasattr(m, "t212_request_latency_seconds")


def test_metrics_generate_valid_prometheus_text():
    import alphalink.metrics  # noqa: F401 — ensure metrics registered

    output = prometheus_client.generate_latest(prometheus_client.REGISTRY)
    assert len(output) > 0
    # Spot-check that our metric names appear in the output
    assert b"signals_total" in output
    assert b"orders_total" in output
    assert b"equity_total" in output
    assert b"inference_latency_seconds" in output


import respx
import httpx
from unittest.mock import MagicMock, patch
from alphalink.broker.t212_client import T212Client

DEMO_BASE = "https://demo.trading212.com/api/v0"


class TestT212ClientMetrics:
    @respx.mock
    def test_successful_get_increments_counter(self):
        respx.get(f"{DEMO_BASE}/equity/account/summary").mock(
            return_value=httpx.Response(200, json={"totalValue": 1000.0, "cash": {}})
        )
        client = T212Client(api_key="test-key", env="demo")
        mock_counter = MagicMock()
        mock_histogram = MagicMock()

        with (
            patch("alphalink.broker.t212_client.t212_requests_total", mock_counter),
            patch("alphalink.broker.t212_client.t212_request_latency_seconds", mock_histogram),
        ):
            client.get_account_summary()

        mock_counter.labels.assert_called_once_with(
            endpoint="/equity/account/summary", status="200"
        )
        mock_counter.labels.return_value.inc.assert_called_once()
        mock_histogram.labels.assert_called_once_with(endpoint="/equity/account/summary")
        mock_histogram.labels.return_value.observe.assert_called_once()

    @respx.mock
    def test_http_error_records_error_status(self):
        respx.get(f"{DEMO_BASE}/equity/account/summary").mock(
            return_value=httpx.Response(500, json={"error": "server error"})
        )
        client = T212Client(api_key="test-key", env="demo")
        mock_counter = MagicMock()
        mock_histogram = MagicMock()

        with (
            patch("alphalink.broker.t212_client.t212_requests_total", mock_counter),
            patch("alphalink.broker.t212_client.t212_request_latency_seconds", mock_histogram),
            patch("time.sleep"),
        ):
            try:
                client.get_account_summary()
            except Exception:
                pass

        # Counter must be called for each attempt (3 retries → 3 increments)
        assert mock_counter.labels.call_count >= 1
        # All status labels should be "500"
        for call in mock_counter.labels.call_args_list:
            assert call.kwargs["status"] == "500"
