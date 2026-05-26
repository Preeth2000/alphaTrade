from __future__ import annotations

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider, TraceBasedExemplarFilter
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def setup_telemetry(
    service_name: str,
    otlp_endpoint: str = "http://localhost:4317",
) -> tuple[TracerProvider, MeterProvider]:
    resource = Resource.create({
        "service.name": service_name,
        "service.environment": "local",
    })

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
    )
    trace.set_tracer_provider(tracer_provider)

    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=otlp_endpoint),
        export_interval_millis=15_000,
    )
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[metric_reader],
        exemplar_filter=TraceBasedExemplarFilter(),
    )
    metrics.set_meter_provider(meter_provider)

    return tracer_provider, meter_provider
