import json
from pathlib import Path

import yaml

from scripts.build_dashboard import dashboard

ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_matches_generator_and_uses_only_exported_metrics():
    committed = json.loads((ROOT / "infra/grafana/dashboards/telemetrylab.json").read_text())
    assert committed == dashboard
    expressions = "\n".join(
        target.get("expr", "") for panel in committed["panels"] for target in panel["targets"]
    )
    for metric in (
        "lab_requests_total",
        "lab_request_errors_total",
        "lab_orders_total",
        "lab_request_duration_seconds_bucket",
        "lab_inventory_failures_total",
    ):
        assert metric in expressions
    assert "order_id" not in expressions and "trace_id" not in expressions


def test_all_signals_use_memory_limiter_batch_and_native_loki_otlp():
    collector = yaml.safe_load((ROOT / "infra/otel-collector.yaml").read_text())
    for signal in ("traces", "metrics", "logs"):
        pipeline = collector["service"]["pipelines"][signal]
        assert pipeline["receivers"] == ["otlp"]
        assert pipeline["processors"][0] == "memory_limiter"
        assert pipeline["processors"][-1] == "batch"
    assert collector["exporters"]["otlphttp/loki"]["endpoint"] == "http://loki:3100/otlp"
    sources = yaml.safe_load(
        (ROOT / "infra/grafana/provisioning/datasources/datasources.yaml").read_text()
    )
    tempo = next(source for source in sources["datasources"] if source["uid"] == "tempo")
    assert 'trace_id = "$${__trace.traceId}"' in tempo["jsonData"]["tracesToLogsV2"]["query"]


def test_compose_exposes_only_localhost_interfaces_and_has_real_healthchecks():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    exposed = set()
    for name, service in compose["services"].items():
        assert service["healthcheck"]["test"][0] == "CMD"
        for port in service.get("ports", []):
            assert port.startswith("127.0.0.1:")
            exposed.add(name)
    assert exposed == {"order", "grafana"}
