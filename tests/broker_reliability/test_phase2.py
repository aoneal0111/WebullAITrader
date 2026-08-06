from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from app.broker_protocol.models import (
    BrokerOrder,
    BrokerOrderRequest,
    BrokerOrderStatus,
    BrokerOrderType,
    BrokerPosition,
    BrokerSide,
    TimeInForce,
)
from app.broker_reliability import (
    AtlasOrderState,
    DuplicateOrderError,
    JournalEventType,
    JournalHealthStatus,
    PersistentOrderJournal,
    ReconciliationOutcome,
    ReliableOrderService,
)


NOW = datetime(2026, 8, 6, 15, 0, tzinfo=timezone.utc)
ID_1 = "8bdf5b0d-7ac2-4e29-8c8f-42fd5abc1111"
ID_2 = "8bdf5b0d-7ac2-4e29-8c8f-42fd5abc2222"


def request(client_order_id: str = "strategy-intent") -> BrokerOrderRequest:
    return BrokerOrderRequest(
        client_order_id,
        "AAPL",
        BrokerSide.BUY,
        BrokerOrderType.LIMIT,
        Decimal("10"),
        Decimal("190.25"),
        None,
        TimeInForce.DAY,
    )


def broker_order(
    atlas_id: str,
    *,
    broker_id: str = "broker-100",
    status: BrokerOrderStatus = BrokerOrderStatus.ACKNOWLEDGED,
    filled: Decimal = Decimal("0"),
) -> BrokerOrder:
    value = request(atlas_id)
    return BrokerOrder(
        broker_id,
        atlas_id,
        value.symbol,
        value.side,
        value.order_type,
        value.quantity,
        filled,
        value.limit_price,
        value.stop_price,
        value.time_in_force,
        status,
        NOW,
    )


class FakeBroker:
    def __init__(self):
        self.orders: list[BrokerOrder] = []
        self.positions: tuple[BrokerPosition, ...] = ()
        self.submit_calls = 0
        self.cancel_calls = 0
        self.replace_calls = 0

    def submit_order(self, value):
        self.submit_calls += 1
        result = broker_order(value.client_order_id, broker_id=f"broker-{self.submit_calls}")
        self.orders.append(result)
        return result

    def cancel_order(self, client_order_id):
        self.cancel_calls += 1
        current = next(item for item in self.orders if item.client_order_id == client_order_id)
        result = replace(current, status=BrokerOrderStatus.CANCELLED)
        self.orders[self.orders.index(current)] = result
        return result

    def replace_order(self, client_order_id, value):
        self.replace_calls += 1
        current = next(item for item in self.orders if item.client_order_id == client_order_id)
        result = replace(
            current,
            quantity=value.quantity,
            limit_price=value.limit_price,
            stop_price=value.stop_price,
            status=BrokerOrderStatus.ACKNOWLEDGED,
        )
        self.orders[self.orders.index(current)] = result
        return result

    def get_orders(self):
        return tuple(self.orders)

    def get_positions(self):
        return self.positions


def test_persistence_replays_identity_prices_and_transitions(tmp_path):
    path = tmp_path / "orders.sqlite3"
    first = PersistentOrderJournal(path)
    first.record_pending(ID_1, replace(request(), client_order_id=ID_1), NOW)
    first.mark_transmission_started(ID_1, NOW)
    first.transition(
        ID_1,
        AtlasOrderState.SUBMITTED,
        NOW,
        broker_order_id="broker-100",
    )
    first.close()

    replayed = PersistentOrderJournal(path)
    order = replayed.get(ID_1)
    assert UUID(order.atlas_order_id).version == 4
    assert order.broker_order_id == "broker-100"
    assert order.request.limit_price == Decimal("190.25")
    assert [event.event_type for event in replayed.events(ID_1)] == [
        JournalEventType.ORDER_RECORDED,
        JournalEventType.TRANSMISSION_STARTED,
        JournalEventType.STATE_CHANGED,
    ]
    assert replayed.verify().status is JournalHealthStatus.HEALTHY


def test_duplicate_submission_is_logged_and_never_reaches_broker(tmp_path, caplog):
    broker = FakeBroker()
    service = ReliableOrderService(PersistentOrderJournal(tmp_path / "orders.sqlite3"), broker)
    service.submit(request(), NOW, atlas_order_id=ID_1)

    with pytest.raises(DuplicateOrderError, match="duplicate Atlas order ID"):
        service.submit(request(), NOW, atlas_order_id=ID_1)

    assert broker.submit_calls == 1
    assert service.journal.events(ID_1)[-1].event_type is JournalEventType.DUPLICATE_REJECTED
    assert "duplicate_order_rejected" in caplog.text


