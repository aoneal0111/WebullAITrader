from datetime import UTC, datetime

from app.gui.models import HealthDashboardSnapshot
from app.gui.presenters import HealthPresenter
from app.operations_core import ApplicationState
from app.read_models.health import HealthState


class View:
    def __init__(self) -> None:
        self.snapshot = None

    def render(self, snapshot: HealthDashboardSnapshot) -> None:
        self.snapshot = snapshot


def test_health_presenter_prepares_immutable_dashboard_model() -> None:
    view = View()
    presenter = HealthPresenter(view)
    state = ApplicationState(
        health_projection=HealthState(
            runtime_status="RUNNING",
            broker_status="CONNECTED",
            market_data_status="CONNECTED",
            trading_environment="TEST",
            trading_rest_status="CONNECTED",
            orders_status="ENABLED",
            balances_status="CONNECTED",
            market_data_environment="PRODUCTION",
            market_data_rest_status="CONNECTED",
            streaming_status="CONNECTED",
            subscription_status="ACCEPTED",
            heartbeat_status="OK",
            reconnect_status="READY",
            entitlement_status="GRANTED",
            probe_aapl_status="SUPPORTED",
            probe_spy_status="SUPPORTED",
            probe_tsla_status="SUPPORTED",
            scanner_status="ACTIVE",
            supported_symbols=7300,
            ai_status="READY",
            risk_status=None,
            persistence_status="READY",
            last_warning="Storage nearing capacity.",
            last_heartbeat=datetime(2026, 7, 30, 15, 0, tzinfo=UTC),
            connection_latency="12.5",
            reconnect_attempts=2,
            healthy=True,
            degraded=False,
        )
    )

    presenter.render(state)

    assert view.snapshot.overall_status == "HEALTHY"
    assert view.snapshot.status_level == "good"
    assert ("Latency", "12.5 ms") in view.snapshot.metrics
    assert ("Runtime", "RUNNING") in view.snapshot.metrics
    assert ("Broker", "CONNECTED") in view.snapshot.metrics
    assert ("Market Data", "CONNECTED") in view.snapshot.metrics
    assert ("Trading Environment", "TEST") in view.snapshot.metrics
    assert ("Orders", "ENABLED") in view.snapshot.metrics
    assert ("Market Data Environment", "PRODUCTION") in view.snapshot.metrics
    assert ("Entitlement", "GRANTED") in view.snapshot.metrics
    assert ("Subscription", "ACCEPTED") in view.snapshot.metrics
    assert ("Probe AAPL", "SUPPORTED") in view.snapshot.metrics
    assert ("Supported Symbols", "7300") in view.snapshot.metrics
    assert ("Scanner", "ACTIVE") in view.snapshot.metrics
    assert ("AI", "READY") in view.snapshot.metrics
    assert ("Persistence", "READY") in view.snapshot.metrics
    assert ("Heartbeat", "10:00:00") in view.snapshot.metrics
    assert ("Reconnects", "2") in view.snapshot.metrics
    assert view.snapshot.incident == "Storage nearing capacity."
