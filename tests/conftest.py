from dataclasses import replace

import pytest
from opentelemetry.sdk._logs.export import InMemoryLogExporter
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from services.common.config import Settings
from services.common.telemetry import Telemetry


@pytest.fixture
def settings():
    return Settings(demo_enabled=True, telemetry_enabled=False, slow_seconds=0.04)


@pytest.fixture
def telemetry_factory(settings):
    created = []

    def factory(service, **overrides):
        spans = InMemorySpanExporter()
        logs = InMemoryLogExporter()
        reader = InMemoryMetricReader()
        telemetry = Telemetry(
            service,
            replace(settings, **overrides),
            span_exporter=spans,
            log_exporter=logs,
            metric_reader=reader,
        )
        created.append(telemetry)
        return telemetry, spans, logs, reader

    yield factory
    # Apps own shutdown when their lifespan is entered; shutdown is idempotent.
    for telemetry in created:
        telemetry.shutdown()
