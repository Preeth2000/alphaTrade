from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def _patch_otlp_exporters():
    """Replace gRPC OTLP exporters with no-op mocks so tests don't open connections."""
    mock_span_exporter = MagicMock()
    mock_span_exporter.export.return_value = 0  # SUCCESS
    mock_metric_exporter = MagicMock()
    mock_metric_exporter.export.return_value = 0
    mock_metric_exporter._preferred_temporality = {}
    mock_metric_exporter._preferred_aggregation = {}
    mock_log_exporter = MagicMock()
    mock_log_exporter.export.return_value = 0

    with (
        patch("alphaTrade.telemetry.OTLPSpanExporter", return_value=mock_span_exporter),
        patch("alphaTrade.telemetry.OTLPMetricExporter", return_value=mock_metric_exporter),
        patch("alphaTrade.telemetry.OTLPLogExporter", return_value=mock_log_exporter),
    ):
        yield


def test_setup_telemetry_returns_tracer_provider_with_correct_resource():
    from opentelemetry.sdk.trace import TracerProvider
    from alphaTrade.telemetry import setup_telemetry

    tracer_provider, *_ = setup_telemetry("test-svc", otlp_endpoint="http://localhost:4317")

    assert isinstance(tracer_provider, TracerProvider)
    assert tracer_provider.resource.attributes["service.name"] == "test-svc"
    assert tracer_provider.resource.attributes["service.environment"] == "local"


def test_setup_telemetry_returns_meter_provider_with_trace_based_exemplar_filter():
    from opentelemetry.sdk.metrics import MeterProvider, TraceBasedExemplarFilter
    from alphaTrade.telemetry import setup_telemetry

    _, meter_provider, *_ = setup_telemetry("test-svc", otlp_endpoint="http://localhost:4317")

    assert isinstance(meter_provider, MeterProvider)
    assert isinstance(meter_provider._sdk_config.exemplar_filter, TraceBasedExemplarFilter)


def test_setup_telemetry_returns_logger_provider_with_correct_resource():
    from opentelemetry.sdk._logs import LoggerProvider
    from alphaTrade.telemetry import setup_telemetry

    _, _, logger_provider = setup_telemetry("test-svc", otlp_endpoint="http://localhost:4317")

    assert isinstance(logger_provider, LoggerProvider)
    assert logger_provider.resource.attributes["service.name"] == "test-svc"


def test_setup_telemetry_sets_global_providers():
    from opentelemetry import trace, metrics
    from opentelemetry._logs import get_logger_provider
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk._logs import LoggerProvider
    from alphaTrade.telemetry import setup_telemetry

    setup_telemetry("test-svc", otlp_endpoint="http://localhost:4317")

    assert isinstance(trace.get_tracer_provider(), TracerProvider)
    assert isinstance(metrics.get_meter_provider(), MeterProvider)
    assert isinstance(get_logger_provider(), LoggerProvider)
