from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from PySide6.QtWidgets import QApplication

from app.broker_protocol.models import (
    BrokerAccount,
    BrokerCash,
    BrokerFill,
    BrokerOrder,
    BrokerOrderStatus,
    BrokerPosition,
)
from app.gui.models import PaperValidationDashboardSnapshot
from app.gui.presenters import PaperValidationPresenter
from app.gui.widgets.paper_validation_panel import PaperValidationPanel
from app.live_execution.paper_validation import (
    InMemoryPaperValidationEventStore,
    PaperOrderCancelled,
    PaperOrderFilled,
    PaperOrderSubmitted,
    PaperTradingValidator,
    PaperValidationCompleted,
    PaperValidationFailed,
    PaperValidationStarted,
    PaperValidationStatus,
)


NOW = datetime(2026, 7, 31, 15, tzinfo=UTC)
D = Decimal


class Records:
    def __init__(self):
        self.values = []

    def log(self, operation, status, **fields):
        self.values.append({"operation": operation, "status": status, **fields})


class PaperBroker:
    def __init__(self, statuses=(BrokerOrderStatus.FILLED, BrokerOrderStatus.FILLED)):
        self.statuses = list(statuses)
        self.connected = False
        self.submissions = []
        self.cancellations = []
        self.cash_values = [D("1000"), D("1000"), D("900"), D("1000")]
        self.position_values = [
            (),
            (BrokerPosition("AAPL", D("1"), D("100"), D("100")),),
            (),
        ]
        self.open_orders = ()
        self.account_error = False
        self.reconciliation_cash_change = False
        self._cash_calls = 0
        self._position_calls = 0

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def get_account(self):
        if self.account_error:
            raise RuntimeError("unavailable")
        return BrokerAccount("****1234", "PAPER", "ACTIVE")

    def get_cash(self):
        index = min(self._cash_calls, len(self.cash_values) - 1)
        value = self.cash_values[index]
        self._cash_calls += 1
        if self.reconciliation_cash_change and self._cash_calls >= 6:
            value -= D("1")
        return BrokerCash(value, D("0"), "USD", value, D("1000"))

    def get_positions(self):
        index = min(self._position_calls, len(self.position_values) - 1)
        self._position_calls += 1
        return self.position_values[index]

    def get_orders(self):
        return self.open_orders

    def get_fills(self):
        return ()

    def submit_order(self, request):
        self.submissions.append(request)
        status = self.statuses.pop(0)
        filled = request.quantity if status is BrokerOrderStatus.FILLED else D("0")
        return BrokerOrder(
            f"broker-{len(self.submissions)}", request.client_order_id, request.symbol,
            request.side, request.order_type, request.quantity, filled, request.limit_price,
            request.stop_price, request.time_in_force, status, NOW,
        )

    def cancel_order(self, client_order_id):
        self.cancellations.append(client_order_id)
        request = next(item for item in self.submissions if item.client_order_id == client_order_id)
        return BrokerOrder(
            f"broker-{self.submissions.index(request) + 1}", request.client_order_id,
            request.symbol, request.side, request.order_type, request.quantity, D("0"),
            request.limit_price, request.stop_price, request.time_in_force,
            BrokerOrderStatus.CANCELLED, NOW,
        )


def validator(broker, **changes):
    events = InMemoryPaperValidationEventStore()
    logs = Records()
    updates = []
    options = dict(
        environment="PAPER", event_store=events, logger=logs, status_sink=updates.append,
        clock=lambda: NOW, monotonic=lambda: 1.0, sleeper=lambda _: None,
        id_factory=iter(("run", "buy", "sell")).__next__, poll_attempts=2,
        poll_interval_seconds=0,
    )
    options.update(changes)
    return PaperTradingValidator(broker, **options), events, logs, updates


