from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PRODUCTS = {
    "LAB-MUG": {"name": "Trace mug", "price_cents": 1800, "stock": 50},
    "LAB-NOTE": {"name": "Field notes", "price_cents": 1200, "stock": 100},
    "LAB-TOTE": {"name": "Signal tote", "price_cents": 2400, "stock": 25},
}
Scenario = Literal["normal", "slow", "failure"]


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str = Field(default="LAB-MUG", min_length=1, max_length=32)
    quantity: int = Field(default=1, ge=1, le=10, strict=True)
    scenario: Scenario = "normal"
