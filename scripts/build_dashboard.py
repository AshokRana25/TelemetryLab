"""Generate the committed dashboard from readable, explicitly named PromQL queries."""

import json
from pathlib import Path

PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
panels = []


def panel(title, exprs, x, y, w, h=7, kind="timeseries", unit="short", description=""):
    item = {
        "id": len(panels) + 1,
        "title": title,
        "type": kind,
        "datasource": PROM,
        "description": description,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {
            "defaults": {"unit": unit, "min": 0, "color": {"mode": "palette-classic"}},
            "overrides": [],
        },
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom"},
            "tooltip": {"mode": "multi"},
        },
        "targets": [
            {
                "refId": chr(65 + index),
                "expr": expr,
                "legendFormat": label,
                "datasource": PROM,
                "range": kind != "stat",
                "instant": kind == "stat",
            }
            for index, (label, expr) in enumerate(exprs)
        ],
    }
    if kind == "stat":
        item["options"] = {
            "reduceOptions": {"calcs": ["lastNotNull"], "values": False},
            "colorMode": "value",
            "graphMode": "none",
            "textMode": "auto",
        }
    if unit == "percentunit":
        item["fieldConfig"]["defaults"]["max"] = 1
    panels.append(item)


scope = 'service_name=~"$service"'
panel(
    "Request rate",
    [
        (
            "{{service_name}}",
            f"sum by (service_name) (rate(lab_requests_total{{{scope}}}[$__rate_interval]))",
        )
    ],
    0,
    0,
    6,
    5,
    unit="reqps",
)
panel(
    "Request error ratio",
    [
        (
            "HTTP ≥ 400",
            f"sum(rate(lab_request_errors_total{{{scope}}}[$__rate_interval])) / clamp_min(sum(rate(lab_requests_total{{{scope}}}[$__rate_interval])), 0.000001)",
        )
    ],
    6,
    0,
    6,
    5,
    unit="percentunit",
    description="Includes validation errors and injected failures. Zero-initialized counters include the first error after baseline scrapes.",
)
panel(
    "Successful orders · process lifetime",
    [("Confirmed", 'sum(lab_orders_total{service_name="order-service",outcome="success"})')],
    12,
    0,
    6,
    5,
    kind="stat",
    description="Cumulative since the Order process started; independent of the service filter.",
)
panel(
    "Failed orders · process lifetime",
    [("Rejected", 'sum(lab_orders_total{service_name="order-service",outcome="failure"})')],
    18,
    0,
    6,
    5,
    kind="stat",
    description="All rejected POST checkouts, including invalid input; resets on process restart.",
)
panel(
    "Latency · p50 / p95",
    [
        (
            f"p{int(q * 100)} · {{{{service_name}}}}",
            f"histogram_quantile({q}, sum by (le, service_name) (rate(lab_request_duration_seconds_bucket{{{scope}}}[$__rate_interval])))",
        )
        for q in (0.5, 0.95)
    ],
    0,
    5,
    12,
    unit="s",
    description="Bucket estimates of server duration, not exact individual timings. No traffic means no quantile.",
)
panel(
    "Checkout outcomes",
    [
        (
            "{{outcome}}",
            'sum by (outcome) (rate(lab_orders_total{service_name="order-service"}[$__rate_interval]))',
        )
    ],
    12,
    5,
    12,
    unit="ops",
)
panel(
    "Inventory latency · p50 / p95",
    [
        (
            f"p{int(q * 100)}",
            f'histogram_quantile({q}, sum by (le) (rate(lab_request_duration_seconds_bucket{{service_name="inventory-service"}}[$__rate_interval])))',
        )
        for q in (0.5, 0.95)
    ],
    0,
    12,
    12,
    unit="s",
)
panel(
    "Inventory failures",
    [
        (
            "{{reason}}",
            'sum by (reason) (rate(lab_inventory_failures_total{service_name="inventory-service"}[$__rate_interval]))',
        )
    ],
    12,
    12,
    6,
    unit="ops",
)
panel(
    "Inventory request rate",
    [
        (
            "HTTP {{status_code}}",
            'sum by (status_code) (rate(lab_requests_total{service_name="inventory-service"}[$__rate_interval]))',
        )
    ],
    18,
    12,
    6,
    unit="reqps",
)
panels.append(
    {
        "id": len(panels) + 1,
        "title": "Application logs · expand a line to inspect its trace",
        "type": "logs",
        "datasource": LOKI,
        "gridPos": {"x": 0, "y": 19, "w": 24, "h": 10},
        "targets": [
            {
                "refId": "A",
                "datasource": LOKI,
                "expr": '{service_name=~"$service"}',
                "queryType": "range",
            }
        ],
        "options": {
            "showTime": True,
            "showLabels": False,
            "showCommonLabels": False,
            "wrapLogMessage": True,
            "sortOrder": "Descending",
            "enableLogDetails": True,
        },
    }
)
dashboard = {
    "uid": "telemetrylab",
    "title": "TelemetryLab",
    "schemaVersion": 41,
    "version": 1,
    "tags": ["telemetrylab", "opentelemetry"],
    "editable": False,
    "timezone": "browser",
    "time": {"from": "now-15m", "to": "now"},
    "refresh": "5s",
    "panels": panels,
    "description": "Live checkout and inventory telemetry. Allow 5–15 seconds for export and scrape.",
    "templating": {
        "list": [
            {
                "name": "service",
                "label": "Service",
                "type": "custom",
                "query": "order-service,inventory-service",
                "multi": True,
                "includeAll": True,
                "allValue": ".*",
                "current": {"text": "All", "value": "$__all"},
                "options": [
                    {"text": "All", "value": "$__all", "selected": True},
                    {"text": "order-service", "value": "order-service", "selected": False},
                    {"text": "inventory-service", "value": "inventory-service", "selected": False},
                ],
            }
        ]
    },
}

if __name__ == "__main__":
    destination = Path(__file__).resolve().parents[1] / "infra/grafana/dashboards/telemetrylab.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(dashboard, indent=2) + "\n", encoding="utf-8")
