import asyncio
from dataclasses import replace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from opentelemetry.context import Context, attach, detach
from opentelemetry.trace import SpanKind, StatusCode

from services.common.config import Settings
from services.inventory.app import create_app as inventory_app
from services.order.app import create_app as order_app


class NetworkBoundaryTransport(httpx.ASGITransport):
    """Model a separate process: only HTTP headers cross, not Python contextvars."""

    async def handle_async_request(self, request):
        token = attach(Context())
        try:
            return await super().handle_async_request(request)
        finally:
            detach(token)


def metric_points(reader, name):
    return [
        point
        for resource in reader.get_metrics_data().resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]


async def test_checkout_propagates_w3c_context_logs_and_metrics(settings, telemetry_factory):
    inv, inv_spans, inv_logs, inv_metrics = telemetry_factory("inventory-service")
    order, order_spans, order_logs, order_metrics = telemetry_factory("order-service")
    inventory = inventory_app(settings, inv)
    app = order_app(settings, order, transport=NetworkBoundaryTransport(app=inventory))
    parent_trace = "1234567890abcdef1234567890abcdef"
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/orders",
                json={"quantity": 2},
                headers={"traceparent": f"00-{parent_trace}-1234567890abcdef-01"},
            )
        assert response.status_code == 201
        body = response.json()
        assert body["total_cents"] == 3600
        assert body["trace_id"] == response.headers["x-trace-id"] == parent_trace
        assert body["elapsed_ms"] >= 0
        assert parent_trace in parse_qs(urlparse(body["trace_url"]).query)["panes"][0]
        spans = [*inv_spans.get_finished_spans(), *order_spans.get_finished_spans()]
        assert {span.context.trace_id for span in spans} == {int(parent_trace, 16)}
        assert {span.resource.attributes["service.name"] for span in spans} == {
            "order-service",
            "inventory-service",
        }
        inventory_server = next(
            span for span in inv_spans.get_finished_spans() if span.kind == SpanKind.SERVER
        )
        http_client = next(
            span for span in order_spans.get_finished_spans() if span.kind == SpanKind.CLIENT
        )
        assert inventory_server.parent.span_id == http_client.context.span_id
        assert len([span for span in spans if span.kind == SpanKind.SERVER]) == 2
        assert {"order.validate", "inventory.check", "order.check_inventory"} <= {
            span.name for span in spans
        }
        for exporter in (order_logs, inv_logs):
            logs = exporter.get_finished_logs()
            assert logs
            assert all(log.log_record.trace_id == int(parent_trace, 16) for log in logs)
            assert all(log.log_record.span_id != 0 for log in logs)
        assert sum(p.value for p in metric_points(order_metrics, "lab.orders")) == 1
        assert sum(p.value for p in metric_points(inv_metrics, "lab.requests")) == 1
        for reader in (order_metrics, inv_metrics):
            for resource in reader.get_metrics_data().resource_metrics:
                for scope in resource.scope_metrics:
                    for metric in scope.metrics:
                        assert not metric.name.startswith("http.")
                        for point in metric.data.data_points:
                            assert set(point.attributes) <= {
                                "outcome",
                                "reason",
                                "route",
                                "status_code",
                            }


