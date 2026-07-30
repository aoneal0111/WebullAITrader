from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.composition.runtime_projection_pipeline import (
    create_runtime_projection_pipeline,
)
from app.operations.runtime import (
    PaperRuntimeEvent,
    RuntimeDecision,
    RuntimeWatchlistQuote,
    RuntimeWatchlistUpdate,
)
from app.operations_core import (
    ApplicationStateStore,
    OperationsBus,
    OperationsOrder,
)
from app.paper_trading.models import PaperFill
from app.runtime_event_replay import (
    ReplayControl,
    ReplayEngine,
    ReplayStatus,
)


NOW = datetime(2026, 7, 30, 15, 0, tzinfo=UTC)


def runtime_event(
    sequence: int,
    event_type: str,
    *,
    order: OperationsOrder | None = None,
    fill: PaperFill | None = None,
    mark_price: Decimal | None = None,
    decision: RuntimeDecision | None = None,
    watchlist: RuntimeWatchlistUpdate | None = None,
    symbol: str | None = None,
    message: str | None = None,
) -> PaperRuntimeEvent:
    return PaperRuntimeEvent(
        sequence=sequence,
        timestamp=NOW + timedelta(seconds=sequence),
        event_type=event_type,
        message=message or event_type.replace("_", " ").title(),
        cycle=1,
        symbol=symbol,
        order=order,
        fill=fill,
        mark_price=mark_price,
        decision=decision,
        watchlist=watchlist,
    )


def events() -> tuple[PaperRuntimeEvent, ...]:
    order_id = "order-1"
    submitted = OperationsOrder(
        order_id=order_id,
        symbol="AAPL",
        side="BUY",
        quantity="10",
        status="SUBMITTED",
        updated_at=NOW + timedelta(seconds=7),
    )
    accepted = OperationsOrder(
        order_id=order_id,
        symbol="AAPL",
        side="BUY",
        quantity="10",
        status="ACCEPTED",
        updated_at=NOW + timedelta(seconds=8),
    )
    filled_order = OperationsOrder(
        order_id=order_id,
        symbol="AAPL",
        side="BUY",
        quantity="10",
        status="FILLED",
        updated_at=NOW + timedelta(seconds=9),
    )
    fill = PaperFill(
        request_id=order_id,
        symbol="AAPL",
        side="BUY",
        quantity=Decimal("10"),
        fill_price=Decimal("100"),
        notional=Decimal("1000"),
        realized_pnl=Decimal("0"),
        timestamp=NOW + timedelta(seconds=9),
    )
    decision = RuntimeDecision(
        decision_id=order_id,
        timestamp=NOW + timedelta(seconds=7),
        strategy_id="momentum-v1",
        symbol="AAPL",
        action="BUY",
        confidence=85,
        reasoning_summary="Structured momentum decision",
        risk_assessment="APPROVED",
        requested_quantity=Decimal("10"),
        resulting_order_id=order_id,
    )
    return (
        runtime_event(1, "STARTED"),
        runtime_event(2, "BROKER_CONNECTED"),
        runtime_event(3, "MARKET_DATA_CONNECTED"),
        runtime_event(4, "AI_READY"),
        runtime_event(
            5,
            "SYMBOL_SUBSCRIBED",
            symbol="AAPL",
            watchlist=RuntimeWatchlistUpdate(
                symbol="AAPL",
                subscribed=True,
            ),
        ),
        runtime_event(
            6,
            "QUOTE_UPDATED",
            symbol="AAPL",
            watchlist=RuntimeWatchlistUpdate(
                symbol="AAPL",
                quote=RuntimeWatchlistQuote(
                    timestamp=NOW + timedelta(seconds=6),
                    latest_price=Decimal("101"),
                    change=Decimal("1"),
                    change_percent=Decimal("1"),
                    bid=Decimal("100.90"),
                    ask=Decimal("101.10"),
                    volume=1000,
                ),
                market_status="OPEN",
            ),
        ),
        runtime_event(
            7,
            "DECISION_PROCESSED",
            symbol="AAPL",
            order=submitted,
            decision=decision,
        ),
        runtime_event(
            8,
            "ORDER_ACCEPTED",
            symbol="AAPL",
            order=accepted,
        ),
        runtime_event(
            9,
            "ORDER_FILLED",
            symbol="AAPL",
            order=filled_order,
            fill=fill,
            mark_price=Decimal("105"),
        ),
        runtime_event(10, "HEARTBEAT"),
        runtime_event(
            11,
            "SYSTEM_WARNING",
            message="Runtime load is elevated.",
        ),
    )


