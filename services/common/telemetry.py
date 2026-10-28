"""One SDK per service, explicit instrumentors, and a bounded metric vocabulary."""

import json
import logging
import time

from opentelemetry import propagate, trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, SimpleLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import DropAggregation, ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from services.common.config import Settings

BOUNDS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 1.5, 2, 3, 5, 10)


def active_ids():
    context = trace.get_current_span().get_span_context()
    return {"trace_id": f"{context.trace_id:032x}", "span_id": f"{context.span_id:016x}"}


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {"level": record.levelname, "message": record.getMessage(), **active_ids()}
        )


class Telemetry:
    def __init__(
        self,
        service: str,
        settings: Settings,
        *,
        span_exporter=None,
        log_exporter=None,
        metric_reader=None,
    ):
        # Resource() intentionally avoids auto-detected machine identifiers in metric labels.
        self.resource = Resource(
            {
                "service.name": service,
                "service.namespace": "telemetrylab",
                "service.version": settings.version,
                "deployment.environment.name": settings.environment,
            }
        )
        propagate.set_global_textmap(
            CompositePropagator(
                [
                    TraceContextTextMapPropagator(),
                    W3CBaggagePropagator(),
                ]
            )
        )
        # This learning lab samples even remote unsampled parents: every demo trace is visible.
        self.traces = TracerProvider(resource=self.resource, sampler=ALWAYS_ON)
        if span_exporter is not None:
            self.traces.add_span_processor(SimpleSpanProcessor(span_exporter))
        elif settings.telemetry_enabled:
            self.traces.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(endpoint=f"{settings.otlp_endpoint}/v1/traces", timeout=5),
                    schedule_delay_millis=500,
                )
            )
        readers = [metric_reader] if metric_reader is not None else []
        if not readers and settings.telemetry_enabled:
            readers = [
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(endpoint=f"{settings.otlp_endpoint}/v1/metrics", timeout=5),
                    export_interval_millis=2000,
                )
            ]
        self.metrics = MeterProvider(
            resource=self.resource,
            metric_readers=readers,
            views=[
                # Use just our route-scoped HTTP metrics, not overlapping instrumentor metrics.
                View(instrument_name="http.*", aggregation=DropAggregation()),
                View(
                    instrument_name="lab.request.duration",
                    aggregation=ExplicitBucketHistogramAggregation(boundaries=BOUNDS),
                ),
            ],
        )
        self.logs = LoggerProvider(resource=self.resource)
        if log_exporter is not None:
            self.logs.add_log_record_processor(SimpleLogRecordProcessor(log_exporter))
        elif settings.telemetry_enabled:
            self.logs.add_log_record_processor(
                BatchLogRecordProcessor(
                    OTLPLogExporter(endpoint=f"{settings.otlp_endpoint}/v1/logs", timeout=5),
                    schedule_delay_millis=500,
                )
            )
        self.logger = logging.Logger(service, level=logging.INFO)
        self.logger.addHandler(LoggingHandler(level=logging.INFO, logger_provider=self.logs))
        console = logging.StreamHandler()
        console.setFormatter(JsonFormatter())
        self.logger.addHandler(console)
        self.tracer = self.traces.get_tracer("telemetrylab", settings.version)
        meter = self.metrics.get_meter("telemetrylab", settings.version)
        self.requests = meter.create_counter("lab.requests", description="Business HTTP requests")
        self.errors = meter.create_counter(
            "lab.request.errors", description="HTTP responses >= 400"
        )
        self.duration = meter.create_histogram("lab.request.duration", unit="s")
        self.orders = meter.create_counter(
            "lab.orders", description="Accepted or rejected checkouts"
        )
        self.inventory_failures = meter.create_counter("lab.inventory.failures")
        route, statuses = (
            ("/api/orders", (201, 403, 405, 409, 422, 500, 502, 504))
            if service == "order-service"
            else ("/api/check", (200, 403, 404, 405, 422, 500, 503))
        )
        # Export a zero baseline before traffic so rate() can include the first error.
        # Never insert fake histogram observations to initialize latency series.
        for status in statuses:
            attrs = {"route": route, "status_code": str(status)}
            self.requests.add(0, attrs)
            if status >= 400:
                self.errors.add(0, attrs)
        if service == "order-service":
            for outcome in ("success", "failure"):
                self.orders.add(0, {"outcome": outcome})
        if service == "inventory-service":
            self.inventory_failures.add(0, {"reason": "simulated"})

    def instrument(self, app):
        app.add_middleware(RequestMetricsMiddleware, telemetry=self)
        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=self.traces,
            meter_provider=self.metrics,
            excluded_urls=r"/healthz$|/static/|/api/config$|/api/products$|/$",
            exclude_spans=["receive", "send"],
        )

    def shutdown(self):
        self.traces.shutdown()
        self.metrics.shutdown()
        self.logs.shutdown()
        for handler in self.logger.handlers:
            handler.close()


class RequestMetricsMiddleware:
    """Pure ASGI middleware preserves trace context and counts all HTTP failure responses."""

    def __init__(self, app, telemetry):
        self.app, self.telemetry = app, telemetry

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] not in ("/api/orders", "/api/check"):
            return await self.app(scope, receive, send)
        start, status = time.perf_counter(), 500

        async def capture(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                ids = active_ids()
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-trace-id", ids["trace_id"].encode()),
                ]
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            attrs = {"route": scope["path"], "status_code": str(status)}
            self.telemetry.requests.add(1, attrs)
            self.telemetry.duration.record(time.perf_counter() - start, attrs)
            if status >= 400:
                self.telemetry.errors.add(1, attrs)
            if scope["path"] == "/api/orders" and scope["method"] == "POST":
                self.telemetry.orders.add(1, {"outcome": "success" if status < 400 else "failure"})
