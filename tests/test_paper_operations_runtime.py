from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.execution_coordinator import (
    CoordinationRequest,
    CoordinationStage,
    CoordinationStatus,
    CoordinationTrace,
    ExecutionCoordinationResult,
)
from app.operations import (
    AtomicPaperRuntimeCheckpoint,
    PaperOperationsEngine,
    PaperRuntimeCycleResult,
    PaperRuntimeStatus,
)
from app.order_compliance.models import (
    OrderSide,
    OrderType,
    ProposedOrder,
    TradingSession,
)
from app.paper_session import PaperSessionStatus
from app.paper_trading.metrics import calculate_metrics
from app.paper_trading.models import (
    EquityPoint,
    ExecutionStatus,
    PaperExecutionResult,
    PaperFill,
    SimulationResult,
)
from app.paper_trading.portfolio import apply_fill
from app.strategy_engine import (
    StrategyDecision,
    StrategyDecisionAction,
    StrategyOrderIntent,
    StrategyPosition,
)

NOW = datetime(2026, 7, 20, 14, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Snapshot:
    symbol: str


class Clock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> datetime:
        value = self.value
        self.value += timedelta(minutes=1)
        return value


class StubStrategy:
    def __init__(self, action=StrategyDecisionAction.HOLD) -> None:
        self.action = action
        self.calls = []

    def evaluate(self, snapshot, position, *, timestamp):
        self.calls.append((snapshot.symbol, position.quantity, timestamp))
        return StrategyDecision(
            symbol=snapshot.symbol,
            action=self.action,
            confidence=80,
            score=Decimal("0.8"),
            timestamp=timestamp,
            reasons=("test",),
            source_action=(
                "BUY"
                if self.action is StrategyDecisionAction.ENTER_LONG
                else "HOLD"
            ),
            position_quantity=position.quantity,
        )


class StubCoordinator:
    def __init__(self) -> None:
        self.calls = []

    def coordinate(self, decision, request=None):
        self.calls.append((decision, request))
        return ExecutionCoordinationResult(
            status=(
                CoordinationStatus.REJECTED
                if decision.creates_order_intent
                else CoordinationStatus.SKIPPED
            ),
            final_stage=(
                CoordinationStage.RISK
                if decision.creates_order_intent
                else CoordinationStage.STRATEGY
            ),
            strategy_decision=decision,
            order_intent=(request.order_intent if request else None),
            proposal=(object() if request else None),
            risk_decision=(object() if request else None),
            compliance_decision=None,
            execution_result=None,
            trace=(
                CoordinationTrace(
                    CoordinationStage.STRATEGY,
                    decision.creates_order_intent,
                    "test trace",
                ),
            ),
        )


def request_builder(decision, snapshot, session, cycle, index):
    intent = StrategyOrderIntent(
        timestamp=decision.timestamp,
        request_id=f"{session.session_id}-{cycle}-{index}",
        symbol=decision.symbol,
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        order_type=OrderType.MARKET,
        requested_session=TradingSession.REGULAR,
    )
    return CoordinationRequest(
        order_intent=intent,
        advisory_response=object(),
        snapshot=snapshot,
        risk_limits=object(),
        account_state=object(),
        market_state=object(),
        gfv_decision=object(),
        compliance_limits=object(),
        kill_switch=object(),
        portfolio=session.portfolio,
        market_quote=object(),
        execution_config=object(),
        journal=session.journal,
        equity_curve=session.equity_curve,
    )


def make_engine(*, snapshots=(Snapshot("AAPL"),), action=StrategyDecisionAction.HOLD, checkpoint=None, events=None, cycle_sink=None):
    return PaperOperationsEngine(
        session_id="paper-1",
        initial_cash=Decimal("10000"),
        snapshot_source=lambda timestamp: snapshots,
        strategy_engine=StubStrategy(action),
        coordinator=StubCoordinator(),
        request_builder=request_builder,
        clock=Clock(),
        checkpoint_sink=checkpoint,
        event_sink=(events.append if events is not None else None),
        cycle_sink=cycle_sink,
    )


def test_start_creates_active_session() -> None:
    engine = make_engine()
    state = engine.start()
    assert state.status is PaperRuntimeStatus.RUNNING
    assert engine.session.status is PaperSessionStatus.ACTIVE
    assert engine.session.portfolio.cash == Decimal("10000")


def test_start_can_only_happen_once() -> None:
    engine = make_engine()
    engine.start()
    with pytest.raises(RuntimeError, match="only be started once"):
        engine.start()


def test_cycle_processes_snapshots_in_symbol_order() -> None:
    engine = make_engine(snapshots=(Snapshot("MSFT"), Snapshot("AAPL")))
    engine.start()
    engine.run_cycle()
    assert [call[0] for call in engine._strategy_engine.calls] == ["AAPL", "MSFT"]
    assert engine.state.cycles_completed == 1
    assert engine.state.decisions_processed == 2


def test_non_executable_decision_does_not_build_request() -> None:
    def fail_builder(*args):
        raise AssertionError("request builder should not be called")

    engine = PaperOperationsEngine(
        session_id="paper-1",
        initial_cash=Decimal("10000"),
        snapshot_source=lambda timestamp: (Snapshot("AAPL"),),
        strategy_engine=StubStrategy(),
        coordinator=StubCoordinator(),
        request_builder=fail_builder,
        clock=Clock(),
    )
    engine.start()
    engine.run_cycle()
    assert engine.session.statistics.decisions_skipped == 1


def test_executable_decision_uses_explicit_request_builder() -> None:
    engine = make_engine(action=StrategyDecisionAction.ENTER_LONG)
    engine.start()
    engine.run_cycle()
    assert engine.session.processed_request_ids == ("paper-1-1-1",)
    assert engine.session.statistics.orders_rejected == 1


def test_pause_and_resume_control_cycles() -> None:
    engine = make_engine()
    engine.start()
    engine.pause()
    assert engine.state.status is PaperRuntimeStatus.PAUSED
    with pytest.raises(RuntimeError, match="must be RUNNING"):
        engine.run_cycle()
    engine.resume()
    engine.run_cycle()
    assert engine.state.cycles_completed == 1


def test_stop_closes_session() -> None:
    engine = make_engine()
    engine.start()
    session = engine.stop()
    assert engine.state.status is PaperRuntimeStatus.STOPPED
    assert session.status is PaperSessionStatus.CLOSED
    assert session.ended_at is not None


def test_cycle_failure_fails_closed() -> None:
    engine = PaperOperationsEngine(
        session_id="paper-1",
        initial_cash=Decimal("10000"),
        snapshot_source=lambda timestamp: (_ for _ in ()).throw(ValueError("bad feed")),
        strategy_engine=StubStrategy(),
        coordinator=StubCoordinator(),
        request_builder=request_builder,
        clock=Clock(),
    )
    engine.start()
    with pytest.raises(ValueError, match="bad feed"):
        engine.run_cycle()
    assert engine.state.status is PaperRuntimeStatus.FAILED
    assert engine.state.failure == "ValueError: bad feed"


def test_event_sink_receives_ordered_events() -> None:
    events = []
    engine = make_engine(events=events)
    engine.start()
    engine.run_cycle()
    engine.stop()
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert [event.event_type for event in events] == [
        "STARTED",
        "DECISION_PROCESSED",
        "CYCLE_COMPLETED",
        "STOPPED",
    ]


def test_executable_decision_event_exposes_explicit_order_fact() -> None:
    events = []
    engine = make_engine(
        action=StrategyDecisionAction.ENTER_LONG,
        events=events,
    )

    engine.start()
    engine.run_cycle()

    decision_event = next(
        event
        for event in events
        if event.event_type == "DECISION_PROCESSED"
    )
    assert decision_event.order is not None
    assert decision_event.order.order_id == "paper-1-1-1"
    assert decision_event.order.symbol == "AAPL"
    assert decision_event.order.side == "BUY"
    assert decision_event.order.quantity == "1"
    assert decision_event.order.status == "REJECTED"
    assert decision_event.decision is not None
    assert decision_event.decision.decision_id == "paper-1-1-1"
    assert decision_event.decision.strategy_id == "1.0"
    assert decision_event.decision.symbol == "AAPL"
    assert decision_event.decision.action == "BUY"
    assert decision_event.decision.requested_quantity == Decimal("1")
    assert decision_event.decision.resulting_order_id == "paper-1-1-1"


def test_filled_decision_event_exposes_fill_and_available_mark() -> None:
    class FillingCoordinator:
        def coordinate(self, decision, request):
            intent = request.order_intent
            proposal = ProposedOrder(
                request_id=intent.request_id,
                symbol=intent.symbol,
                side=intent.side,
                order_type=intent.order_type,
                quantity=intent.quantity,
                limit_price=intent.limit_price,
                stop_price=intent.stop_price,
                requested_session=intent.requested_session,
                created_timestamp=intent.timestamp,
            )
            portfolio, realized_pnl = apply_fill(
                request.portfolio,
                intent.symbol,
                intent.side,
                intent.quantity,
                Decimal("100"),
                Decimal("105"),
                intent.timestamp,
            )
            fill = PaperFill(
                request_id=intent.request_id,
                symbol=intent.symbol,
                side=intent.side.value,
                quantity=intent.quantity,
                fill_price=Decimal("100"),
                notional=intent.quantity * Decimal("100"),
                realized_pnl=realized_pnl,
                timestamp=intent.timestamp,
            )
            execution = PaperExecutionResult(
                status=ExecutionStatus.FILLED,
                reason="filled",
                original_proposal=proposal,
                fill=fill,
                portfolio_before=request.portfolio,
                portfolio_after=portfolio,
            )
            equity_curve = (
                *request.equity_curve,
                EquityPoint(intent.timestamp, portfolio.equity),
            )
            simulation = SimulationResult(
                execution=execution,
                portfolio=portfolio,
                journal=request.journal,
                equity_curve=equity_curve,
                metrics=calculate_metrics(request.journal, equity_curve),
            )
            return ExecutionCoordinationResult(
                status=CoordinationStatus.EXECUTED,
                final_stage=CoordinationStage.COMPLETE,
                strategy_decision=decision,
                order_intent=intent,
                proposal=proposal,
                risk_decision=object(),
                compliance_decision=object(),
                execution_result=simulation,
                trace=(
                    CoordinationTrace(
                        CoordinationStage.COMPLETE,
                        True,
                        "filled",
                    ),
                ),
            )

    events = []
    engine = PaperOperationsEngine(
        session_id="paper-1",
        initial_cash=Decimal("10000"),
        snapshot_source=lambda timestamp: (Snapshot("AAPL"),),
        strategy_engine=StubStrategy(
            StrategyDecisionAction.ENTER_LONG
        ),
        coordinator=FillingCoordinator(),
        request_builder=request_builder,
        clock=Clock(),
        event_sink=events.append,
    )

    engine.start()
    engine.run_cycle()

    decision_event = next(
        event
        for event in events
        if event.event_type == "DECISION_PROCESSED"
    )
    assert decision_event.fill is not None
    assert decision_event.fill.request_id == "paper-1-1-1"
    assert decision_event.fill.fill_price == Decimal("100")
    assert decision_event.mark_price == Decimal("105")


def test_checkpoint_sink_runs_after_transitions() -> None:
    checkpoints = []
    engine = make_engine(checkpoint=lambda state, session: checkpoints.append((state.status, session.status)))
    engine.start()
    engine.pause()
    engine.resume()
    engine.stop()
    assert checkpoints == [
        (PaperRuntimeStatus.RUNNING, PaperSessionStatus.ACTIVE),
        (PaperRuntimeStatus.PAUSED, PaperSessionStatus.ACTIVE),
        (PaperRuntimeStatus.RUNNING, PaperSessionStatus.ACTIVE),
        (PaperRuntimeStatus.STOPPED, PaperSessionStatus.CLOSED),
    ]


def test_atomic_checkpoint_is_canonical(tmp_path) -> None:
    path = tmp_path / "runtime.json"
    engine = make_engine(checkpoint=AtomicPaperRuntimeCheckpoint(path))
    engine.start()
    first = path.read_text(encoding="utf-8")
    payload = json.loads(first)
    assert payload["schema_version"] == "1"
    assert payload["runtime"]["status"] == "RUNNING"
    assert payload["session"]["session_id"] == "paper-1"
    assert ": " not in first
    assert ", " not in first


def test_run_respects_max_cycles() -> None:
    engine = make_engine()
    engine.start()
    engine.run(interval_seconds=0, max_cycles=3, wait=lambda seconds: False)
    assert engine.state.cycles_completed == 3
    assert engine.state.status is PaperRuntimeStatus.RUNNING


def test_naive_clock_is_rejected() -> None:
    engine = PaperOperationsEngine(
        session_id="paper-1",
        initial_cash=Decimal("10000"),
        snapshot_source=lambda timestamp: (),
        strategy_engine=StubStrategy(),
        coordinator=StubCoordinator(),
        request_builder=request_builder,
        clock=lambda: datetime(2026, 7, 20, 14, 0),
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        engine.start()


def test_cycle_sink_receives_committed_cycle_result() -> None:
    results = []
    engine = PaperOperationsEngine(
        session_id="paper-1",
        initial_cash=Decimal("10000"),
        snapshot_source=lambda timestamp: (Snapshot("MSFT"), Snapshot("AAPL")),
        strategy_engine=StubStrategy(),
        coordinator=StubCoordinator(),
        request_builder=request_builder,
        clock=Clock(),
        cycle_sink=results.append,
    )
    engine.start()
    engine.run_cycle()
    assert len(results) == 1
    result = results[0]
    assert isinstance(result, PaperRuntimeCycleResult)
    assert result.cycle == 1
    assert result.symbols == ("AAPL", "MSFT")
    assert tuple(decision.symbol for decision in result.decisions) == result.symbols
    assert result.session.statistics.decisions_processed == 2


def test_cycle_sink_failure_fails_closed_without_committing_session() -> None:
    def fail_cycle_sink(result):
        raise RuntimeError("analytics unavailable")

    engine = PaperOperationsEngine(
        session_id="paper-1",
        initial_cash=Decimal("10000"),
        snapshot_source=lambda timestamp: (Snapshot("AAPL"),),
        strategy_engine=StubStrategy(),
        coordinator=StubCoordinator(),
        request_builder=request_builder,
        clock=Clock(),
        cycle_sink=fail_cycle_sink,
    )
    engine.start()
    original_session = engine.session
    with pytest.raises(RuntimeError, match="analytics unavailable"):
        engine.run_cycle()
    assert engine.state.status is PaperRuntimeStatus.FAILED
    assert engine.state.cycles_completed == 0
    assert engine.session is original_session
    assert engine.session.statistics.decisions_processed == 0


def test_recover_continues_with_next_cycle_number() -> None:
    engine = make_engine()
    engine.start()
    engine.run_cycle()

    recovered = PaperOperationsEngine.recover(
        state=engine.state,
        session=engine.session,
        snapshot_source=lambda timestamp: (Snapshot("MSFT"),),
        strategy_engine=StubStrategy(),
        coordinator=StubCoordinator(),
        request_builder=request_builder,
        clock=lambda: NOW + timedelta(minutes=2),
    )
    recovered.run_cycle()
    assert recovered.state.cycles_completed == 2
    assert recovered.state.decisions_processed == 2


def test_recover_rejects_closed_session() -> None:
    engine = make_engine()
    engine.start()
    state = engine.state
    session = engine.stop()
    with pytest.raises(ValueError, match="must be active"):
        PaperOperationsEngine.recover(
            state=state,
            session=session,
            snapshot_source=lambda timestamp: (),
            strategy_engine=StubStrategy(),
            coordinator=StubCoordinator(),
            request_builder=request_builder,
            clock=Clock(),
        )


def test_atomic_runtime_journal_records_cycle_and_analytics(tmp_path) -> None:
    from app.operations import AtomicPaperRuntimeJournal

    path = tmp_path / "journal.json"
    engine = make_engine(cycle_sink=AtomicPaperRuntimeJournal(path))
    engine.start()
    engine.run_cycle()

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1"
    assert payload["session_id"] == "paper-1"
    assert payload["cycles"][0]["cycle"] == 1
    assert payload["cycles"][0]["symbols"] == ["AAPL"]
    assert payload["cycles"][0]["decisions"][0]["action"] == "HOLD"
    assert payload["latest_analytics"]["as_of_cycle"] == 1
    assert payload["latest_analytics"]["decisions_processed"] == 1


def test_atomic_runtime_journal_appends_in_cycle_order(tmp_path) -> None:
    from app.operations import AtomicPaperRuntimeJournal

    path = tmp_path / "journal.json"
    engine = make_engine(cycle_sink=AtomicPaperRuntimeJournal(path))
    engine.start()
    engine.run_cycle()
    engine.run_cycle()

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert [item["cycle"] for item in payload["cycles"]] == [1, 2]
    assert payload["latest_analytics"]["as_of_cycle"] == 2


def test_atomic_runtime_journal_is_canonical(tmp_path) -> None:
    from app.operations import AtomicPaperRuntimeJournal

    path = tmp_path / "journal.json"
    engine = make_engine(cycle_sink=AtomicPaperRuntimeJournal(path))
    engine.start()
    engine.run_cycle()

    content = path.read_text(encoding="utf-8")
    assert ": " not in content
    assert ", " not in content
    assert content == json.dumps(json.loads(content), sort_keys=True, separators=(",", ":"))


def test_runtime_journal_failure_prevents_cycle_commit(tmp_path) -> None:
    from app.operations import AtomicPaperRuntimeJournal

    path = tmp_path / "journal.json"
    path.write_text('{"schema_version":"999"}', encoding="utf-8")
    engine = make_engine(cycle_sink=AtomicPaperRuntimeJournal(path))
    engine.start()

    with pytest.raises(ValueError, match="schema version"):
        engine.run_cycle()

    assert engine.state.status is PaperRuntimeStatus.FAILED
    assert engine.state.cycles_completed == 0
    assert engine.session.statistics.decisions_processed == 0


def test_runtime_journal_rejects_cycle_gap(tmp_path) -> None:
    from app.operations import AtomicPaperRuntimeJournal, PaperRuntimeCycleResult

    path = tmp_path / "journal.json"
    engine = make_engine()
    engine.start()
    session = engine.run_cycle()
    result = PaperRuntimeCycleResult(
        cycle=2,
        timestamp=NOW + timedelta(minutes=5),
        symbols=("AAPL",),
        decisions=(StubStrategy().evaluate(Snapshot("AAPL"), StrategyPosition("AAPL"), timestamp=NOW),),
        session=session,
    )

    with pytest.raises(ValueError, match="expected cycle 1"):
        AtomicPaperRuntimeJournal(path)(result)


def test_runtime_journal_rejects_different_session(tmp_path) -> None:
    from app.operations import AtomicPaperRuntimeJournal

    path = tmp_path / "journal.json"
    first = make_engine(cycle_sink=AtomicPaperRuntimeJournal(path))
    first.start()
    first.run_cycle()

    second = PaperOperationsEngine(
        session_id="paper-2",
        initial_cash=Decimal("10000"),
        snapshot_source=lambda timestamp: (Snapshot("AAPL"),),
        strategy_engine=StubStrategy(),
        coordinator=StubCoordinator(),
        request_builder=request_builder,
        clock=Clock(),
        cycle_sink=AtomicPaperRuntimeJournal(path),
    )
    second.start()
    with pytest.raises(ValueError, match="session ID"):
        second.run_cycle()
