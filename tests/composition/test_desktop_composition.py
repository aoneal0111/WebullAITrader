from threading import Event

import app.composition.desktop_runtime as desktop_runtime_module
from app.composition import (
    DesktopComposition,
    create_desktop_composition,
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


def test_create_desktop_composition_returns_complete_graph() -> None:
    composition = create_desktop_composition()

    try:
        assert isinstance(composition, DesktopComposition)
        assert composition.runtime_service.status is RuntimeServiceStatus.STOPPED
        assert composition.runtime_service.cycles_completed == 0
        assert composition.state_store.snapshot().revision == 0
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

    def create_broker_driver(*, event_sink, account_snapshot_sink, source):
        driver = FakeDriver()
        created.append(
            (driver, event_sink, account_snapshot_sink, source)
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
        assert created[0][3] == "desktop-broker-runtime:1"

        composition.runtime_service.stop()
        assert composition.runtime_service.wait(1.0)
    finally:
        composition.close(timeout_seconds=1.0)
