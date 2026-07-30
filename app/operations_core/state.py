from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from threading import RLock
from typing import TYPE_CHECKING

from app.operations_core.bus import OperationsBus, Subscription
from app.operations_core.events import (
    OperationsEvent,
    DecisionsUpdated,
    PortfolioUpdated,
    HealthUpdated,
    WatchlistUpdated,
    OperationsOrder,
    OperationsPosition,
    OrdersUpdated,
    PositionsUpdated,
    PaperRuntimeSnapshot,
    PaperRuntimeUpdated,

    RuntimeCycleCompleted,
    RuntimeFailed,
    RuntimeStarted,
    RuntimeStarting,
    RuntimeStopped,
    RuntimeStopping,
    TimelineUpdated,
)

if TYPE_CHECKING:
    from app.replay_workspace.models import ReplayWorkspaceState
    from app.read_models.decisions.models import DecisionsReadModelSnapshot
    from app.read_models.orders.models import OrdersReadModelSnapshot
    from app.read_models.positions.models import PositionsReadModelSnapshot
    from app.read_models.timeline.models import TimelineReadModelSnapshot
    from app.read_models.portfolio.models import PortfolioSummary
    from app.read_models.health.models import HealthState
    from app.read_models.watchlist.models import WatchlistState


def _initial_order_projection() -> "OrdersReadModelSnapshot":
    from app.read_models.orders.models import OrdersReadModelSnapshot

    return OrdersReadModelSnapshot.initial()


def _initial_position_projection() -> "PositionsReadModelSnapshot":
    from app.read_models.positions.models import PositionsReadModelSnapshot

    return PositionsReadModelSnapshot.initial()


def _initial_timeline_projection() -> "TimelineReadModelSnapshot":
    from app.read_models.timeline.models import TimelineReadModelSnapshot

    return TimelineReadModelSnapshot.initial()


def _initial_decision_projection() -> "DecisionsReadModelSnapshot":
    from app.read_models.decisions.models import DecisionsReadModelSnapshot

    return DecisionsReadModelSnapshot.initial()


def _initial_portfolio_projection() -> "PortfolioSummary":
    from app.read_models.portfolio.models import PortfolioSummary

    return PortfolioSummary.initial()


def _initial_health_projection() -> "HealthState":
    from app.read_models.health.models import HealthState

    return HealthState.initial()


def _initial_watchlist_projection() -> "WatchlistState":
    from app.read_models.watchlist.models import WatchlistState

    return WatchlistState.initial()


def _initial_replay_state() -> "ReplayWorkspaceState":
    from app.replay_workspace.models import ReplayWorkspaceState

    return ReplayWorkspaceState()