def live_state(recorded_events):
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    pipeline = create_runtime_projection_pipeline(
        operations_bus=bus,
        account_id="replay",
    )
    for event in recorded_events:
        pipeline.sink(event)
    return store.snapshot(), pipeline


def test_empty_event_stream_rebuilds_initial_application_state() -> None:
    engine = ReplayEngine(())

    result = engine.replay_from_beginning()

    assert result.status is ReplayStatus.EMPTY
    assert result.processed_events == 0
    assert result.completed is True
    assert result.state == engine.state


def test_events_are_replayed_in_timestamp_then_sequence_order() -> None:
    recorded = events()
    shuffled = (recorded[8], recorded[2], recorded[0], *recorded[1:2], *recorded[3:8], *recorded[9:])
    engine = ReplayEngine(shuffled)

    result = engine.replay_from_beginning()

    assert tuple(event.sequence for event in engine.ordered_events) == tuple(
        range(1, 12)
    )
    assert result.status is ReplayStatus.COMPLETED


def test_replay_reconstructs_same_complete_state_as_live_pipeline() -> None:
    recorded = events()
    expected, live_pipeline = live_state(recorded)
    engine = ReplayEngine(recorded)

    result = engine.replay_from_beginning()

    assert result.state == expected
    assert result.state.order_projection == (
        live_pipeline.order_projection.snapshot
    )
    assert result.state.position_projection == (
        live_pipeline.position_projection.snapshot
    )
    assert result.state.timeline_projection == (
        live_pipeline.timeline_projection.snapshot
    )
    assert result.state.decision_projection == (
        live_pipeline.decision_projection.snapshot
    )
    assert result.state.portfolio_projection == (
        live_pipeline.portfolio_projection.snapshot
    )
    assert result.state.health_projection == (
        live_pipeline.health_projection.snapshot
    )
    assert result.state.watchlist_projection == (
        live_pipeline.watchlist_projection.snapshot
    )


def test_replay_from_arbitrary_index_matches_live_suffix() -> None:
    recorded = events()
    expected, _ = live_state(recorded[5:])
    engine = ReplayEngine(recorded)

    result = engine.replay(start_index=5)

    assert result.start_index == 5
    assert result.processed_events == len(recorded) - 5
    assert result.state == expected


def test_replay_to_timestamp_is_inclusive_and_resumable() -> None:
    recorded = events()
    engine = ReplayEngine(recorded)

    partial = engine.replay_from_beginning(
        to_timestamp=NOW + timedelta(seconds=6)
    )

    assert partial.status is ReplayStatus.PARTIAL
    assert partial.next_index == 6
    assert partial.state.watchlist_projection.entries[0].latest_price == "101"

    completed = engine.resume()
    assert completed.status is ReplayStatus.COMPLETED
    assert completed.start_index == 6
    assert completed.state == ReplayEngine(recorded).replay().state


class InterruptingSpeed:
    def __init__(self, control: ReplayControl, before_event: int) -> None:
        self._control = control
        self._before_event = before_event
        self.calls = 0

    def pace(self, previous_event, next_event) -> None:
        del previous_event, next_event
        self.calls += 1
        if self.calls == self._before_event:
            self._control.interrupt()


def test_replay_interruption_and_resume_preserve_pipeline_state() -> None:
    recorded = events()
    control = ReplayControl()
    speed = InterruptingSpeed(control, before_event=4)
    engine = ReplayEngine(recorded)

    interrupted = engine.replay(speed=speed, control=control)

    assert interrupted.status is ReplayStatus.INTERRUPTED
    assert interrupted.processed_events == 3
    assert interrupted.next_index == 3

    resumed = engine.resume()
    expected = ReplayEngine(recorded).replay().state
    assert resumed.status is ReplayStatus.COMPLETED
    assert resumed.processed_events == len(recorded) - 3
    assert resumed.state == expected


def test_duplicate_recorded_events_are_projection_idempotent() -> None:
    recorded = events()
    with_duplicate = (*recorded[:9], recorded[8], *recorded[9:])

    duplicate_state = ReplayEngine(with_duplicate).replay().state
    baseline_state = ReplayEngine(recorded).replay().state

    assert duplicate_state == baseline_state


def test_large_event_stream_replays_without_unbounded_timeline() -> None:
    recorded = tuple(
        runtime_event(index, "HEARTBEAT")
        for index in range(1, 2001)
    )
    engine = ReplayEngine(recorded)

    result = engine.replay()

    assert result.processed_events == 2000
    assert result.state.health_projection.last_heartbeat == (
        NOW + timedelta(seconds=2000)
    )
    assert len(result.state.timeline_projection.entries) == 500


def test_explicit_determinism_verification_rebuilds_identical_state() -> None:
    engine = ReplayEngine(events())

    assert engine.verify_determinism() is True
