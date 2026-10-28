import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from opentelemetry.trace import Status, StatusCode

from services.common.catalog import PRODUCTS, CheckoutRequest
from services.common.config import Settings
from services.common.responses import install_validation_handler, record_failure, result
from services.common.telemetry import Telemetry


class SimulatedInventoryFailure(RuntimeError):
    pass


def create_app(settings: Settings | None = None, telemetry: Telemetry | None = None):
    settings = settings or Settings.from_env()
    telemetry = telemetry or Telemetry("inventory-service", settings)

    @asynccontextmanager
    async def lifespan(app):
        yield
        telemetry.shutdown()

    app = FastAPI(title="TelemetryLab Inventory", version=settings.version, lifespan=lifespan)
    app.state.telemetry = telemetry
    install_validation_handler(app, settings, telemetry)

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "service": "inventory-service"}

    @app.post("/api/check")
    async def check_inventory(item: CheckoutRequest):
        start = time.perf_counter()
        if item.scenario != "normal" and not settings.demo_enabled:
            return result(
                settings, start, 403, status="error", message="Demo scenarios are disabled"
            )
        try:
            with telemetry.tracer.start_as_current_span("inventory.check") as span:
                span.set_attribute("demo.scenario", item.scenario)
                span.set_attribute("product.sku", item.sku)
                if item.scenario == "slow":
                    telemetry.logger.warning(
                        "Injecting slow inventory response",
                        extra={"demo.delay_seconds": settings.slow_seconds},
                    )
                    await asyncio.sleep(settings.slow_seconds)
                if item.scenario == "failure":
                    raise SimulatedInventoryFailure("Deterministic inventory outage")
                product = PRODUCTS.get(item.sku)
                if product is None:
                    span.set_status(Status(StatusCode.ERROR, "Unknown product"))
                    telemetry.logger.warning("Unknown inventory product")
                    return result(settings, start, 404, status="error", message="Unknown product")
                available = item.quantity <= product["stock"]
                telemetry.logger.info("Inventory checked", extra={"inventory.available": available})
                return result(settings, start, status="ok", available=available)
        except SimulatedInventoryFailure as exc:
            # The child span records the raised exception; mark the incoming server span as well.
            record_failure(telemetry, exc, "Inventory scenario failed")
            telemetry.inventory_failures.add(1, {"reason": "simulated"})
            return result(settings, start, 503, status="error", message=str(exc))

    telemetry.instrument(app)
    return app
