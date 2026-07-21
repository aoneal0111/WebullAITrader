from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class Fill:
    """One immutable paper-order execution."""

    fill_id: str
    order_id: str
    quantity: Decimal
    price: Decimal
    timestamp: datetime
    commission: Decimal = ZERO
    slippage: Decimal = ZERO
    venue: str | None = None
    liquidity_flag: str | None = None

    def __post_init__(self) -> None:
        fill_id = self.fill_id.strip()
        order_id = self.order_id.strip()

        if not fill_id:
            raise ValueError("fill_id is required")

        if not order_id:
            raise ValueError("order_id is required")

        if self.quantity <= ZERO:
            raise ValueError("quantity must be positive")

        if self.price <= ZERO:
            raise ValueError("price must be positive")

        if self.commission < ZERO:
            raise ValueError("commission cannot be negative")

        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")

        venue = self.venue.strip() if self.venue and self.venue.strip() else None
        liquidity_flag = (
            self.liquidity_flag.strip().upper()
            if self.liquidity_flag and self.liquidity_flag.strip()
            else None
        )

        object.__setattr__(self, "fill_id", fill_id)
        object.__setattr__(self, "order_id", order_id)
        object.__setattr__(self, "venue", venue)
        object.__setattr__(self, "liquidity_flag", liquidity_flag)

    @property
    def notional(self) -> Decimal:
        return self.quantity * self.price
