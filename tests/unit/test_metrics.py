"""Smoke tests for alphaTrade.metrics — validates metric definitions parse correctly."""
from __future__ import annotations

import asyncio
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
import prometheus_client
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from sqlmodel import SQLModel, create_engine

from alphaTrade.broker.t212_client import T212Client
from alphaTrade.config import Settings
from alphaTrade.health import HealthState
from alphaTrade.main import make_tick
from alphaTrade.store.repos import Position

DEMO_BASE = "https://demo.trading212.com/api/v0"


def test_metrics_module_exports_all_expected_names():
    import alphaTrade.metrics as m

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
    import alphaTrade.metrics  # noqa: F401 — ensure metrics registered

    output = prometheus_client.generate_latest(prometheus_client.REGISTRY)
    assert len(output) > 0
    # Spot-check that our metric names appear in the output
    assert b"signals_total" in output
    assert b"orders_total" in output
    assert b"equity_total" in output
    assert b"inference_latency_seconds" in output


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
            patch("alphaTrade.broker.t212_client.t212_requests_total", mock_counter),
            patch("alphaTrade.broker.t212_client.t212_request_latency_seconds", mock_histogram),
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
            patch("alphaTrade.broker.t212_client.t212_requests_total", mock_counter),
            patch("alphaTrade.broker.t212_client.t212_request_latency_seconds", mock_histogram),
            patch("time.sleep"),
        ):
            with pytest.raises(Exception):
                client.get_account_summary()

        # Counter must be called once per attempt (3 retries → 3 increments)
        assert mock_counter.labels.call_count == 3
        # All status labels should be "500"
        for call in mock_counter.labels.call_args_list:
            assert call.kwargs["status"] == "500"

    @respx.mock
    def test_successful_post_increments_counter(self):
        respx.post(f"{DEMO_BASE}/equity/orders/market").mock(
            return_value=httpx.Response(200, json={"id": "123"})
        )
        client = T212Client(api_key="test-key", env="demo")
        mock_counter = MagicMock()
        mock_histogram = MagicMock()

        with (
            patch("alphaTrade.broker.t212_client.t212_requests_total", mock_counter),
            patch("alphaTrade.broker.t212_client.t212_request_latency_seconds", mock_histogram),
        ):
            client.place_market_order("AAPL_US_EQ", 1)

        mock_counter.labels.assert_called_once_with(
            endpoint="/equity/orders/market", status="200"
        )
        mock_counter.labels.return_value.inc.assert_called_once()

    @respx.mock
    def test_successful_delete_increments_counter(self):
        respx.delete(f"{DEMO_BASE}/equity/orders/abc123").mock(
            return_value=httpx.Response(200, json={})
        )
        client = T212Client(api_key="test-key", env="demo")
        mock_counter = MagicMock()
        mock_histogram = MagicMock()

        with (
            patch("alphaTrade.broker.t212_client.t212_requests_total", mock_counter),
            patch("alphaTrade.broker.t212_client.t212_request_latency_seconds", mock_histogram),
        ):
            client.cancel_order("abc123")

        # Route template used, not the raw path with order ID
        mock_counter.labels.assert_called_once_with(
            endpoint="/equity/orders/{id}", status="200"
        )
        mock_counter.labels.return_value.inc.assert_called_once()

    @respx.mock
    def test_transport_error_records_error_status(self):
        respx.get(f"{DEMO_BASE}/equity/account/summary").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        client = T212Client(api_key="test-key", env="demo")
        mock_counter = MagicMock()
        mock_histogram = MagicMock()

        with (
            patch("alphaTrade.broker.t212_client.t212_requests_total", mock_counter),
            patch("alphaTrade.broker.t212_client.t212_request_latency_seconds", mock_histogram),
            patch("time.sleep"),
        ):
            with pytest.raises(httpx.ConnectError):
                client.get_account_summary()

        # All 3 attempts record status="error"
        assert mock_counter.labels.call_count == 3
        for call in mock_counter.labels.call_args_list:
            assert call.kwargs["status"] == "error"

    @respx.mock
    def test_429_records_429_status(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})

        respx.get(f"{DEMO_BASE}/equity/account/summary").mock(side_effect=handler)
        client = T212Client(api_key="test-key", env="demo")
        mock_counter = MagicMock()
        mock_histogram = MagicMock()

        with (
            patch("alphaTrade.broker.t212_client.t212_requests_total", mock_counter),
            patch("alphaTrade.broker.t212_client.t212_request_latency_seconds", mock_histogram),
            patch("time.sleep"),
        ):
            with pytest.raises(Exception):
                client.get_account_summary()

        assert mock_counter.labels.call_count == 4
        for call in mock_counter.labels.call_args_list:
            assert call.kwargs["status"] == "429"


