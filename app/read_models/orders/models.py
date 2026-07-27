from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")

    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")

    if value != value.strip():
        raise ValueError(f"{field_name} must be stripped")

    return value


@dataclass(frozen=True, slots=True)
class OrderReadModel:
    """Immutable consumer-facing representation of one operational order."""

    order_id: str
    symbol: str
    side: str
    quantity: str
    status: str
    updated_at: datetime

    def __post_init__(self) -> None:
        for field_name in (
            "order_id",
            "symbol",
            "side",
            "quantity",
            "status",
        ):
            _required_text(
                getattr(self, field_name),
                field_name,
            )

        if not isinstance(self.updated_at, datetime):
            raise TypeError("updated_at must be a datetime")

        if self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class OrdersReadModelSnapshot:
    """Immutable collection delivered to presentation consumers."""

    orders: tuple[OrderReadModel, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.orders, tuple):
            raise TypeError("orders must be an immutable tuple")

        if any(
            not isinstance(order, OrderReadModel)
            for order in self.orders
        ):
            raise TypeError(
                "orders must contain only OrderReadModel instances"
            )

    @classmethod
    def initial(cls) -> "OrdersReadModelSnapshot":
        return cls()
