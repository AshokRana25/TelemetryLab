import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.trace import Status, StatusCode

from services.common.catalog import PRODUCTS, CheckoutRequest
from services.common.config import Settings
from services.common.responses import install_validation_handler, record_failure, result
from services.common.telemetry import Telemetry

STATIC = Path(__file__).parent / "static"


def create_app(
    settings: Settings | None = None,
    telemetry: Telemetry | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
):
    settings = settings or Settings.from_env()
    telemetry = telemetry or Telemetry("order-service", settings)

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(
            base_url=settings.inventory_url,
            timeout=httpx.Timeout(
                settings.inventory_timeout, connect=min(1, settings.inventory_timeout)
            ),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            transport=transport,
        ) as client:
            # Instrument this one client, never the global HTTPX class or uvicorn process twice.
            HTTPXClientInstrumentor.instrument_client(
                client, tracer_provider=telemetry.traces, meter_provider=telemetry.metrics
            )
            app.state.inventory = client
            yield
            HTTPXClientInstrumentor.uninstrument_client(client)
        telemetry.shutdown()

    app = FastAPI(title="TelemetryLab Order", version=settings.version, lifespan=lifespan)
    app.state.telemetry = telemetry
    install_validation_handler(app, settings, telemetry)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "service": "order-service"}

    @app.get("/api/config")
    async def config():
        return {
            "demo_enabled": settings.demo_enabled,
            "slow_seconds": settings.slow_seconds,
            "burst_count": 12,
            "burst_concurrency": 3,
            "dashboard_url": f"{settings.grafana_url}/d/telemetrylab/telemetrylab",
        }

    @app.get("/api/products")
    async def products():
        return [{"sku": sku, **product} for sku, product in PRODUCTS.items()]

    @app.post("/api/orders")
    async def checkout(item: CheckoutRequest):
        start = time.perf_counter()
        with telemetry.tracer.start_as_current_span("order.validate") as span:
            span.set_attribute("demo.scenario", item.scenario)
            product = PRODUCTS.get(item.sku)
            if item.scenario != "normal" and not settings.demo_enabled:
                span.set_status(Status(StatusCode.ERROR, "Demo disabled"))
                telemetry.logger.warning("Demo scenario rejected")
                return result(
                    settings, start, 403, status="error", message="Demo scenarios are disabled"
                )
            if product is None:
                span.set_status(Status(StatusCode.ERROR, "Unknown product"))
                telemetry.logger.warning("Order validation failed: unknown product")
                return result(settings, start, 422, status="error", message="Unknown product")
            telemetry.logger.info("Order validated", extra={"demo.scenario": item.scenario})
        try:
            with telemetry.tracer.start_as_current_span("order.check_inventory"):
                # A wall-clock deadline also bounds connection-pool waits and the entire response.
                async with asyncio.timeout(settings.inventory_timeout):
                    response = await app.state.inventory.post("/api/check", json=item.model_dump())
                    response.raise_for_status()
                    inventory = response.json()
                    if not isinstance(inventory, dict) or not isinstance(
                        inventory.get("available"), bool
                    ):
                        raise ValueError("Invalid inventory response")
        except (httpx.TimeoutException, TimeoutError) as exc:
            record_failure(telemetry, exc, "Inventory request timed out")
            return result(
                settings, start, 504, status="error", message="Inventory request timed out"
            )
        except (httpx.HTTPError, ValueError) as exc:
            record_failure(telemetry, exc, "Inventory unavailable; checkout rejected")
            return result(
                settings,
                start,
                502,
                status="error",
                message="Inventory unavailable; no order was created",
            )
        if not inventory["available"]:
            telemetry.logger.warning("Order rejected: insufficient stock")
            return result(settings, start, 409, status="error", message="Insufficient stock")
        order_id = f"LAB-{uuid.uuid4().hex[:12].upper()}"
        telemetry.logger.info("Order completed", extra={"order.id": order_id})
        return result(
            settings,
            start,
            201,
            status="success",
            message="Your demo order is confirmed",
            order_id=order_id,
            sku=item.sku,
            quantity=item.quantity,
            total_cents=product["price_cents"] * item.quantity,
        )

    telemetry.instrument(app)
    return app