async def test_slow_failure_and_recovery_are_request_scoped(settings, telemetry_factory):
    inv, inv_spans, _, _ = telemetry_factory("inventory-service")
    order, spans, _, reader = telemetry_factory("order-service")
    app = order_app(
        settings, order, transport=httpx.ASGITransport(app=inventory_app(settings, inv))
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            slow, normal = await asyncio.gather(
                client.post("/api/orders", json={"scenario": "slow"}),
                client.post("/api/orders", json={"scenario": "normal"}),
            )
            failed = await client.post("/api/orders", json={"scenario": "failure"})
            recovered = await client.post("/api/orders", json={})
        assert slow.status_code == normal.status_code == recovered.status_code == 201
        assert slow.json()["elapsed_ms"] >= settings.slow_seconds * 1000
        assert normal.json()["trace_id"] != slow.json()["trace_id"]
        assert failed.status_code == 502
        assert "order_id" not in failed.json()
        failure_trace = int(failed.json()["trace_id"], 16)
        error_spans = [
            span
            for span in [*spans.get_finished_spans(), *inv_spans.get_finished_spans()]
            if span.context.trace_id == failure_trace
        ]
        assert any(
            span.events and span.status.status_code == StatusCode.ERROR for span in error_spans
        )
        assert all(
            span.status.status_code == StatusCode.ERROR
            for span in error_spans
            if span.kind == SpanKind.SERVER
        )
        outcomes = {p.attributes["outcome"]: p.value for p in metric_points(reader, "lab.orders")}
        assert outcomes == {"success": 3, "failure": 1}


@pytest.mark.parametrize("service", ["order", "inventory"])
async def test_faults_require_explicit_demo_enable(settings, telemetry_factory, service):
    disabled = replace(settings, demo_enabled=False)
    telemetry, _, _, reader = telemetry_factory(f"{service}-service")
    app = (order_app if service == "order" else inventory_app)(disabled, telemetry)
    path = "/api/orders" if service == "order" else "/api/check"
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for scenario in ("slow", "failure"):
                response = await client.post(path, json={"scenario": scenario})
                assert response.status_code == 403
        assert sum(p.value for p in metric_points(reader, "lab.request.errors")) == 2
    assert Settings().demo_enabled is False


@pytest.mark.parametrize(
    "payload",
    [
        {"sku": "MISSING"},
        {"quantity": 0},
        {"quantity": 11},
        {"quantity": True},
        {"quantity": 1.5},
        {"scenario": "random"},
        {"payment_card": "not-accepted"},
    ],
)
async def test_validation_rejects_without_calling_inventory(settings, telemetry_factory, payload):
    def unexpected(request):
        pytest.fail("Invalid input reached inventory")

    telemetry, _, _, reader = telemetry_factory("order-service")
    app = order_app(settings, telemetry, httpx.MockTransport(unexpected))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/orders", json=payload)
        assert response.status_code == 422
        assert len(response.json()["trace_id"]) == 32
        assert sum(p.value for p in metric_points(reader, "lab.request.errors")) == 1


@pytest.mark.parametrize(
    "failure, expected", [("timeout", 504), ("connection", 502), ("bad_json", 502)]
)
async def test_dependency_errors_are_observable(settings, telemetry_factory, failure, expected):
    async def upstream(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("Timed out", request=request)
        if failure == "connection":
            raise httpx.ConnectError("Connection refused", request=request)
        return httpx.Response(200, text="not-json")

    telemetry, spans, _, _ = telemetry_factory("order-service")
    app = order_app(settings, telemetry, httpx.MockTransport(upstream))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/orders", json={})
        assert response.status_code == expected
        assert any(
            span.status.status_code == StatusCode.ERROR for span in spans.get_finished_spans()
        )


async def test_absolute_deadline_bounds_slow_dependency(settings, telemetry_factory):
    cancelled = asyncio.Event()

    async def upstream(request):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    telemetry, _, _, _ = telemetry_factory("order-service")
    app = order_app(
        replace(settings, inventory_timeout=0.03), telemetry, httpx.MockTransport(upstream)
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await asyncio.wait_for(client.post("/api/orders", json={}), timeout=10)
        assert response.status_code == 504
        assert cancelled.is_set(), "The deadline must cancel the blocked dependency operation"


async def test_static_ui_config_and_health_do_not_pollute_business_metrics(
    settings, telemetry_factory
):
    telemetry, spans, _, reader = telemetry_factory("order-service")
    app = order_app(settings, telemetry)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for path in ("/", "/healthz", "/api/config", "/api/products", "/static/app.js"):
                assert (await client.get(path)).status_code == 200
            config = (await client.get("/api/config")).json()
        assert config["burst_count"] == 12 and config["burst_concurrency"] == 3
        assert sum(p.value for p in metric_points(reader, "lab.requests")) == 0
        assert spans.get_finished_spans() == ()
