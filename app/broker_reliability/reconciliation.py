from __future__ import annotations

from datetime import datetime

from app.broker_protocol.models import BrokerOrder, BrokerOrderStatus
from app.broker_reliability.journal import PersistentOrderJournal
from app.broker_reliability.models import (
    AtlasOrderState,
    JournalEventType,
    ReconciliationOutcome,
    ReconciliationStatus,
    TERMINAL_STATES,
    _aware,
)


_BROKER_STATES = {
    "NEW": AtlasOrderState.PENDING,
    "SUBMITTED": AtlasOrderState.SUBMITTED,
    "ACKNOWLEDGED": AtlasOrderState.SUBMITTED,
    "PARTIALLY_FILLED": AtlasOrderState.PARTIALLY_FILLED,
    "FILLED": AtlasOrderState.FILLED,
    "CANCELLED": AtlasOrderState.CANCELLED,
    "REJECTED": AtlasOrderState.REJECTED,
    "EXPIRED": AtlasOrderState.EXPIRED,
}


class ReconciliationService:
    """Compares all broker orders and positions with the durable Atlas journal."""

    def __init__(self, journal: PersistentOrderJournal, broker):
        self._journal = journal
        self._broker = broker
        self._last_status: ReconciliationStatus | None = None

    @property
    def last_status(self) -> ReconciliationStatus | None:
        return self._last_status

    def reconcile(self, timestamp: datetime) -> ReconciliationStatus:
        _aware(timestamp)
        broker_orders = tuple(self._broker.get_orders())
        positions = tuple(self._broker.get_positions())
        journal_orders = self._journal.orders()
        by_client: dict[str, list] = {}
        by_broker: dict[str, list] = {}
        for order in journal_orders:
            by_client.setdefault(order.request.client_order_id, []).append(order)
            if order.broker_order_id:
                by_broker.setdefault(order.broker_order_id, []).append(order)

        corrected: list[str] = []
        orphans: list[str] = []
        seen: set[str] = set()
        for broker_order in sorted(
            broker_orders, key=lambda item: (item.updated_timestamp, item.broker_order_id)
        ):
            matches = by_broker.get(broker_order.broker_order_id, [])
            if not matches:
                matches = by_client.get(broker_order.client_order_id, [])
            if not matches:
                orphans.append(broker_order.broker_order_id)
                continue
            # A replacement lineage can share the broker's client identifier. The
            # newest non-terminal child is the authoritative projected order.
            local = next(
                (item for item in reversed(matches) if item.state not in TERMINAL_STATES),
                matches[-1],
            )
            seen.add(local.atlas_order_id)
            desired = _state(broker_order)
            changed = (
                local.state is not desired
                or local.broker_order_id != broker_order.broker_order_id
                or local.filled_quantity != broker_order.filled_quantity
                or not local.recovered
            )
            if changed:
                self._journal.transition(
                    local.atlas_order_id,
                    desired,
                    timestamp,
                    broker_order_id=broker_order.broker_order_id,
                    filled_quantity=broker_order.filled_quantity,
                    reason="broker state applied during reconciliation",
                    recovered=True,
                    event_type=JournalEventType.RECONCILED,
                )
                corrected.append(local.atlas_order_id)

        missing = tuple(
            sorted(
                order.atlas_order_id
                for order in journal_orders
                if order.state not in TERMINAL_STATES
                and order.atlas_order_id not in seen
                and order.transmission_started
            )
        )
        if orphans or missing:
            outcome = ReconciliationOutcome.ORPHANS_DETECTED
        elif corrected:
            outcome = ReconciliationOutcome.CORRECTED
        else:
            outcome = ReconciliationOutcome.HEALTHY
        self._last_status = ReconciliationStatus(
            outcome=outcome,
            timestamp=timestamp,
            corrected_atlas_order_ids=tuple(sorted(corrected)),
            orphan_broker_order_ids=tuple(sorted(orphans)),
            missing_broker_atlas_order_ids=missing,
            positions=positions,
        )
        return self._last_status


def _state(order: BrokerOrder) -> AtlasOrderState:
    value = order.status.value if isinstance(order.status, BrokerOrderStatus) else str(order.status)
    try:
        return _BROKER_STATES[value]
    except KeyError as exc:
        raise ValueError(f"unsupported broker order status: {value}") from exc
