# Signal reference

## Metrics are contracts

OTLP SDK names are deliberately short dotted names. The Collector's `UnderscoreEscapingWithSuffixes` translation produces the following Prometheus names. The only promoted resource labels are `service_name` and `environment`; exporter bookkeeping labels such as `job`, `instance`, and OTel scope labels may also appear.

| SDK instrument | Exported Prometheus series | Application labels |
| --- | --- | --- |
| `lab.requests` counter | `lab_requests_total` | `route`, `status_code` |
| `lab.request.errors` counter | `lab_request_errors_total` | `route`, `status_code` |
| `lab.request.duration` histogram, unit `s` | `lab_request_duration_seconds_bucket`, `_sum`, `_count` | `route`, `status_code`; buckets add `le` |
| `lab.orders` counter | `lab_orders_total` | `outcome=success\|failure` |
| `lab.inventory.failures` counter | `lab_inventory_failures_total` | `reason=simulated` |

Only the fixed paths `/api/orders` and `/api/check` are measured. HTTP errors mean status >=400. Orders counts POST checkout results, including validation rejections. Inventory failures counts deliberately injected outages; broader Inventory HTTP errors appear in `lab_request_errors_total`. Order-side connection failures/timeouts appear in the Order HTTP errors even when Inventory is unreachable.

SKU, order ID, trace ID, span ID, full URL, and error text are never metric labels. Order IDs may occur in log metadata; product SKU may occur in spans. These are useful request details but unsuitable metric dimensions.

```promql
# Request rate per service
sum by (service_name) (rate(lab_requests_total[1m]))

# p95 inventory server duration in seconds
histogram_quantile(0.95,
  sum by (le) (rate(lab_request_duration_seconds_bucket{service_name="inventory-service"}[1m]))
)

# Cumulative failed checkouts since process start
sum(lab_orders_total{service_name="order-service",outcome="failure"})
```

The dashboard uses `$__rate_interval` with the Prometheus datasource's 5-second scrape interval. Cumulative outcome panels are labeled as process-lifetime counters, not selected-range totals. Counters for known HTTP statuses and finite order outcomes are initialized to zero so rates can observe the first event after baseline scrapes. Histograms receive only real observations. No dashboard fills missing traffic with invented requests or delays.

## Traces

```text
POST /api/orders                           order-service · SERVER
├── order.validate                         order-service · INTERNAL
└── order.check_inventory                  order-service · INTERNAL
    └── POST                               order-service · CLIENT (HTTPX)
        └── POST /api/check                 inventory-service · SERVER
            └── inventory.check            inventory-service · INTERNAL
```

HTTPX's exact span display name can vary with semantic conventions; the smoke test checks its CLIENT kind and parentage, not that name. FastAPI send/receive helper spans are excluded for readability. W3C `traceparent` crosses HTTP; all six spans share a trace ID. The incoming request's trace ID is returned in JSON and `X-Trace-ID`, including handled validation failures.

`order.validate` performs catalog/demo checks after Pydantic validates the body. Pydantic rejections never call Inventory; their server span and correlated validation log remain available. Injected exceptions record events and ERROR status on the relevant child and incoming server spans. HTTP 5xx instrumentation also marks server/client spans appropriately.

## Logs and links

Each service has a Python logging handler connected to an OTel `LoggerProvider`. The handler reads the current trace/span context; there is no second stdout scraper. Console JSON is useful for `docker compose logs`, while the independent OTLP handler sends log records through the Collector.

Loki's native path is:

```text
Collector otlphttp exporter endpoint: http://loki:3100/otlp
Actual POST destination:             http://loki:3100/otlp/v1/logs
```

Loki indexes only service name, namespace, and deployment environment. It normalizes dots to underscores. Trace IDs, span IDs, exception data, and other attributes remain structured metadata. Filter them after the stream selector:

```logql
{service_name="inventory-service"} | trace_id = "ACTUAL_TRACE_ID"
```

The Tempo datasource uses a custom query against structured metadata, with a ±5-second span time window. It does not search for the trace ID in the human-readable log body. Loki's label-based derived field extracts `trace_id` and opens Tempo. Double dollars in provisioning YAML escape Grafana's environment substitution; they become single-dollar query variables at runtime.

The checkout trace URL uses Grafana Explore's serialized panes state and the provisioned `tempo` datasource UID. Its public base URL is configurable separately from internal Docker URLs.
