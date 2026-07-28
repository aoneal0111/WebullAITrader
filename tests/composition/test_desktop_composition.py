from datetime import datetime, timezone
from decimal import Decimal
from threading import Event

from app.composition import (
    DesktopComposition,
    create_desktop_composition,
)
from app.operations_core import (
    DecisionsUpdated,
    OperationsDecision,
    OperatorDecisionSelected,
    RuntimeStarted,
)
from app.read_models.decisions import DecisionProjector
from app.read_models.runtime_health import (
    OverallHealth,
    RuntimeHealthProjector,
)
from app.read_models.operator_workspace import (
    OperatorWorkspaceProjector,
    WorkspaceSelectionSource,
)
from app.read_models.timeline import TimelineProjector
from app.read_models.trade_lifecycle import (
    TradeLifecycleProjector,
    TradeLifecycleStatus,
)
from app.services import RuntimeServiceStatus


class FakeDriver:
    environment = "PAPER"
    active_model = "fake-model"
    cycles_completed = 0

    def run(self, *, stop_event: Event, cycle_sink):
        while not stop_event.is_set():
            cycle_sink(1)
            stop_event.wait(0.01)


NOW = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)


def test_create_desktop_composition_returns_complete_graph() -> None:
    composition = create_desktop_composition()

    try:
        assert isinstance(composition, DesktopComposition)
        assert composition.runtime_service.status is RuntimeServiceStatus.STOPPED
        assert composition.runtime_service.cycles_completed == 0
        assert composition.state_store.snapshot().revision == 0
        assert isinstance(composition.decision_projector, DecisionProjector)
        assert composition.decision_projector.snapshot().decisions == ()
        assert isinstance(
            composition.runtime_health_projector,
            RuntimeHealthProjector,
        )
        assert isinstance(
            composition.timeline_projector,
            TimelineProjector,
        )
        assert isinstance(
            composition.trade_lifecycle_projector,
            TradeLifecycleProjector,
        )
        assert isinstance(
            composition.operator_workspace_projector,
            OperatorWorkspaceProjector,
        )
    finally:
        composition.close(timeout_seconds=1.0)


def test_desktop_composition_uses_fresh_dependencies() -> None:
    first = create_desktop_composition()
    second = create_desktop_composition()

    try:
        assert first is not second
        assert first.bus is not second.bus
        assert first.state_store is not second.state_store
        assert first.decision_projector is not second.decision_projector
        assert (
            first.runtime_health_projector
            is not second.runtime_health_projector
        )
        assert first.timeline_projector is not second.timeline_projector
        assert (
            first.trade_lifecycle_projector
            is not second.trade_lifecycle_projector
        )
        assert (
            first.operator_workspace_projector
            is not second.operator_workspace_projector
        )
        assert first.runtime_service is not second.runtime_service
    finally:
        first.close(timeout_seconds=1.0)
        second.close(timeout_seconds=1.0)


def test_desktop_composition_accepts_driver_factory() -> None:
    created = []

    def factory():
        driver = FakeDriver()
        created.append(driver)
        return driver

    composition = create_desktop_composition(driver_factory=factory)

    try:
        assert composition.runtime_service.status is RuntimeServiceStatus.STOPPED

        assert composition.runtime_service.start() is True
        assert composition.runtime_service.wait(1.0) is False

        assert len(created) == 1

        composition.runtime_service.stop()
        assert composition.runtime_service.wait(1.0)
    finally:
        composition.close(timeout_seconds=1.0)


def test_composed_state_notifications_observe_latest_decision_snapshot() -> None:
    composition = create_desktop_composition()
    observed_cycles: list[int | None] = []
    listener_id = composition.state_store.subscribe(
        lambda state: observed_cycles.append(
            composition.decision_projector.snapshot().cycle
        )
    )
    try:
        composition.bus.publish(
            DecisionsUpdated(
                cycle=2,
                occurred_at=NOW,
                decisions=(
                    OperationsDecision(
                        symbol="AAPL",
                        action="HOLD",
                        confidence=50,
                        score=Decimal("0.5"),
                        reasons=("waiting",),
                        source_action="HOLD",
                        position_quantity=Decimal("0"),
                        strategy_version="1.0",
                        decided_at=NOW,
                    ),
                ),
            )
        )

        assert observed_cycles == [None, 2]
    finally:
        composition.state_store.unsubscribe(listener_id)
        composition.close(timeout_seconds=1.0)


