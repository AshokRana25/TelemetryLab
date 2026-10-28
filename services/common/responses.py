import json
import time
from urllib.parse import urlencode

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from services.common.telemetry import active_ids


def trace_url(base, trace_id):
    panes = {
        "trace": {
            "datasource": "tempo",
            "queries": [{"refId": "A", "queryType": "traceql", "query": trace_id}],
            "range": {"from": "now-1h", "to": "now"},
        }
    }
    return f"{base}/explore?{urlencode({'schemaVersion': '1', 'panes': json.dumps(panes)})}"


def result(settings, start, status_code=200, **body):
    ids = active_ids()
    return JSONResponse(
        status_code=status_code,
        content={
            **body,
            "trace_id": ids["trace_id"],
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
            "trace_url": trace_url(settings.grafana_url, ids["trace_id"]),
        },
    )


def record_failure(telemetry, error, message):
    span = trace.get_current_span()
    span.record_exception(error)
    span.set_status(Status(StatusCode.ERROR, message))
    telemetry.logger.error(message, extra={"error.type": type(error).__name__})


def install_validation_handler(app, settings, telemetry):
    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        record_failure(telemetry, exc, "Request validation failed")
        # Do not echo arbitrary input into a response or telemetry labels.
        return result(
            settings,
            time.perf_counter(),
            422,
            status="error",
            message="Invalid SKU, quantity, or scenario",
        )