def test_partial_fill_lifecycle_is_journaled_deterministically(tmp_path):
    broker = FakeBroker()
    service = ReliableOrderService(PersistentOrderJournal(tmp_path / "orders.sqlite3"), broker)
    service.submit(request(), NOW, atlas_order_id=ID_1)
    service.record_broker_update(
        broker_order(
            ID_1,
            broker_id="broker-1",
            status=BrokerOrderStatus.PARTIALLY_FILLED,
            filled=Decimal("4"),
        ),
        NOW,
    )
    service.record_broker_update(
        broker_order(
            ID_1,
            broker_id="broker-1",
            status=BrokerOrderStatus.FILLED,
            filled=Decimal("10"),
        ),
        NOW,
    )

    assert service.journal.get(ID_1).state is AtlasOrderState.FILLED
    assert [event.state for event in service.journal.events(ID_1)][-3:] == [
        AtlasOrderState.SUBMITTED,
        AtlasOrderState.PARTIALLY_FILLED,
        AtlasOrderState.FILLED,
    ]


def test_expired_lifecycle_is_supported(tmp_path):
    broker = FakeBroker()
    service = ReliableOrderService(PersistentOrderJournal(tmp_path / "orders.sqlite3"), broker)
    service.submit(request(), NOW, atlas_order_id=ID_1)
    service.record_broker_update(
        broker_order(ID_1, broker_id="broker-1", status=BrokerOrderStatus.EXPIRED), NOW
    )

    assert service.journal.get(ID_1).state is AtlasOrderState.EXPIRED


def test_cancel_replace_preserves_lineage_and_write_ahead_events(tmp_path):
    broker = FakeBroker()
    service = ReliableOrderService(PersistentOrderJournal(tmp_path / "orders.sqlite3"), broker)
    service.submit(request(), NOW, atlas_order_id=ID_1)
    service.replace(
        ID_1,
        replace(request(), quantity=Decimal("12"), limit_price=Decimal("189.50")),
        NOW,
        replacement_atlas_order_id=ID_2,
    )

    original = service.journal.get(ID_1)
    replacement = service.journal.get(ID_2)
    assert original.state is AtlasOrderState.CANCELLED
    assert replacement.parent_atlas_order_id == ID_1
    assert replacement.root_atlas_order_id == ID_1
    assert any(
        event.event_type is JournalEventType.REPLACE_REQUESTED
        for event in service.journal.events(ID_1)
    )
    broker.orders[0] = replace(
        broker.orders[0],
        status=BrokerOrderStatus.PARTIALLY_FILLED,
        filled_quantity=Decimal("4"),
    )
    service.record_broker_update(broker.orders[0], NOW)
    assert service.journal.get(ID_2).state is AtlasOrderState.PARTIALLY_FILLED
    service.cancel(ID_2, NOW)
    assert service.journal.get(ID_2).state is AtlasOrderState.CANCELLED
    assert broker.replace_calls == broker.cancel_calls == 1


def test_restart_reconciles_broker_ack_without_duplicate_transmission(tmp_path):
    path = tmp_path / "orders.sqlite3"
    broker = FakeBroker()
    before_crash = PersistentOrderJournal(path)
    outbound = replace(request(), client_order_id=ID_1)
    before_crash.record_pending(ID_1, outbound, NOW)
    before_crash.mark_transmission_started(ID_1, NOW)
    # Broker accepted the order, then Atlas crashed before journaling the response.
    broker.orders.append(broker_order(ID_1))
    before_crash.close()

    restarted = ReliableOrderService(PersistentOrderJournal(path), broker)
    status = restarted.recover(NOW)

    assert status.outcome is ReconciliationOutcome.CORRECTED
    assert restarted.journal.get(ID_1).state is AtlasOrderState.SUBMITTED
    assert restarted.journal.get(ID_1).recovered is True
    assert broker.submit_calls == 0
    assert restarted.read_models().recovered_orders.orders[0].atlas_order_id == ID_1


def test_reconciliation_detects_broker_and_journal_orphans_and_reads_positions(tmp_path):
    broker = FakeBroker()
    journal = PersistentOrderJournal(tmp_path / "orders.sqlite3")
    journal.record_pending(ID_1, replace(request(), client_order_id=ID_1), NOW)
    journal.mark_transmission_started(ID_1, NOW)
    broker.orders.append(broker_order(ID_2, broker_id="external-1"))
    broker.positions = (
        BrokerPosition("AAPL", Decimal("3"), Decimal("180"), Decimal("570")),
    )

    service = ReliableOrderService(journal, broker)
    status = service.recover(NOW)

    assert status.outcome is ReconciliationOutcome.ORPHANS_DETECTED
    assert status.orphan_broker_order_ids == ("external-1",)
    assert status.missing_broker_atlas_order_ids == (ID_1,)
    assert status.positions[0].symbol == "AAPL"
    assert service.read_models().outstanding_orders.orders[0].atlas_order_id == ID_1
