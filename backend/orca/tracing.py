"""OpenTelemetry tracing, off unless configured.

Set ``ORCA_OTLP_ENDPOINT`` (for example http://localhost:4318/v1/traces, an OpenTelemetry Collector,
Jaeger or Grafana Tempo) and install the ``observability`` extra to export spans for every engine
advance and every matching batch, sampled at ``ORCA_TRACE_SAMPLE`` (default 0.1). Without them every
span is a no-op costing well under a microsecond.
"""
from __future__ import annotations

import contextlib
import os


class _NoSpan:
    def set_attribute(self, *a):
        pass


class _NoTracer:
    @contextlib.contextmanager
    def start_as_current_span(self, name, **kw):
        yield _NoSpan()


_tracer = None


def tracer():
    global _tracer
    if _tracer is not None:
        return _tracer
    endpoint = os.environ.get("ORCA_OTLP_ENDPOINT")
    _tracer = _NoTracer()
    if not endpoint:
        return _tracer
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
    except ImportError:
        return _tracer
    provider = TracerProvider(resource=Resource.create({"service.name": "orca-dispatch"}),
                              sampler=TraceIdRatioBased(float(os.environ.get("ORCA_TRACE_SAMPLE", 0.1))))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("orca")
    return _tracer
