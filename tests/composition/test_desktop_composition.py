import json
from dataclasses import replace
from decimal import Decimal as D
from datetime import UTC, datetime
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
from app.trade_intelligence.decision_intelligence.service import HistoricalDecisionIntelligence
from app.market_data.models import MarketEvent, MarketEventType, QuotePayload
from tests.warrior_momentum.test_forward_capture import account, bar, bars, point
from tests.test_support.session_clock import session_timestamp
from app.trade_intelligence.taxonomy_paper_bridge import _discovery_context


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
        state = composition.state_store.snapshot()
        assert state.revision == 4
        assert state.paper_account is not None
        assert composition.chart_default_symbol is None
        assert composition.entry_opportunity_value_observer is not None
        assert composition.entry_opportunity_value_observer.metrics().enabled is False
        assert composition.adaptive_entry_research_observer is not None
        assert composition.adaptive_entry_research_observer.metrics().enabled is False
        assert composition.memory_observability is not None
        assert composition.memory_observability.enabled is False
    finally:
        composition.close(timeout_seconds=1.0)


def test_paper_entry_experiment_is_composed_only_by_explicit_paper_opt_in(
    monkeypatch, tmp_path,
) -> None:
    configuration = load_configuration({
        "WEBULL_TRADING_ENVIRONMENT": "PAPER",
        "ATLAS_HISTORICAL_ENTRY_EXPERIMENT_ENABLED": "true",
        "ATLAS_HISTORICAL_ENTRY_EXPERIMENT_MODE": "PAPER_TREATMENT",
        "ATLAS_HISTORICAL_ENTRY_EXPERIMENT_PATH": str(tmp_path / "experiment.sqlite3"),
        "WARRIOR_FORWARD_PAPER_ENABLED": "true",
        "ALLOWED_SYMBOLS": "XYZ",
    })
    monkeypatch.setattr(desktop_module, "load_configuration", lambda: configuration)
    composition = create_desktop_composition(
        paper_persistence_path=tmp_path / "paper.sqlite3",
    )
    try:
        policy = composition.paper_entry_intelligence
        assert policy is not None
        assert policy.config.enabled is True
        assert policy.config.mode == "PAPER_TREATMENT"
        assert policy._journal is not None
        assert composition.warrior_forward_sidecar is not None
        assert composition.warrior_forward_sidecar._paper_entry_intelligence is policy
    finally:
        composition.close(timeout_seconds=1.0)


