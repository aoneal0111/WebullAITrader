from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4


def utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationsEvent:
    """Base class for immutable Operations Center business events."""

    occurred_at: datetime = field(default_factory=utc_now)
    event_id: UUID = field(default_factory=uuid4)
    source: str = "operations"

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")

        if not self.source.strip():
            raise ValueError("source must not be empty")


@dataclass(frozen=True, slots=True)
class OperationsOrder:
    """Backend-neutral immutable order state for Operations Center consumers."""

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
            value = getattr(self, field_name)

            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be empty")

            if value != value.strip():
                raise ValueError(f"{field_name} must be stripped")

        if self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")


@dataclass(frozen=True, slots=True, kw_only=True)
class OrdersUpdated(OperationsEvent):
    """Replace the Operations Center order slice with an immutable snapshot."""

    orders: tuple[OperationsOrder, ...] = ()

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)

        if not isinstance(self.orders, tuple):
            raise TypeError("orders must be an immutable tuple")

        if any(
            not isinstance(order, OperationsOrder)
            for order in self.orders
        ):
            raise TypeError(
                "orders must contain only OperationsOrder instances"
            )

@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeStarting(OperationsEvent):
    environment: str = "PAPER"


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeStarted(OperationsEvent):
    environment: str = "PAPER"
    active_model: str = "Not loaded"


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeCycleCompleted(OperationsEvent):
    cycle_count: int

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)

        if self.cycle_count < 0:
            raise ValueError("cycle_count must be nonnegative")


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeStopping(OperationsEvent):
    reason: str = "Operator requested shutdown"


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeStopped(OperationsEvent):
    reason: str = "Runtime stopped cleanly"
    cycles_completed: int = 0

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)

        if self.cycles_completed < 0:
            raise ValueError("cycles_completed must be nonnegative")


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeFailed(OperationsEvent):
    error_message: str

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)

        if not self.error_message.strip():
            raise ValueError("error_message must not be empty")
