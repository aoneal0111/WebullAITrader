from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from app.broker_protocol.models import BrokerOrderRequest, BrokerPosition


class AtlasOrderState(StrEnum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class JournalEventType(StrEnum):
    ORDER_RECORDED = "ORDER_RECORDED"
    TRANSMISSION_STARTED = "TRANSMISSION_STARTED"
    STATE_CHANGED = "STATE_CHANGED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    REPLACE_REQUESTED = "REPLACE_REQUESTED"
    RECONCILED = "RECONCILED"
    DUPLICATE_REJECTED = "DUPLICATE_REJECTED"


class ReconciliationOutcome(StrEnum):
    HEALTHY = "HEALTHY"
    CORRECTED = "CORRECTED"
    ORPHANS_DETECTED = "ORPHANS_DETECTED"


class JournalHealthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    EMPTY = "EMPTY"
    CORRUPTED = "CORRUPTED"


TERMINAL_STATES = frozenset(
    {
        AtlasOrderState.FILLED,
        AtlasOrderState.CANCELLED,
        AtlasOrderState.REJECTED,
        AtlasOrderState.EXPIRED,
    }
)


@dataclass(frozen=True, slots=True)
class OrderJournalEntry:
    atlas_order_id: str
    broker_order_id: str | None
    request: BrokerOrderRequest
    state: AtlasOrderState
    filled_quantity: Decimal
    created_at: datetime
    updated_at: datetime
    parent_atlas_order_id: str | None = None
    root_atlas_order_id: str | None = None
    transmission_started: bool = False
    recovered: bool = False

    def __post_init__(self) -> None:
        _atlas_id(self.atlas_order_id)
        _aware(self.created_at)
        _aware(self.updated_at)
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.request.quantity < self.filled_quantity or self.filled_quantity < 0:
            raise ValueError("filled quantity is outside the order quantity")
        for value in (self.parent_atlas_order_id, self.root_atlas_order_id):
            if value is not None:
                _atlas_id(value)


@dataclass(frozen=True, slots=True)
class OrderJournalEvent:
    sequence_number: int
    atlas_order_id: str
    event_type: JournalEventType
    timestamp: datetime
    previous_state: AtlasOrderState | None
    state: AtlasOrderState
    broker_order_id: str | None
    reason: str
    filled_quantity: Decimal


@dataclass(frozen=True, slots=True)
class OutstandingOrders:
    orders: tuple[OrderJournalEntry, ...]


@dataclass(frozen=True, slots=True)
class RecoveredOrders:
    orders: tuple[OrderJournalEntry, ...]


@dataclass(frozen=True, slots=True)
class ReconciliationStatus:
    outcome: ReconciliationOutcome
    timestamp: datetime
    corrected_atlas_order_ids: tuple[str, ...]
    orphan_broker_order_ids: tuple[str, ...]
    missing_broker_atlas_order_ids: tuple[str, ...]
    positions: tuple[BrokerPosition, ...]


@dataclass(frozen=True, slots=True)
class JournalHealth:
    status: JournalHealthStatus
    order_count: int
    event_count: int
    message: str


@dataclass(frozen=True, slots=True)
class BrokerReliabilityReadModels:
    outstanding_orders: OutstandingOrders
    recovered_orders: RecoveredOrders
    reconciliation_status: ReconciliationStatus | None
    journal_health: JournalHealth


class DuplicateOrderError(ValueError):
    pass


class InvalidOrderTransitionError(ValueError):
    pass


class JournalCorruptionError(RuntimeError):
    pass


def _atlas_id(value: str) -> None:
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("atlas_order_id must be a UUID") from exc
    if str(parsed) != value.lower():
        raise ValueError("atlas_order_id must use canonical UUID form")


def _aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
