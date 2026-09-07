import json
from threading import Event
from time import monotonic, sleep

import app.composition.desktop_runtime as desktop_runtime_module
import app.composition.desktop as desktop_module
from app.configuration import load_configuration
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
        assert composition.memory_observability is not None
        assert composition.memory_observability.enabled is False
    finally:
        composition.close(timeout_seconds=1.0)


def test_disabled_memory_observability_has_no_output(monkeypatch, tmp_path) -> None:
    output = tmp_path / "disabled-memory.jsonl"
    configuration = load_configuration({
        "ATLAS_MEMORY_OBSERVABILITY_PATH": str(output),
    })
    monkeypatch.setattr(desktop_module, "load_configuration", lambda: configuration)

    composition = create_desktop_composition(
        paper_persistence_path=tmp_path / "paper.sqlite3",
    )
    try:
        assert composition.memory_observability is not None
        assert composition.memory_observability.enabled is False
        assert not output.exists()
    finally:
        composition.close(timeout_seconds=1.0)

    assert not output.exists()


def test_enabled_memory_observability_composes_real_providers_and_jsonl(
    monkeypatch, tmp_path,
) -> None:
    output = tmp_path / "premarket-memory.jsonl"
    configuration = load_configuration({
        "ATLAS_MEMORY_OBSERVABILITY_ENABLED": "true",
        "ATLAS_MEMORY_OBSERVABILITY_PATH": str(output),
    })
    monkeypatch.setattr(desktop_module, "load_configuration", lambda: configuration)

    composition = create_desktop_composition(
        paper_persistence_path=tmp_path / "paper.sqlite3",
    )
    diagnostics = composition.memory_observability
    try:
        assert diagnostics is not None and diagnostics.enabled
        assert diagnostics._thread is not None and diagnostics._thread.is_alive()
        assert set(diagnostics._providers) == {
            "warrior_forward_runtime",
            "trade_intelligence_runtime",
            "trade_intelligence_discovery_worker",
            "multi_strategy_discovery_engine",
            "realtime_scanner",
            "adaptive_entry_runtime",
            "adaptive_entry_worker",
            "timeline_projection",
        }
        deadline = monotonic() + 1.0
        while not output.exists() and monotonic() < deadline:
            sleep(0.01)
        assert output.exists()
        failures = diagnostics.metrics()["failures"]
        diagnostics._providers["failing_provider"] = lambda: (
            _ for _ in ()
        ).throw(RuntimeError("provider failure"))
        snapshot = diagnostics.sample()
        assert snapshot is not None
        assert diagnostics.metrics()["failures"] == failures + 1
    finally:
        composition.close(timeout_seconds=1.0)

    rows = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows
    assert rows[0]["metrics"]["lifecycle_startup"] == 1
    assert "timeline_projection_timeline_count" in rows[0]["metrics"]
    assert rows[-1]["metrics"]["lifecycle_shutdown"] == 1


def test_memory_provider_and_close_failures_cannot_block_runtime_shutdown(
    monkeypatch, tmp_path,
) -> None:
    class FailingMemoryObservability:
        enabled = True

        def __init__(self, providers, **_kwargs):
            self.providers = providers
            self.started = False
            self.closed = False

        def record_lifecycle(self, _event):
            return None

        def start(self):
            self.started = True
            self.providers["timeline_projection"] = lambda: (_ for _ in ()).throw(
                RuntimeError("provider failure")
            )

        def close(self, **_kwargs):
            self.closed = True
            raise RuntimeError("diagnostic close failure")

    configuration = load_configuration({
        "ATLAS_MEMORY_OBSERVABILITY_ENABLED": "true",
        "ATLAS_MEMORY_OBSERVABILITY_PATH": str(tmp_path / "memory.jsonl"),
    })
    monkeypatch.setattr(desktop_module, "load_configuration", lambda: configuration)
    monkeypatch.setattr(
        desktop_module, "MemoryObservability", FailingMemoryObservability,
    )
    composition = create_desktop_composition(
        driver_factory=lambda: FakeDriver(),
        paper_persistence_path=tmp_path / "paper.sqlite3",
    )
    diagnostics = composition.memory_observability
    assert diagnostics is not None and diagnostics.started
    assert composition.runtime_service.start()
    assert composition.close(timeout_seconds=1.0)
    assert diagnostics.closed
    assert composition.runtime_service.status is RuntimeServiceStatus.STOPPED


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
