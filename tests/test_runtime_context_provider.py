from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.execution_coordinator.context_provider import CoordinationContextProvider
from app.execution_coordinator.runtime_context_provider import (
    RuntimeCoordinationContextProvider,
)
from app.paper_session import create_paper_session


def test_runtime_provider_implements_context_provider_contract() -> None:
    provider = RuntimeCoordinationContextProvider()

    assert isinstance(provider, CoordinationContextProvider)


def test_runtime_provider_is_not_implemented_yet() -> None:
    provider = RuntimeCoordinationContextProvider()
    timestamp = datetime(2026, 7, 23, 14, 0, tzinfo=UTC)
    session = create_paper_session(
        session_id="paper-1",
        initial_cash=Decimal("10000"),
        started_at=timestamp,
    )

    with pytest.raises(NotImplementedError):
        provider.get_context(
            order_intent=object(),
            symbol="AAPL",
            snapshot=object(),
            session=session,
            cycle=1,
            symbol_index=0,
        )


class RecordingAssembler:
    def __init__(self) -> None:
        self.calls = []

    def build(self, **values):
        self.calls.append(values)
        return object()


def test_runtime_provider_delegates_authoritative_inputs_to_assembler() -> None:
    timestamp = datetime(2026, 7, 23, 14, 0, tzinfo=UTC)
    session = create_paper_session(
        session_id="paper-1",
        initial_cash=Decimal("10000"),
        started_at=timestamp,
    )
    order_intent = object()
    snapshot = object()
    source_calls = []

    from app.execution_coordinator.runtime_context_provider import (
        RuntimeContextInputs,
    )

    inputs = RuntimeContextInputs(
        account_type=object(),
        timestamp=timestamp,
        market_state=object(),
        risk_limits=object(),
        compliance_limits=object(),
        gfv_decision=object(),
        kill_switch=object(),
        market_quote=object(),
        execution_config=object(),
    )

    def input_source(**values):
        source_calls.append(values)
        return inputs

    assembler = RecordingAssembler()
    provider = RuntimeCoordinationContextProvider(
        input_source=input_source,
        assembler=assembler,
    )

    result = provider.get_context(
        order_intent=order_intent,
        symbol="AAPL",
        snapshot=snapshot,
        session=session,
        cycle=3,
        symbol_index=2,
    )

    assert result is not None
    assert source_calls == [
        {
            "order_intent": order_intent,
            "symbol": "AAPL",
            "snapshot": snapshot,
            "session": session,
            "cycle": 3,
            "symbol_index": 2,
        }
    ]
    assert assembler.calls == [
        {
            "portfolio": session.portfolio,
            "account_type": inputs.account_type,
            "filled_orders": session.statistics.orders_filled,
            "symbol": "AAPL",
            "timestamp": timestamp,
            "market_state": inputs.market_state,
            "risk_limits": inputs.risk_limits,
            "compliance_limits": inputs.compliance_limits,
            "gfv_decision": inputs.gfv_decision,
            "kill_switch": inputs.kill_switch,
            "market_quote": inputs.market_quote,
            "execution_config": inputs.execution_config,
            "journal": session.journal,
            "equity_curve": session.equity_curve,
        }
    ]


def test_runtime_provider_rejects_invalid_input_source_result() -> None:
    timestamp = datetime(2026, 7, 23, 14, 0, tzinfo=UTC)
    session = create_paper_session(
        session_id="paper-1",
        initial_cash=Decimal("10000"),
        started_at=timestamp,
    )
    provider = RuntimeCoordinationContextProvider(
        input_source=lambda **_: object()
    )

    with pytest.raises(
        TypeError,
        match="must return RuntimeContextInputs",
    ):
        provider.get_context(
            order_intent=object(),
            symbol="AAPL",
            snapshot=object(),
            session=session,
            cycle=1,
            symbol_index=0,
        )
