"""Small, explicit configuration surface; faults are off outside the Compose demo."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    demo_enabled: bool = False
    slow_seconds: float = 1.5
    inventory_timeout: float = 3.0
    inventory_url: str = "http://inventory:8081"
    grafana_url: str = "http://localhost:3000"
    otlp_endpoint: str = "http://otel-collector:4318"
    telemetry_enabled: bool = True
    environment: str = "local"
    version: str = "1.0.0"

    def __post_init__(self):
        if not 0 < self.slow_seconds <= 10:
            raise ValueError("DEMO_SLOW_SECONDS must be in (0, 10]")
        if not 0 < self.inventory_timeout <= 15:
            raise ValueError("INVENTORY_TIMEOUT_SECONDS must be in (0, 15]")
        for url in (self.inventory_url, self.grafana_url, self.otlp_endpoint):
            if not url.startswith(("http://", "https://")):
                raise ValueError("Service URLs must use HTTP or HTTPS")

    @classmethod
    def from_env(cls):
        return cls(
            demo_enabled=os.getenv("DEMO_ENABLED", "false").lower() == "true",
            slow_seconds=float(os.getenv("DEMO_SLOW_SECONDS", "1.5")),
            inventory_timeout=float(os.getenv("INVENTORY_TIMEOUT_SECONDS", "3")),
            inventory_url=os.getenv("INVENTORY_URL", "http://inventory:8081").rstrip("/"),
            grafana_url=os.getenv("GRAFANA_PUBLIC_URL", "http://localhost:3000").rstrip("/"),
            otlp_endpoint=os.getenv(
                "OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4318"
            ).rstrip("/"),
            telemetry_enabled=os.getenv("TELEMETRY_ENABLED", "true").lower() == "true",
            environment=os.getenv("DEPLOYMENT_ENVIRONMENT", "local"),
            version=os.getenv("SERVICE_VERSION", "1.0.0"),
        )
