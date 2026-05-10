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