def test_account_and_filled_order_lifecycle_success():
    service, events, logs, updates = validator(PaperBroker())
    report = service.run()

    assert report.overall is PaperValidationStatus.PASS
    assert report.account.detail == "CONNECTED"
    assert report.orders.status is PaperValidationStatus.PASS
    assert report.positions.status is PaperValidationStatus.PASS
    assert len(service._broker.submissions) == 2
    assert service._broker.submissions[0].symbol == "AAPL"
    assert service._broker.submissions[0].quantity == D("1")
    assert [type(event) for event in events.events] == [
        PaperValidationStarted, PaperOrderSubmitted, PaperOrderFilled,
        PaperOrderSubmitted, PaperOrderFilled, PaperValidationCompleted,
    ]
    assert updates[0].overall is PaperValidationStatus.RUNNING
    assert updates[-1].overall is PaperValidationStatus.PASS
    assert all({"operation", "order_id", "status", "elapsed_ms", "endpoint",
                "environment", "fingerprint"} <= record.keys() for record in logs.values)
    assert not any("credential" in str(record).lower() for record in logs.values)


def test_account_failure_stops_before_order_submission():
    broker = PaperBroker()
    broker.account_error = True
    service, events, _, _ = validator(broker)

    report = service.run()

    assert report.account.status is PaperValidationStatus.FAIL
    assert report.overall is PaperValidationStatus.FAIL
    assert broker.submissions == []
    assert isinstance(events.events[-1], PaperValidationFailed)


def test_order_rejection_is_reported_and_not_cancelled():
    broker = PaperBroker((BrokerOrderStatus.REJECTED,))
    service, events, _, _ = validator(broker)

    report = service.run()

    assert report.orders.detail == "REJECTED"
    assert report.overall is PaperValidationStatus.FAIL
    assert broker.cancellations == []
    assert isinstance(events.events[-1], PaperValidationFailed)


def test_working_order_is_cancelled_and_buying_power_restored():
    broker = PaperBroker((BrokerOrderStatus.ACKNOWLEDGED,))
    broker.open_orders = ()
    broker.position_values = [(), (), ()]
    service, events, _, _ = validator(broker)

    report = service.run()

    assert report.overall is PaperValidationStatus.PASS
    assert report.orders.detail == "CANCELLED"
    assert len(broker.cancellations) == 1
    assert report.buying_power_values.before_trade == D("1000")
    assert report.buying_power_values.after_buy == D("900")
    assert report.buying_power_values.after_sell_or_cancel == D("1000")
    assert any(isinstance(event, PaperOrderCancelled) for event in events.events)


def test_buying_power_inconsistency_fails_validation():
    broker = PaperBroker((BrokerOrderStatus.ACKNOWLEDGED,))
    broker.cash_values = [D("1000"), D("1000"), D("1100"), D("1000")]
    broker.position_values = [(), (), ()]
    service, _, _, _ = validator(broker)

    report = service.run()

    assert report.buying_power.status is PaperValidationStatus.FAIL
    assert report.overall is PaperValidationStatus.FAIL


def test_reconciliation_detects_broker_change():
    broker = PaperBroker((BrokerOrderStatus.ACKNOWLEDGED,))
    broker.position_values = [(), (), ()]
    broker.reconciliation_cash_change = True
    service, _, _, _ = validator(broker)

    report = service.run()

    assert report.reconciliation.status is PaperValidationStatus.FAIL
    assert report.overall is PaperValidationStatus.FAIL


def test_gui_snapshot_and_panel_show_validation_statuses():
    application = QApplication.instance() or QApplication([])
    del application
    panel = PaperValidationPanel()
    presenter = PaperValidationPresenter(panel)
    service, _, _, updates = validator(PaperBroker(), status_sink=presenter)

    report = service.run()

    assert updates == []
    assert presenter.snapshot == PaperValidationDashboardSnapshot.from_report(report)
    assert panel.overall_badge.text() == "OVERALL: PASS"
    assert panel.status_badges["Account"].text() == "PASS"
    assert panel.status_badges["Reconciliation"].text() == "PASS"


@pytest.mark.parametrize("environment", ("LIVE", "PRODUCTION-LIVE"))
def test_live_mode_is_blocked_before_broker_or_events(environment):
    broker = PaperBroker()
    service, events, logs, updates = validator(broker, environment=environment)

    report = service.run()

    assert report.message == "Validation disabled in LIVE"
    assert report.overall is PaperValidationStatus.FAIL
    assert broker.connected is False
    assert broker.submissions == []
    assert events.events == []
    assert logs.values == []
    assert updates[-1] == report