def test_production_desktop_historical_treatment_full_lifecycle_survives_restart(
    monkeypatch, tmp_path,
) -> None:
    """Acceptance proof through the real desktop PAPER composition root."""
    intelligence_path = tmp_path / "intelligence.sqlite3"
    configuration = load_configuration({
        "WEBULL_TRADING_ENVIRONMENT": "PAPER",
        "ATLAS_HISTORICAL_ENTRY_EXPERIMENT_ENABLED": "true",
        "ATLAS_HISTORICAL_ENTRY_EXPERIMENT_MODE": "PAPER_TREATMENT",
        "ATLAS_HISTORICAL_ENTRY_EXPERIMENT_PATH": str(tmp_path / "experiment.sqlite3"),
        "WARRIOR_FORWARD_PAPER_ENABLED": "true",
        "ALLOWED_SYMBOLS": "XYZ",
    })
    monkeypatch.setattr(desktop_module, "load_configuration", lambda: configuration)
    monkeypatch.setattr(
        desktop_module, "HistoricalDecisionIntelligence",
        lambda: HistoricalDecisionIntelligence(journal_path=intelligence_path),
    )
    composition = create_desktop_composition(
        paper_persistence_path=tmp_path / "paper.sqlite3",
        paper_clock=lambda: datetime(2026, 8, 10, 14, 50, tzinfo=UTC),
    )
    pretrigger = (*bars()[:-1], bar(4, "9.96", "10", "9.94", "9.95", "300"))
    try:
        sidecar = composition.warrior_forward_sidecar
        assert sidecar is not None
        sidecar.start("PAPER")
        assert sidecar._service is not None
        assert sidecar._decision_intelligence_observer is not None

        # Allocation is adjusted only on this production-composed policy object
        # to make the deterministic fixture exercise TREATMENT.
        sidecar._paper_entry_intelligence.config = replace(
            sidecar._paper_entry_intelligence.config, allocation_percent=0,
        )
        observed_paper_events = []
        original_paper_event_observer = sidecar._paper_entry_intelligence.observe_paper_event
        sidecar._paper_entry_intelligence.observe_paper_event = lambda event: (
            observed_paper_events.append(event),
            original_paper_event_observer(event),
        )[1]
        candidate, signal = sidecar._service.observe(
            point(bars=pretrigger), account=account(),
        )
        assert candidate.setup is not None
        assert signal is not None
        paper = composition.paper_order_book
        assert paper is not None and len(paper.history()) == 1
        order = paper.history()[0]

        experiment = sidecar._paper_entry_intelligence._journal
        assert experiment is not None
        assignment = experiment._connection.execute(
            "SELECT assignment_id, assignment_identity, arm FROM experiment_assignments"
        ).fetchone()
        assert assignment is not None and assignment[2] == "TREATMENT"
        assignment_identity = assignment[1]
        assert experiment.assignment_for_lifecycle(
            order.request.strategy_lifecycle_id,
        )[0] == assignment[0]

        quote = MarketEvent(
            1, session_timestamp(1, at=datetime(2026, 8, 10, 14, 50, tzinfo=UTC)),
            "XYZ", "desktop-e2e-fill",
            MarketEventType.QUOTE,
            QuotePayload(signal.entry_trigger - D("0.01"), signal.entry_trigger,
                         D("1000"), D("1000")),
        )
        reports = composition.paper_trading_commands.gateway.process_market_event(quote)
        assert reports and reports[0].fills
        assert reports[0].fills[0].order_id == order.order_id
        projection = composition.runtime_projections.position_projection.snapshot
        assert any(item.symbol == "XYZ" and D(item.quantity) > 0 for item in projection.positions)

        intelligence = sidecar._decision_intelligence_observer
        event_types = {
            row[0] for row in intelligence._journal.execute(
                "SELECT event_type FROM opportunity_timeline"
            )
        }
        assert {"FIRST_RECOGNIZED", "TRIGGER_READY"} <= event_types
        execution_types = {
            row[0] for row in experiment._connection.execute(
                "SELECT event_type FROM experiment_execution_events"
            )
        }
        assert observed_paper_events
        assert experiment.assignment_for_lifecycle(
            observed_paper_events[-1].order.lifecycle_id,
        ) is not None
        assert {"ORDER_SUBMITTED", "FILLED"} <= execution_types

        # The ordinary control condition is evaluated later in the same
        # forward runtime, but remains shadow-only for this treatment-owned
        # opportunity.
        sidecar._service.observe(point(), account=account())
        shadow_types = {
            row[0] for row in experiment._connection.execute(
                "SELECT shadow_type FROM experiment_shadows"
            )
        }
        assert "CONTROL_DECISION" in shadow_types
        assert len(paper.history()) == 1

        # Recreate the production composition with the same durable stores.
        # The discovery engine must recover the triggered structural episode
        # before evaluating the retraced, bucket-advanced observation.
        composition.close(timeout_seconds=1.0)
        composition = create_desktop_composition(
            paper_persistence_path=tmp_path / "paper.sqlite3",
            paper_clock=lambda: datetime(2026, 8, 10, 14, 50, tzinfo=UTC),
        )
        sidecar = composition.warrior_forward_sidecar
        assert sidecar is not None
        sidecar.start("PAPER")
        sidecar._paper_entry_intelligence.config = replace(
            sidecar._paper_entry_intelligence.config, allocation_percent=0,
        )
        experiment = sidecar._paper_entry_intelligence._journal
        assert experiment is not None
        restored_context, _ = _discovery_context(point(bars=pretrigger))
        restored_batch = sidecar._decision_intelligence_observer._discovery.observe(
            restored_context,
        )
        real_entry_orders = tuple(
            item for item in composition.paper_order_book.history()
            if item.request.side.value == "BUY"
            and item.request.execution_reason == "ENTRY"
        )
        assert len(real_entry_orders) == 1
        assert sidecar._decision_intelligence_observer._discovery.memory_metrics()[
            "triggered_anchor_count"
        ] > 0
        assert not any(
            row.state.value == "TRIGGER_ARMED"
            for row in restored_batch.lifecycle_detections
        )
        assert experiment._connection.execute(
            "SELECT arm FROM experiment_assignments WHERE assignment_identity=?",
            (assignment_identity,),
        ).fetchone()[0] == "TREATMENT"
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

    class QueueMetrics:
        def memory_metrics(self):
            return {
                "current_depth": 4,
                "high_water_depth": 7,
                "messages_enqueued": 11,
                "messages_dequeued": 6,
            }

    class MetricsDriver(FakeDriver):
        def __init__(self):
            self._market_data = QueueMetrics()

    composition = create_desktop_composition(
        driver_factory=MetricsDriver,
        paper_persistence_path=tmp_path / "paper.sqlite3",
    )
    diagnostics = composition.memory_observability
    try:
        assert diagnostics is not None and diagnostics.enabled
        assert diagnostics._thread is not None and diagnostics._thread.is_alive()
        assert set(diagnostics._providers) == {
            "warrior_forward_runtime",
            "warrior_forward_report",
            "websocket_callback_queue",
            "trade_intelligence_runtime",
            "trade_intelligence_discovery_worker",
            "multi_strategy_discovery_engine",
            "realtime_scanner",
            "scanner_snapshot_publisher",
            "adaptive_entry_runtime",
            "adaptive_entry_worker",
            "crypto_research",
            "paper_order_book",
            "order_projection",
            "position_projection",
            "decision_projection",
            "health_projection",
            "watchlist_projection",
            "timeline_projection",
            "application_state",
            "operations_bus",
            "startup",
        }
        assert composition.runtime_service.start() is True
        sleep(0.01)
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
        cardinalities = dict(snapshot.metrics)
        assert cardinalities["websocket_callback_queue_current_depth"] == 4
        assert cardinalities["websocket_callback_queue_high_water_depth"] == 7
        assert cardinalities["websocket_callback_queue_messages_enqueued"] == 11
        assert cardinalities["websocket_callback_queue_messages_dequeued"] == 6
        assert cardinalities["paper_order_book_order_count"] == 0
        assert cardinalities["order_projection_order_count"] == 0
        assert cardinalities["position_projection_processed_fill_id_count"] == 0
        assert cardinalities["decision_projection_order_outcome_count"] == 0
        assert cardinalities["operations_bus_subscription_count"] > 0
        assert diagnostics.metrics()["failures"] == failures + 1
    finally:
        composition.runtime_service.stop()
        composition.runtime_service.wait(1.0)
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
