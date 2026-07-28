from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.operations_core import (
    ApplicationStateStore,
    OperationsBus,
    OperationsEvent,
)
from app.event_store import (
    EventStoreController,
    EventStoreQueryEngine,
    EventStoreRepository,
)
from app.analytics import (
    AnalyticsController,
    AnalyticsEngine,
    AnalyticsRepository,
)
from app.order_cancellation import OrderCancellationRuntime
from app.order_placement import OrderPlacementRuntime
from app.paper_trading.order_book import PaperOrderBook
from app.read_models.decisions import DecisionProjector
from app.read_models.operator_workspace import OperatorWorkspaceProjector
from app.read_models.runtime_health import RuntimeHealthProjector
from app.read_models.timeline import TimelineProjector
from app.read_models.trade_lifecycle import TradeLifecycleProjector
from app.recording import (
    RecordingController,
    RecordingReader,
    RecordingSerializer,
    RecordingWriter,
    SessionRecorder,
)
from app.replay import (
    ReplayClock,
    ReplayController,
    ReplayEngine,
    ReplayEventArchive,
)
from app.services import OrderCommandFactory, RuntimeService, TradingService

from .desktop_runtime import create_desktop_runtime_service
from .desktop_runtime_config import DesktopRuntimeConfiguration
from app.paper_trading.command_composition import (
    PaperTradingCommandComposition,
    create_paper_trading_command_composition,
)


@dataclass(slots=True)
class ReplayProjectionGraph:
    """Dedicated replay projections rebuilt for backward navigation."""

    bus: OperationsBus
    state_store: ApplicationStateStore
    decision_projector: DecisionProjector
    runtime_health_projector: RuntimeHealthProjector
    timeline_projector: TimelineProjector
    trade_lifecycle_projector: TradeLifecycleProjector
    operator_workspace_projector: OperatorWorkspaceProjector
    _closed: bool = False

    @classmethod
    def create(cls) -> "ReplayProjectionGraph":
        bus = OperationsBus()
        decision_projector = DecisionProjector(bus)
        runtime_health_projector = RuntimeHealthProjector(bus)
        timeline_projector = TimelineProjector(bus)
        trade_lifecycle_projector = TradeLifecycleProjector(bus)
        operator_workspace_projector = OperatorWorkspaceProjector(bus)
        state_store = ApplicationStateStore(bus)
        return cls(
            bus=bus,
            state_store=state_store,
            decision_projector=decision_projector,
            runtime_health_projector=runtime_health_projector,
            timeline_projector=timeline_projector,
            trade_lifecycle_projector=trade_lifecycle_projector,
            operator_workspace_projector=operator_workspace_projector,
        )

    def reset(
        self,
        events: tuple[OperationsEvent, ...],
    ) -> OperationsBus:
        self.close()
        replacement = type(self).create()
        self.bus = replacement.bus
        self.state_store = replacement.state_store
        self.decision_projector = replacement.decision_projector
        self.runtime_health_projector = (
            replacement.runtime_health_projector
        )
        self.timeline_projector = replacement.timeline_projector
        self.trade_lifecycle_projector = (
            replacement.trade_lifecycle_projector
        )
        self.operator_workspace_projector = (
            replacement.operator_workspace_projector
        )
        self._closed = False
        for event in events:
            self.bus.publish(event)
        return self.bus

    def close(self) -> None:
        if self._closed:
            return
        self.state_store.close()
        self.decision_projector.close()
        self.runtime_health_projector.close()
        self.timeline_projector.close()
        self.trade_lifecycle_projector.close()
        self.operator_workspace_projector.close()
        self._closed = True


@dataclass(slots=True)
class DesktopComposition:
    bus: OperationsBus
    state_store: ApplicationStateStore
    decision_projector: DecisionProjector
    runtime_health_projector: RuntimeHealthProjector
    timeline_projector: TimelineProjector
    trade_lifecycle_projector: TradeLifecycleProjector
    operator_workspace_projector: OperatorWorkspaceProjector
    session_recorder: SessionRecorder
    recording_serializer: RecordingSerializer
    recording_writer: RecordingWriter
    recording_reader: RecordingReader
    recording_controller: RecordingController
    event_store_repository: EventStoreRepository
    event_store_controller: EventStoreController
    analytics_repository: AnalyticsRepository
    analytics_engine: AnalyticsEngine
    analytics_controller: AnalyticsController
    replay_archive: ReplayEventArchive
    replay_clock: ReplayClock
    replay_engine: ReplayEngine
    replay_controller: ReplayController
    replay_projections: ReplayProjectionGraph
    runtime_service: RuntimeService
    trading_service: TradingService | None = None
    order_command_factory: OrderCommandFactory | None = None
    paper_order_book: PaperOrderBook | None = None
    paper_trading_commands: PaperTradingCommandComposition | None = None

    def close(self, *, timeout_seconds: float = 5.0) -> bool:
        """Close composed resources in lifecycle order."""

        runtime_stopped = self.runtime_service.close(
            timeout_seconds=timeout_seconds
        )
        self.session_recorder.close()
        self.recording_controller.close()
        self.analytics_controller.close()
        self.event_store_controller.close()
        self.state_store.close()
        self.decision_projector.close()
        self.runtime_health_projector.close()
        self.timeline_projector.close()
        self.trade_lifecycle_projector.close()
        self.operator_workspace_projector.close()
        self.replay_controller.close()
        self.replay_projections.close()
        return runtime_stopped


