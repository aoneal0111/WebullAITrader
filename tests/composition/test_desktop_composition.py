from threading import Event

import app.composition.desktop_runtime as desktop_runtime_module
from app.composition import (
    DesktopComposition,
    create_desktop_composition,
)
from app.composition.desktop_runtime_config import (
    DesktopRuntimeConfiguration,
)
from app.composition.runtime_mode import RuntimeMode
from app.services import RuntimeServiceStatus
from app.strategies.warrior_momentum.autonomous_paper import AutonomousPaperReadiness


class FakeDriver:
    environment = "PAPER"
    active_model = "fake-model"
    cycles_completed = 0

    def run(self, *, stop_event: Event, cycle_sink):
        while not stop_event.is_set():
            cycle_sink(1)
            stop_event.wait(0.01)


def test_create_desktop_composition_returns_complete_graph() -> None:
    composition = create_desktop_composition()

    try:
        assert isinstance(composition, DesktopComposition)
        assert composition.runtime_service.status is RuntimeServiceStatus.STOPPED
        assert composition.runtime_service.cycles_completed == 0
        assert composition.state_store.snapshot().revision == 0
        assert composition.chart_default_symbol is None
        assert composition.entry_opportunity_value_observer is not None
        assert composition.entry_opportunity_value_observer.metrics().enabled is False
        assert composition.adaptive_entry_research_observer is not None
        assert composition.adaptive_entry_research_observer.metrics().enabled is False
    finally:
        composition.close(timeout_seconds=1.0)


def test_desktop_composition_reconciles_paper_execution_before_ready(tmp_path) -> None:
    composition = create_desktop_composition(
        paper_persistence_path=tmp_path / "paper.sqlite3",
    )
    try:
        assert composition.autonomous_paper_bridge is not None
        assert composition.autonomous_paper_bridge.readiness is AutonomousPaperReadiness.READY
    finally:
        composition.close(timeout_seconds=1.0)


def test_desktop_composition_uses_fresh_dependencies() -> None:
    first = create_desktop_composition()
    second = create_desktop_composition()

    try:
        assert first is not second
        assert first.bus is not second.bus
        assert first.state_store is not second.state_store
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


def test_desktop_composition_defaults_to_configured_broker_driver(
    monkeypatch,
) -> None:
    created = []

    def create_broker_driver(
        *,
        event_sink,
        account_snapshot_sink,
        configuration_loader,
        market_event_observer,
        source,
    ):
        driver = FakeDriver()
        created.append(
            (
                driver,
                event_sink,
                account_snapshot_sink,
                configuration_loader,
                market_event_observer,
                source,
            )
        )
        return driver

    monkeypatch.setattr(
        desktop_runtime_module,
        "create_configured_desktop_broker_driver",
        create_broker_driver,
    )
    composition = create_desktop_composition()

    try:
        assert composition.runtime_service.start() is True
        assert composition.runtime_service.wait(0.05) is False
        assert len(created) == 1
        assert callable(created[0][1])
        assert callable(created[0][2])
        assert callable(created[0][3])
        assert callable(created[0][4])
        assert created[0][5] == "desktop-broker-runtime:1"

        composition.runtime_service.stop()
        assert composition.runtime_service.wait(1.0)
    finally:
        composition.close(timeout_seconds=1.0)


def test_simulation_mode_does_not_construct_configured_broker_stream(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        desktop_runtime_module,
        "create_configured_desktop_broker_driver",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError(
                "simulation must not construct broker market data"
            )
        ),
    )
    composition = create_desktop_composition(
        configuration=DesktopRuntimeConfiguration(
            runtime_mode=RuntimeMode.SIMULATED,
        )
    )

    try:
        assert composition.runtime_service.start() is True
        assert composition.runtime_service.wait(0.05) is False
        composition.runtime_service.stop()
        assert composition.runtime_service.wait(2.0)
    finally:
        composition.close(timeout_seconds=1.0)
