from __future__ import annotations


def test_setup_telemetry_returns_tracer_provider_with_correct_resource():
    from opentelemetry.sdk.trace import TracerProvider
    from alphaTrade.telemetry import setup_telemetry

    tracer_provider, _ = setup_telemetry("test-svc", otlp_endpoint="http://localhost:4317")

    assert isinstance(tracer_provider, TracerProvider)
    assert tracer_provider.resource.attributes["service.name"] == "test-svc"
    assert tracer_provider.resource.attributes["service.environment"] == "local"


def test_setup_telemetry_returns_meter_provider_with_trace_based_exemplar_filter():
    from opentelemetry.sdk.metrics import MeterProvider, TraceBasedExemplarFilter
    from alphaTrade.telemetry import setup_telemetry

    _, meter_provider = setup_telemetry("test-svc", otlp_endpoint="http://localhost:4317")

    assert isinstance(meter_provider, MeterProvider)
    assert isinstance(meter_provider._sdk_config.exemplar_filter, TraceBasedExemplarFilter)


def test_setup_telemetry_sets_global_providers():
    from opentelemetry import trace, metrics
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.metrics import MeterProvider
    from alphaTrade.telemetry import setup_telemetry

    setup_telemetry("test-svc", otlp_endpoint="http://localhost:4317")

    assert isinstance(trace.get_tracer_provider(), TracerProvider)
    assert isinstance(metrics.get_meter_provider(), MeterProvider)