def create_desktop_composition(
    driver_factory: Callable[[], object] | None = None,
    *,
    configuration: DesktopRuntimeConfiguration = DesktopRuntimeConfiguration(),
    placement_runtime: OrderPlacementRuntime | None = None,
    cancellation_runtime: OrderCancellationRuntime | None = None,
    order_command_factory: OrderCommandFactory | None = None,
    paper_order_book: PaperOrderBook | None = None,
) -> DesktopComposition:
    """Construct the desktop application dependency graph."""

    bus = OperationsBus()
    decision_projector = DecisionProjector(bus)
    runtime_health_projector = RuntimeHealthProjector(bus)
    timeline_projector = TimelineProjector(bus)
    trade_lifecycle_projector = TradeLifecycleProjector(bus)
    operator_workspace_projector = OperatorWorkspaceProjector(bus)
    recording_serializer = RecordingSerializer()
    recording_writer = RecordingWriter(
        configuration.recording_directory,
        recording_serializer,
    )
    recording_reader = RecordingReader(recording_serializer)
    session_recorder = SessionRecorder(
        bus,
        recording_serializer,
        application_version="0.1.0",
        broker="BROKER_NEUTRAL",
        runtime_mode=configuration.runtime_mode.value,
    )
    state_store = ApplicationStateStore(bus)
    replay_archive = ReplayEventArchive()
    replay_clock = ReplayClock()
    replay_projections = ReplayProjectionGraph.create()
    replay_engine = ReplayEngine(
        replay_projections.bus,
        replay_clock,
        reset_sink=replay_projections.reset,
    )
    replay_controller = ReplayController(
        replay_archive,
        replay_clock,
        replay_engine,
    )
    recording_controller = RecordingController(
        session_recorder,
        recording_writer,
        recording_reader,
        replay_controller,
    )
    event_store_repository = EventStoreRepository(
        configuration.recording_directory,
        recording_reader,
    )
    event_store_controller = EventStoreController(
        event_store_repository,
        EventStoreQueryEngine(),
        replay_controller,
    )
    analytics_repository = AnalyticsRepository()
    analytics_engine = AnalyticsEngine()
    analytics_controller = AnalyticsController(
        analytics_repository,
        analytics_engine,
        event_store_controller.snapshot,
    )

    runtime_service = create_desktop_runtime_service(
        bus,
        driver_factory=driver_factory,
        runtime_mode=configuration.runtime_mode,
    )

    if (placement_runtime is None) != (cancellation_runtime is None):
        raise ValueError(
            "placement_runtime and cancellation_runtime must be provided together"
        )

    paper_trading_commands = None
    if placement_runtime is None:
        paper_trading_commands = create_paper_trading_command_composition(
            order_book=paper_order_book,
        )
        placement_runtime = paper_trading_commands.placement_runtime
        cancellation_runtime = paper_trading_commands.cancellation_runtime
        order_command_factory = (
            order_command_factory
            or paper_trading_commands.order_command_factory
        )
        paper_order_book = paper_trading_commands.order_book

    trading_service = TradingService(
        placement_runtime,
        cancellation_runtime,
    )

    return DesktopComposition(
        bus=bus,
        state_store=state_store,
        decision_projector=decision_projector,
        runtime_health_projector=runtime_health_projector,
        timeline_projector=timeline_projector,
        trade_lifecycle_projector=trade_lifecycle_projector,
        operator_workspace_projector=operator_workspace_projector,
        session_recorder=session_recorder,
        recording_serializer=recording_serializer,
        recording_writer=recording_writer,
        recording_reader=recording_reader,
        recording_controller=recording_controller,
        event_store_repository=event_store_repository,
        event_store_controller=event_store_controller,
        analytics_repository=analytics_repository,
        analytics_engine=analytics_engine,
        analytics_controller=analytics_controller,
        replay_archive=replay_archive,
        replay_clock=replay_clock,
        replay_engine=replay_engine,
        replay_controller=replay_controller,
        replay_projections=replay_projections,
        runtime_service=runtime_service,
        trading_service=trading_service,
        order_command_factory=order_command_factory,
        paper_order_book=paper_order_book,
        paper_trading_commands=paper_trading_commands,
    )


__all__ = [
    "DesktopComposition",
    "ReplayProjectionGraph",
    "create_desktop_composition",
]
