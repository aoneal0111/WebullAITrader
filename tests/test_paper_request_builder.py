from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from app.execution_coordinator.context_provider import CoordinationContext
from app.execution_coordinator.paper_request_builder import PaperRequestBuilder
from app.paper_session import create_paper_session
from app.strategy_engine import StrategyDecision, StrategyDecisionAction


NOW = datetime(2026, 7, 23, 14, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Snapshot:
    symbol: str


class StubIntentFactory:
    def __init__(self) -> None:
        self.calls = []

    def create(self, decision):
        self.calls.append(decision)
        return object()


class RecordingContextProvider:
    def __init__(self) -> None:
        self.calls = []

    def get_context(
        self,
        *,
        order_intent,
        symbol,
        snapshot,
        session,
        cycle,
        symbol_index,
    ):
        self.calls.append(
            {
                "symbol": symbol,
                "snapshot": snapshot,
                "session": session,
                "cycle": cycle,
                "symbol_index": symbol_index,
            }
        )
        return CoordinationContext(
            account_state=object(),
            market_state=object(),
            risk_limits=object(),
            compliance_limits=object(),
            gfv_decision=object(),
            kill_switch=object(),
            portfolio=session.portfolio,
            market_quote=object(),
            execution_config=object(),
            journal=session.journal,
            equity_curve=session.equity_curve,
        )


def test_builder_forwards_current_runtime_inputs_to_context_provider() -> None:
    decision = StrategyDecision(
        symbol="aapl",
        action=StrategyDecisionAction.ENTER_LONG,
        confidence=80,
        score=Decimal("0.8"),
        timestamp=NOW,
        reasons=("test",),
        source_action="BUY",
        position_quantity=Decimal("0"),
    )
    snapshot = Snapshot("AAPL")
    session = create_paper_session(
        session_id="paper-1",
        initial_cash=Decimal("10000"),
        started_at=NOW,
    )
    intent_factory = StubIntentFactory()
    context_provider = RecordingContextProvider()
    builder = PaperRequestBuilder(
        order_intent_factory=intent_factory,
        context_provider=context_provider,
    )

    request = builder(
        decision,
        snapshot,
        session,
        3,
        2,
    )

    assert context_provider.calls == [
        {
            "symbol": "AAPL",
            "snapshot": snapshot,
            "session": session,
            "cycle": 3,
            "symbol_index": 2,
        }
    ]
    assert intent_factory.calls == [decision]
    assert request.snapshot is snapshot
    assert request.portfolio is session.portfolio
    assert request.journal is session.journal
    assert request.equity_curve is session.equity_curve