class RuntimePhase(StrEnum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class RuntimeState:
    phase: RuntimePhase = RuntimePhase.STOPPED
    environment: str = "PAPER"
    broker_status: str = "Disconnected"
    market_feed_status: str = "Idle"
    inference_status: str = "Ready"
    active_model: str = "Not loaded"
    cycles_completed: int = 0
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    event_id: str
    occurred_at: datetime
    event_type: str
    source: str
    message: str


@dataclass(frozen=True, slots=True)
class ApplicationState:
    runtime: RuntimeState = field(default_factory=RuntimeState)
    paper_runtime: PaperRuntimeSnapshot | None = None
    orders: tuple[OperationsOrder, ...] = ()
    positions: tuple[OperationsPosition, ...] = ()

    timeline: tuple[TimelineEntry, ...] = ()
    revision: int = 0
    order_projection: "OrdersReadModelSnapshot" = field(
        default_factory=_initial_order_projection
    )
    position_projection: "PositionsReadModelSnapshot" = field(
        default_factory=_initial_position_projection
    )
    timeline_projection: "TimelineReadModelSnapshot" = field(
        default_factory=_initial_timeline_projection
    )
    decision_projection: "DecisionsReadModelSnapshot" = field(
        default_factory=_initial_decision_projection
    )
    portfolio_projection: "PortfolioSummary" = field(
        default_factory=_initial_portfolio_projection
    )
    health_projection: "HealthState" = field(
        default_factory=_initial_health_projection
    )
    watchlist_projection: "WatchlistState" = field(
        default_factory=_initial_watchlist_projection
    )
    replay: "ReplayWorkspaceState" = field(
        default_factory=_initial_replay_state
    )


StateListener = Callable[[ApplicationState], None]


class ApplicationStateStore:
    """
    Thread-safe single source of truth for Operations Center presentation state.

    It consumes immutable business events and publishes immutable state snapshots.
    """

    def __init__(
        self,
        bus: OperationsBus,
        *,
        timeline_limit: int = 500,
    ) -> None:
        if timeline_limit <= 0:
            raise ValueError("timeline_limit must be positive")

        self._bus = bus
        self._timeline_limit = timeline_limit
        self._lock = RLock()
        self._state = ApplicationState()
        self._listeners: dict[int, StateListener] = {}
        self._next_listener_id = 1
        self._subscription: Subscription = bus.subscribe(
            OperationsEvent,
            self._handle_event,
        )

    def snapshot(self) -> ApplicationState:
        with self._lock:
            return self._state

    def subscribe(self, listener: StateListener) -> int:
        if not callable(listener):
            raise TypeError("listener must be callable")

        with self._lock:
            listener_id = self._next_listener_id
            self._next_listener_id += 1
            self._listeners[listener_id] = listener
            state = self._state

        listener(state)
        return listener_id

    def unsubscribe(self, listener_id: int) -> bool:
        with self._lock:
            return self._listeners.pop(listener_id, None) is not None

    def close(self) -> None:
        self._bus.unsubscribe(self._subscription)

        with self._lock:
            self._listeners.clear()

    def _handle_event(self, event: OperationsEvent) -> None:
        with self._lock:
            runtime = self._reduce_runtime(self._state.runtime, event)
            paper_runtime = self._reduce_paper_runtime(
                self._state.paper_runtime,
                event,
            )
            orders = self._reduce_orders(self._state.orders, event)
            order_projection = self._reduce_order_projection(
                self._state.order_projection,
                event,
            )
            positions = self._reduce_positions(
                self._state.positions,
                event,
            )
            position_projection = self._reduce_position_projection(
                self._state.position_projection,
                event,
            )
            timeline_projection = self._reduce_timeline_projection(
                self._state.timeline_projection,
                event,
            )
            decision_projection = self._reduce_decision_projection(
                self._state.decision_projection,
                event,
            )
            portfolio_projection = self._reduce_portfolio_projection(
                self._state.portfolio_projection,
                event,
            )
            health_projection = self._reduce_health_projection(
                self._state.health_projection,
                event,
            )
            watchlist_projection = self._reduce_watchlist_projection(
                self._state.watchlist_projection,
                event,
            )

            timeline = self._state.timeline

            if not isinstance(
                event,
                (
                    RuntimeCycleCompleted,
                    PaperRuntimeUpdated,
                    TimelineUpdated,
                    DecisionsUpdated,
                    PortfolioUpdated,
                    HealthUpdated,
                    WatchlistUpdated,
                ),
            ):
                timeline = timeline + (self._timeline_entry(event),)
                timeline = timeline[-self._timeline_limit :]

            self._state = ApplicationState(
                runtime=runtime,
                paper_runtime=paper_runtime,
                orders=orders,
                order_projection=order_projection,
                positions=positions,
                position_projection=position_projection,
                timeline_projection=timeline_projection,
                decision_projection=decision_projection,
                portfolio_projection=portfolio_projection,
                health_projection=health_projection,
                watchlist_projection=watchlist_projection,
                replay=self._state.replay,
                timeline=timeline,
                revision=self._state.revision + 1,
            )

            state = self._state
            listeners = tuple(self._listeners.values())

        for listener in listeners:
            listener(state)

    @staticmethod
    def _reduce_paper_runtime(
        current: PaperRuntimeSnapshot | None,
        event: OperationsEvent,
    ) -> PaperRuntimeSnapshot | None:
        if isinstance(event, PaperRuntimeUpdated):
            return event.snapshot

        return current

    @staticmethod
    def _reduce_runtime(
        current: RuntimeState,
        event: OperationsEvent,
    ) -> RuntimeState:
        if isinstance(event, RuntimeStarting):
            return replace(
                current,
                phase=RuntimePhase.STARTING,
                environment=event.environment,
                broker_status="Connecting",
                market_feed_status="Starting",
                inference_status="Loading",
                cycles_completed=0,
                last_error=None,
            )

        if isinstance(event, RuntimeStarted):
            return replace(
                current,
                phase=RuntimePhase.RUNNING,
                environment=event.environment,
                broker_status="Connected",
                market_feed_status="Healthy",
                inference_status="Healthy",
                active_model=event.active_model,
                last_error=None,
            )

        if isinstance(event, RuntimeCycleCompleted):
            return replace(
                current,
                cycles_completed=event.cycle_count,
            )

        if isinstance(event, RuntimeStopping):
            return replace(
                current,
                phase=RuntimePhase.STOPPING,
            )

        if isinstance(event, RuntimeStopped):
            return replace(
                current,
                phase=RuntimePhase.STOPPED,
                broker_status="Disconnected",
                market_feed_status="Idle",
                inference_status="Ready",
                cycles_completed=event.cycles_completed,
                last_error=None,
            )

        if isinstance(event, RuntimeFailed):
            return replace(
                current,
                phase=RuntimePhase.FAILED,
                broker_status="Disconnected",
                market_feed_status="Error",
                inference_status="Error",
                last_error=event.error_message,
            )

        return current

    @staticmethod
    def _reduce_orders(
        current: tuple[OperationsOrder, ...],
        event: OperationsEvent,
    ) -> tuple[OperationsOrder, ...]:
        if isinstance(event, OrdersUpdated):
            return event.orders

        return current

    @staticmethod
    def _reduce_order_projection(
        current: "OrdersReadModelSnapshot",
        event: OperationsEvent,
    ) -> "OrdersReadModelSnapshot":
        if isinstance(event, OrdersUpdated):
            from app.read_models.orders.projector import (
                project_operational_orders,
            )

            return project_operational_orders(event.orders)

        return current

    @staticmethod
    def _reduce_positions(
        current: tuple[OperationsPosition, ...],
        event: OperationsEvent,
    ) -> tuple[OperationsPosition, ...]:
        if isinstance(event, PositionsUpdated):
            return event.positions

        return current

    @staticmethod
    def _reduce_position_projection(
        current: "PositionsReadModelSnapshot",
        event: OperationsEvent,
    ) -> "PositionsReadModelSnapshot":
        if isinstance(event, PositionsUpdated):
            from app.read_models.positions.projector import (
                project_operational_positions,
            )

            return project_operational_positions(event.positions)

        return current

    @staticmethod
    def _reduce_timeline_projection(
        current: "TimelineReadModelSnapshot",
        event: OperationsEvent,
    ) -> "TimelineReadModelSnapshot":
        if isinstance(event, TimelineUpdated):
            from app.read_models.timeline.projector import (
                project_operational_timeline,
            )

            return project_operational_timeline(event.entries)

        return current

    @staticmethod
    def _reduce_decision_projection(
        current: "DecisionsReadModelSnapshot",
        event: OperationsEvent,
    ) -> "DecisionsReadModelSnapshot":
        if isinstance(event, DecisionsUpdated):
            from app.read_models.decisions.projector import (
                project_operational_decisions,
            )

            return project_operational_decisions(event.decisions)

        return current

    @staticmethod
    def _reduce_portfolio_projection(
        current: "PortfolioSummary",
        event: OperationsEvent,
    ) -> "PortfolioSummary":
        if isinstance(event, PortfolioUpdated):
            from app.read_models.portfolio.projector import (
                project_operational_portfolio,
            )

            return project_operational_portfolio(event.summary)

        return current

    @staticmethod
    def _reduce_health_projection(
        current: "HealthState",
        event: OperationsEvent,
    ) -> "HealthState":
        if isinstance(event, HealthUpdated):
            from app.read_models.health.projector import (
                project_operational_health,
            )

            return project_operational_health(event.state)

        return current

    @staticmethod
    def _reduce_watchlist_projection(
        current: "WatchlistState",
        event: OperationsEvent,
    ) -> "WatchlistState":
        if isinstance(event, WatchlistUpdated):
            from app.read_models.watchlist.projector import (
                project_operational_watchlist,
            )

            return project_operational_watchlist(event.state)

        return current

    @staticmethod
    def _timeline_entry(event: OperationsEvent) -> TimelineEntry:
        if isinstance(event, RuntimeStarting):
            message = f"Starting {event.environment} runtime."
        elif isinstance(event, RuntimeStarted):
            message = (
                f"{event.environment} runtime started using "
                f"{event.active_model}."
            )
        elif isinstance(event, RuntimeStopping):
            message = event.reason
        elif isinstance(event, RuntimeStopped):
            message = (
                f"{event.reason} Cycles completed: "
                f"{event.cycles_completed}."
            )
        elif isinstance(event, RuntimeFailed):
            message = f"Runtime failed: {event.error_message}"
        elif isinstance(event, OrdersUpdated):
            order_count = len(event.orders)
            noun = "order" if order_count == 1 else "orders"
            message = f"Order state updated: {order_count} {noun}."
        elif isinstance(event, PositionsUpdated):
            position_count = len(event.positions)
            noun = "position" if position_count == 1 else "positions"
            message = (
                f"Position state updated: {position_count} {noun}."
            )
        else:
            message = type(event).__name__

        return TimelineEntry(
            event_id=str(event.event_id),
            occurred_at=event.occurred_at,
            event_type=type(event).__name__,
            source=event.source,
            message=message,
        )
