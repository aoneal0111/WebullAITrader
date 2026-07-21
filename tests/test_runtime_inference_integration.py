from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.operations.learning_runtime import RuntimeInferenceAudit
from app.operations.runtime import PaperOperationsEngine
from app.strategy_engine import StrategyDecisionAction

from tests.test_paper_operations_runtime import (
    Clock,
    Snapshot,
    StubCoordinator,
    StubStrategy,
    request_builder,
)


class StubInferenceAdapter:
    def __init__(self, *, allowed: bool, reason: str = "test policy") -> None:
        self.allowed = allowed
        self.reason = reason
        self.begin_cycles: list[int] = []
        self.calls: list[tuple[int, int, str]] = []

    def begin_cycle(self, cycle: int) -> bool:
        self.begin_cycles.append(cycle)
        return cycle == 1

    def evaluate(
        self,
        *,
        snapshot,
        session,
        cycle: int,
        symbol_index: int,
    ) -> RuntimeInferenceAudit:
        del session
        symbol = snapshot.symbol.strip().upper()
        self.calls.append((cycle, symbol_index, symbol))
        return RuntimeInferenceAudit(
            cycle=cycle,
            symbol_index=symbol_index,
            symbol=symbol,
            model_id="model-runtime-test",
            prediction=1 if self.allowed else 0,
            action="BUY" if self.allowed else "HOLD",
            score=Decimal("1"),
            threshold=Decimal("0"),
            confidence=Decimal("0.90"),
            minimum_confidence=Decimal("0.60"),
            feature_names=("feature_a",),
            feature_sha256="a" * 64,
            model_reloaded=cycle == 1,
            allows_order_intent=self.allowed,
            reason=self.reason,
            error=None,
        )


def _engine(
    *,
    inference_adapter,
    request_builder_override=request_builder,
    cycle_sink=None,
):
    return PaperOperationsEngine(
        session_id="paper-learning-runtime",
        initial_cash=Decimal("10000"),
        snapshot_source=lambda timestamp: (Snapshot("AAPL"),),
        strategy_engine=StubStrategy(StrategyDecisionAction.ENTER_LONG),
        coordinator=StubCoordinator(),
        request_builder=request_builder_override,
        clock=Clock(),
        cycle_sink=cycle_sink,
        inference_adapter=inference_adapter,
    )


def test_runtime_inference_allows_executable_strategy_decision() -> None:
    calls = []

    def build_request(decision, snapshot, session, cycle, index):
        calls.append((decision.action, snapshot.symbol, cycle, index))
        return request_builder(
            decision,
            snapshot,
            session,
            cycle,
            index,
        )

    adapter = StubInferenceAdapter(allowed=True)
    results = []
    engine = _engine(
        inference_adapter=adapter,
        request_builder_override=build_request,
        cycle_sink=results.append,
    )

    engine.start()
    engine.run_cycle()

    assert calls == [(StrategyDecisionAction.ENTER_LONG, "AAPL", 1, 1)]
    assert adapter.begin_cycles == [1]
    assert adapter.calls == [(1, 1, "AAPL")]
    assert results[0].decisions[0].action is StrategyDecisionAction.ENTER_LONG
    assert results[0].inference_audits[0].allows_order_intent is True


def test_runtime_inference_veto_converts_buy_to_hold() -> None:
    calls = []

    def unexpected_request(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("request builder must not run after inference veto")

    adapter = StubInferenceAdapter(
        allowed=False,
        reason="model prediction is HOLD",
    )
    results = []
    engine = _engine(
        inference_adapter=adapter,
        request_builder_override=unexpected_request,
        cycle_sink=results.append,
    )

    engine.start()
    engine.run_cycle()

    assert calls == []
    assert results[0].decisions[0].action is StrategyDecisionAction.HOLD
    assert results[0].decisions[0].creates_order_intent is False
    assert results[0].inference_audits[0].allows_order_intent is False
    assert engine.state.status.value == "RUNNING"
    assert any(
        event.event_type == "INFERENCE_VETO"
        for event in engine.state.events
    )


def test_runtime_without_inference_preserves_previous_behavior() -> None:
    calls = []

    def build_request(decision, snapshot, session, cycle, index):
        calls.append((decision.action, snapshot.symbol))
        return request_builder(
            decision,
            snapshot,
            session,
            cycle,
            index,
        )

    engine = _engine(
        inference_adapter=None,
        request_builder_override=build_request,
    )

    engine.start()
    engine.run_cycle()

    assert calls == [(StrategyDecisionAction.ENTER_LONG, "AAPL")]


def test_runtime_reloads_inference_once_at_each_cycle_boundary() -> None:
    adapter = StubInferenceAdapter(allowed=False)
    engine = _engine(inference_adapter=adapter)

    engine.start()
    engine.run_cycle()
    engine.run_cycle()

    assert adapter.begin_cycles == [1, 2]
    assert adapter.calls == [
        (1, 1, "AAPL"),
        (2, 1, "AAPL"),
    ]