def test_composed_state_notifications_observe_latest_health_snapshot() -> None:
    composition = create_desktop_composition()
    observed_health: list[OverallHealth] = []
    listener_id = composition.state_store.subscribe(
        lambda state: observed_health.append(
            composition.runtime_health_projector.snapshot().overall_health
        )
    )
    try:
        composition.bus.publish(
            RuntimeStarted(
                active_model="atlas",
                occurred_at=NOW,
            )
        )

        assert observed_health == [
            OverallHealth.UNKNOWN,
            OverallHealth.HEALTHY,
        ]
    finally:
        composition.state_store.unsubscribe(listener_id)
        composition.close(timeout_seconds=1.0)


def test_close_releases_all_composed_bus_subscriptions() -> None:
    composition = create_desktop_composition()

    assert composition.bus.subscription_count == 7
    composition.close(timeout_seconds=1.0)

    assert composition.bus.subscription_count == 0


def test_state_notifications_observe_latest_workspace_selection() -> None:
    composition = create_desktop_composition()
    observed: list[tuple[str | None, WorkspaceSelectionSource]] = []
    listener_id = composition.state_store.subscribe(
        lambda state: observed.append(
            (
                composition.operator_workspace_projector
                .snapshot()
                .selected_symbol,
                composition.operator_workspace_projector
                .snapshot()
                .selection_source,
            )
        )
    )
    try:
        composition.bus.publish(
            OperatorDecisionSelected(
                symbol="AAPL",
                decision_id="decision-1",
                occurred_at=NOW,
            )
        )

        assert observed == [
            (None, WorkspaceSelectionSource.NONE),
            ("AAPL", WorkspaceSelectionSource.DECISION),
        ]
    finally:
        composition.state_store.unsubscribe(listener_id)
        composition.close(timeout_seconds=1.0)


def test_state_notifications_observe_latest_timeline_entry() -> None:
    composition = create_desktop_composition()
    observed_titles: list[str | None] = []
    listener_id = composition.state_store.subscribe(
        lambda state: observed_titles.append(
            (
                composition.timeline_projector.snapshot().entries[0].title
                if composition.timeline_projector.snapshot().entries
                else None
            )
        )
    )
    try:
        composition.bus.publish(
            RuntimeStarted(
                active_model="atlas",
                occurred_at=NOW,
            )
        )

        assert observed_titles == [None, "Runtime started"]
    finally:
        composition.state_store.unsubscribe(listener_id)
        composition.close(timeout_seconds=1.0)


def test_state_notifications_observe_latest_trade_lifecycle() -> None:
    composition = create_desktop_composition()
    observed_statuses: list[TradeLifecycleStatus | None] = []
    listener_id = composition.state_store.subscribe(
        lambda state: observed_statuses.append(
            (
                composition.trade_lifecycle_projector
                .snapshot()
                .lifecycles[0]
                .status
                if composition.trade_lifecycle_projector
                .snapshot()
                .lifecycles
                else None
            )
        )
    )
    try:
        composition.bus.publish(
            DecisionsUpdated(
                cycle=1,
                decisions=(
                    OperationsDecision(
                        symbol="AAPL",
                        action="ENTER_LONG",
                        confidence=80,
                        score=Decimal("0.8"),
                        reasons=("approved",),
                        source_action="BUY",
                        position_quantity=Decimal("0"),
                        strategy_version="1.0",
                        decided_at=NOW,
                    ),
                ),
                occurred_at=NOW,
            )
        )

        assert observed_statuses == [
            None,
            TradeLifecycleStatus.OPEN,
        ]
    finally:
        composition.state_store.unsubscribe(listener_id)
        composition.close(timeout_seconds=1.0)