_BUY_DF = pd.DataFrame({
    "Open": [150.0], "High": [155.0], "Low": [148.0],
    "Close": [152.0], "Volume": [1_000_000],
})


def _engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    return eng


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        t212_demo_api_key="test-key",
        state_db_path=tmp_path / "state.db",
        models_dir=tmp_path / "models",
        overrides_path=tmp_path / "overrides.yaml",
    )


def _manifest(ticker="AAPL", run_name="run_a", interval="1d"):
    m = MagicMock()
    m.ticker = ticker
    m.run_name = run_name
    m.interval = interval
    m.window = 20
    m.feature_names = ["Close"]
    m.normalize_mean = {}
    m.normalize_std = {}
    return m


def _registry_with_signal(manifest, signal_output):
    model = MagicMock()
    model.run.return_value = signal_output
    reg = MagicMock()
    reg.refresh = AsyncMock()
    reg.snapshot_by_interval.return_value = {"1d": [(manifest, model)]}
    reg.by_run_name = {manifest.run_name: (manifest, model)}
    return reg


class TestTickMetrics:
    @pytest.mark.asyncio
    async def test_signals_total_incremented_on_signal(self, tmp_path):
        manifest = _manifest()
        # HOLD signal (argmax=2 → "HOLD")
        registry = _registry_with_signal(manifest, np.array([-1.0, -1.0, 2.0]))
        t212 = MagicMock()
        t212.get_total_equity.return_value = 10_000.0
        provider = MagicMock()
        provider.fetch_ohlcv.return_value = _BUY_DF

        mock_signals_total = MagicMock()
        mock_equity_total = MagicMock()
        mock_open_positions = MagicMock()
        mock_daily_pnl_pct = MagicMock()

        tick = make_tick(
            "1d",
            registry=registry,
            settings=_settings(tmp_path),
            engine=_engine(),
            t212_holder=[t212],
            provider_holder=[provider],
            health_state=HealthState(),
            oco_tasks=set(),
            static_map={"AAPL": "AAPL_US_EQ"},
        )

        with (
            patch("alphaTrade.main.compute_features", return_value=_BUY_DF),
            patch("alphaTrade.main.normalize", return_value=_BUY_DF),
            patch("alphaTrade.main.build_input", return_value=np.zeros((1, 1))),
            patch("alphaTrade.main.consensus_by_ticker", return_value={"AAPL": "HOLD"}),
            patch("alphaTrade.main.signals_total", mock_signals_total),
            patch("alphaTrade.main.metric_equity_total", mock_equity_total),
            patch("alphaTrade.main.metric_open_positions", mock_open_positions),
            patch("alphaTrade.main.metric_daily_pnl_pct", mock_daily_pnl_pct),
        ):
            await tick()

        mock_signals_total.labels.assert_called_once_with(ticker="AAPL", signal="HOLD")
        mock_signals_total.labels.return_value.inc.assert_called_once()
        mock_equity_total.set.assert_called_once_with(10_000.0)
        mock_daily_pnl_pct.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_inference_errors_total_on_exception(self, tmp_path):
        manifest = _manifest()
        registry = _registry_with_signal(manifest, np.array([-1.0, -1.0, 2.0]))
        t212 = MagicMock()
        t212.get_total_equity.return_value = 10_000.0
        provider = MagicMock()
        provider.fetch_ohlcv.return_value = _BUY_DF

        mock_inference_errors = MagicMock()

        tick = make_tick(
            "1d",
            registry=registry,
            settings=_settings(tmp_path),
            engine=_engine(),
            t212_holder=[t212],
            provider_holder=[provider],
            health_state=HealthState(),
            oco_tasks=set(),
            static_map={"AAPL": "AAPL_US_EQ"},
        )

        with (
            patch("alphaTrade.main.compute_features", side_effect=ValueError("bad features")),
            patch("alphaTrade.main.inference_errors_total", mock_inference_errors),
        ):
            await tick()

        mock_inference_errors.labels.assert_called_once_with(run_name="run_a")
        mock_inference_errors.labels.return_value.inc.assert_called_once()
