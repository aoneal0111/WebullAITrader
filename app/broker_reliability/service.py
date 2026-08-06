from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime
from typing import Callable
from uuid import uuid4

from app.broker_protocol.models import BrokerOrder, BrokerOrderRequest
from app.broker_reliability.journal import PersistentOrderJournal
from app.broker_reliability.models import (
    AtlasOrderState,
    BrokerReliabilityReadModels,
    DuplicateOrderError,
    JournalEventType,
    OutstandingOrders,
    RecoveredOrders,
    _aware,
)
from app.broker_reliability.reconciliation import ReconciliationService, _state


class ReliableOrderService:
    """The write-ahead, duplicate-safe boundary for outbound broker orders."""

    def __init__(
        self,
        journal: PersistentOrderJournal,
        broker,
        *,
        atlas_id_factory: Callable[[], str] | None = None,
        logger: logging.Logger | None = None,
    ):
        self.journal = journal
        self.broker = broker
        self._atlas_id_factory = atlas_id_factory or (lambda: str(uuid4()))
        self._logger = logger or logging.getLogger(__name__)
        self.reconciliation = ReconciliationService(journal, broker)

    def submit(
        self,
        request: BrokerOrderRequest,
        timestamp: datetime,
        *,
        atlas_order_id: str | None = None,
    ) -> BrokerOrder:
        _aware(timestamp)
        atlas_id = atlas_order_id or self._atlas_id_factory()
        outbound = replace(request, client_order_id=atlas_id)
        try:
            self.journal.record_pending(atlas_id, outbound, timestamp)
            self.journal.mark_transmission_started(atlas_id, timestamp)
        except DuplicateOrderError:
            self._logger.warning(
                "duplicate_order_rejected",
                extra={"atlas_order_id": atlas_id, "reason": "already present in journal"},
            )
            raise
        response = self.broker.submit_order(outbound)
        self._apply_response(atlas_id, response, timestamp, "broker submission acknowledged")
        return response

    def cancel(self, atlas_order_id: str, timestamp: datetime) -> BrokerOrder:
        current = self.journal.get(atlas_order_id)
        self.journal.record_operation(
            atlas_order_id,
            JournalEventType.CANCEL_REQUESTED,
            timestamp,
            "cancellation durably recorded before transmission",
        )
        response = self.broker.cancel_order(current.request.client_order_id)
        self._apply_response(atlas_order_id, response, timestamp, "broker cancellation response")
        return response

    def replace(
        self,
        atlas_order_id: str,
        replacement: BrokerOrderRequest,
        timestamp: datetime,
        *,
        replacement_atlas_order_id: str | None = None,
    ) -> BrokerOrder:
        original = self.journal.get(atlas_order_id)
        child_id = replacement_atlas_order_id or self._atlas_id_factory()
        outbound = replace(replacement, client_order_id=original.request.client_order_id)
        try:
            self.journal.record_pending(
                child_id, outbound, timestamp, parent_atlas_order_id=atlas_order_id
            )
            self.journal.record_operation(
                atlas_order_id,
                JournalEventType.REPLACE_REQUESTED,
                timestamp,
                f"replacement linked to Atlas order {child_id}",
            )
            self.journal.mark_transmission_started(
                child_id, timestamp, "replacement broker transmission started"
            )
        except DuplicateOrderError:
            self._logger.warning(
                "duplicate_replacement_rejected",
                extra={"atlas_order_id": child_id, "reason": "already present in journal"},
            )
            raise
        response = self.broker.replace_order(original.request.client_order_id, outbound)
        self.journal.transition(
            atlas_order_id,
            AtlasOrderState.CANCELLED,
            timestamp,
            reason=f"superseded by Atlas order {child_id}",
        )
        self._apply_response(child_id, response, timestamp, "broker replacement response")
        return response

    def record_broker_update(self, broker_order: BrokerOrder, timestamp: datetime) -> None:
        local = self.journal.find_by_broker_order_id(broker_order.broker_order_id)
        if local is None:
            candidates = tuple(
                order
                for order in self.journal.orders()
                if order.request.client_order_id == broker_order.client_order_id
            )
            if not candidates:
                raise KeyError("broker update has no Atlas order")
            local = candidates[-1]
        self._apply_response(local.atlas_order_id, broker_order, timestamp, "broker lifecycle update")

    def recover(self, timestamp: datetime):
        """Reload is implicit in journal construction; reconciliation never blind-replays."""
        return self.reconciliation.reconcile(timestamp)

    def read_models(self) -> BrokerReliabilityReadModels:
        return BrokerReliabilityReadModels(
            outstanding_orders=OutstandingOrders(self.journal.outstanding()),
            recovered_orders=RecoveredOrders(self.journal.recovered()),
            reconciliation_status=self.reconciliation.last_status,
            journal_health=self.journal.verify(),
        )

    def _apply_response(
        self, atlas_order_id: str, response: BrokerOrder, timestamp: datetime, reason: str
    ) -> None:
        state = _state(response)
        self.journal.transition(
            atlas_order_id,
            state,
            timestamp,
            broker_order_id=response.broker_order_id,
            filled_quantity=response.filled_quantity,
            reason=reason,
        )
