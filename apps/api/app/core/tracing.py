"""OpenTelemetry tracing. A no-op unless OTEL_EXPORTER_OTLP_ENDPOINT is set (or an exporter is
injected, as the tests do). Spans carry operational attributes only: no message text, no PII."""
import logging
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

log = logging.getLogger("hospitality.tracing")
tracer = trace.get_tracer("hospitality")  # replaced by setup_tracing when enabled


def setup_tracing(app, settings, exporter=None) -> TracerProvider | None:
    global tracer
    if exporter is None:
        if not settings.otel_exporter_otlp_endpoint:
            return None
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        except ImportError:
            log.warning("OTLP exporter not installed; tracing disabled")
            return None
        exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint.rstrip("/")
                                    + "/v1/traces")
        processor = BatchSpanProcessor(exporter)
    else:
        processor = SimpleSpanProcessor(exporter)
    provider = TracerProvider(resource=Resource.create({"service.name": settings.otel_service_name}))
    provider.add_span_processor(processor)
    tracer = provider.get_tracer("hospitality")
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    return provider


@contextmanager
def span(name: str, **attrs):
    with tracer.start_as_current_span(name) as s:
        for k, v in attrs.items():
            s.set_attribute(k, v)
        yield s
