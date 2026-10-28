"""Live end-to-end verification. Standard library only. Never substitutes synthetic telemetry."""

import argparse
import base64
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request


def request(url, body=None):
    encoded = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=encoded,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def eventually(check, label, timeout=90):
    deadline, last = time.monotonic() + timeout, None
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                print(f"PASS {label}", flush=True)
                return value
        except (
            AssertionError,
            KeyError,
            ValueError,
            StopIteration,
            urllib.error.URLError,
            TimeoutError,
        ) as exc:
            last = str(exc)
        time.sleep(2)
    raise AssertionError(f"Timed out: {label}; last error: {last}")


def spans_from_trace(value):
    if isinstance(value, dict):
        if "scopeSpans" in value or "instrumentationLibrarySpans" in value:
            attrs = value.get("resource", {}).get("attributes", [])
            service = next(
                (a["value"].get("stringValue") for a in attrs if a["key"] == "service.name"), None
            )
            for scope in value.get("scopeSpans", value.get("instrumentationLibrarySpans", [])):
                for span in scope.get("spans", []):
                    yield service, span
        else:
            for child in value.values():
                yield from spans_from_trace(child)
    elif isinstance(value, list):
        for child in value:
            yield from spans_from_trace(child)


def identifier(value):
    # Tempo versions can serialize protobuf byte fields as hex or base64 JSON.
    if len(value) in (16, 32) and all(char in "0123456789abcdef" for char in value.lower()):
        return value.lower()
    return base64.b64decode(value).hex()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--order-url", default="http://localhost:8080")
    parser.add_argument("--grafana-url", default="http://localhost:3000")
    args = parser.parse_args()
    proxy = f"{args.grafana_url}/api/datasources/proxy/uid"

    def query(expression):
        status, body = request(
            f"{proxy}/prometheus/api/v1/query?" + urllib.parse.urlencode({"query": expression})
        )
        assert status == 200 and body["status"] == "success", body
        return body["data"]["result"]

    def scalar(expression):
        results = query(expression)
        return float(results[0]["value"][1]) if results else 0

    def checkout(scenario):
        status, body = request(
            f"{args.order_url}/api/orders", {"sku": "LAB-MUG", "quantity": 1, "scenario": scenario}
        )
        assert status == (502 if scenario == "failure" else 201), (status, body)
        assert len(body["trace_id"]) == 32 and int(body["trace_id"], 16) != 0
        assert body["trace_id"] in urllib.parse.unquote(body["trace_url"])
        return body

    eventually(lambda: request(f"{args.order_url}/healthz")[0] == 200, "Order healthy")
    eventually(lambda: request(f"{args.grafana_url}/api/health")[0] == 200, "Grafana healthy")
    _, config = request(f"{args.order_url}/api/config")
    assert config["demo_enabled"], "Smoke scenarios require DEMO_ENABLED=true"
    checkout("normal")
    eventually(
        lambda: scalar('sum(lab_orders_total{outcome="success"})') >= 1,
        "Warmup metric reached Prometheus",
    )
    baseline_success = scalar('sum(lab_orders_total{outcome="success"})')
    baseline_failed = scalar('sum(lab_orders_total{outcome="failure"})')
    baseline_inventory_failed = scalar("sum(lab_inventory_failures_total)")
    runs = [(scenario, checkout(scenario)) for scenario in ("normal", "slow", "failure", "normal")]
    slow_seconds = config["slow_seconds"]
    assert runs[1][1]["elapsed_ms"] >= slow_seconds * 1000 * 0.95
    for scenario, body in runs:
        trace_id = body["trace_id"]

        def verify_trace(trace_id=trace_id, scenario=scenario):
            status, payload = request(f"{proxy}/tempo/api/traces/{trace_id}")
            assert status == 200
            spans = list(spans_from_trace(payload))
            assert {service for service, _ in spans} >= {"order-service", "inventory-service"}
            assert all(identifier(span["traceId"]) == trace_id for _, span in spans)
            incoming = next(
                span
                for service, span in spans
                if service == "inventory-service" and span["kind"] in (2, "SPAN_KIND_SERVER")
            )
            outgoing = next(
                span
                for service, span in spans
                if service == "order-service" and span["kind"] in (3, "SPAN_KIND_CLIENT")
            )
            assert identifier(incoming["parentSpanId"]) == identifier(outgoing["spanId"])
            inventory_span = next(span for _, span in spans if span["name"] == "inventory.check")
            assert any(span["name"] == "order.validate" for _, span in spans)
            if scenario == "slow":
                elapsed = (
                    int(inventory_span["endTimeUnixNano"])
                    - int(inventory_span["startTimeUnixNano"])
                ) / 1e9
                assert elapsed >= slow_seconds * 0.95
            if scenario == "failure":
                assert inventory_span["status"]["code"] in (2, "STATUS_CODE_ERROR")
                assert any(event["name"] == "exception" for event in inventory_span["events"])
            return True

        eventually(verify_trace, f"{scenario}: distributed trace, parentage, and span status")

        def verify_logs(trace_id=trace_id):
            expression = f'{{service_namespace="telemetrylab"}} | trace_id = "{trace_id}"'
            params = urllib.parse.urlencode({"query": expression, "since": "15m", "limit": "1000"})
            status, logs = request(f"{proxy}/loki/loki/api/v1/query_range?{params}")
            assert status == 200
            streams = logs["data"]["result"]
            assert {entry["stream"]["service_name"] for entry in streams} >= {
                "order-service",
                "inventory-service",
            }
            # Loki may merge structured metadata into stream fields or return it per entry.
            assert "span_id" in json.dumps(streams)
            return True

        eventually(verify_logs, f"{scenario}: correlated logs in both services")

    eventually(
        lambda: scalar('sum(lab_orders_total{outcome="success"})') >= baseline_success + 3,
        "Successful order metric increased",
    )
    eventually(
        lambda: scalar('sum(lab_orders_total{outcome="failure"})') >= baseline_failed + 1,
        "Failed order metric increased",
    )
    eventually(
        lambda: scalar("sum(lab_inventory_failures_total)") >= baseline_inventory_failed + 1,
        "Inventory failure metric increased",
    )
    eventually(
        lambda: scalar(
            'sum(lab_request_errors_total{service_name="order-service",status_code="502"})'
        )
        >= 1,
        "Checkout HTTP error metric exported",
    )
    eventually(
        lambda: 0
        < scalar(
            'sum(rate(lab_request_errors_total{service_name="order-service"}[1m])) / '
            'sum(rate(lab_requests_total{service_name="order-service"}[1m]))'
        )
        <= 1,
        "Injected checkout failure is visible as a valid error ratio",
    )
    eventually(
        lambda: scalar('sum(lab_request_duration_seconds_sum{service_name="inventory-service"})')
        >= slow_seconds,
        "Inventory duration histogram includes injected delay",
    )
    _, body = request(f"{args.grafana_url}/api/dashboards/uid/telemetrylab")
    assert body["dashboard"]["uid"] == "telemetrylab"
    for panel in body["dashboard"]["panels"]:
        for target in panel["targets"]:
            if target["datasource"]["uid"] != "prometheus":
                continue
            expression = target["expr"].replace("$__rate_interval", "1m").replace("$service", ".*")

            def verify_query(expression=expression):
                values = query(expression)
                return values and all(math.isfinite(float(value["value"][1])) for value in values)

            eventually(verify_query, f"Dashboard query: {panel['title']}")
    print("All live smoke checks passed. Recovery produced a successful distributed trace.")


if __name__ == "__main__":
    main()
