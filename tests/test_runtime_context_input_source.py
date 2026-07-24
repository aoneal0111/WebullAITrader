from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.execution_coordinator.runtime_context_input_source import (
    ConfiguredRuntimeContextInputSource,
    RuntimeContextConfiguration,
)
from app.paper_session import create_paper_session


NOW = datetime(2026, 7, 23, 14, 0, tzinfo=UTC)


def _session():
    return create_paper_session(
        session_id="paper-1",
        initial_cash=Decimal("10000"),
        started_at=NOW,
    )


def test_configured_runtime_input_source_composes_authoritative_inputs() -> None:
    session = _session()
    order_intent = object()
    snapshot = object()

    account_type = object()
    risk_limits = object()
    compliance_limits = object()
    kill_switch = object()
    execution_config = object()
    market_state = object()
    market_quote = object()
    gfv_decision = object()

    calls = []

    def timestamp_source(intent, supplied_snapshot, supplied_session):
        calls.append(
            (
                "timestamp",
                intent,
                supplied_snapshot,
                supplied_session,
            )
        )
        return NOW

    def market_state_source(
        intent,
        supplied_snapshot,
        supplied_session,
        timestamp,
    ):
        calls.append(
            (
                "market_state",
                intent,
                supplied_snapshot,
                supplied_session,
                timestamp,
            )
        )
        return market_state

    def market_quote_source(
        intent,
        supplied_snapshot,
        supplied_session,
        timestamp,
    ):
        calls.append(
            (
                "market_quote",
                intent,
                supplied_snapshot,
                supplied_session,
                timestamp,
            )
        )
        return market_quote

    def gfv_decision_source(
        intent,
        supplied_snapshot,
        supplied_session,
        timestamp,
    ):
        calls.append(
            (
                "gfv",
                intent,
                supplied_snapshot,
                supplied_session,
                timestamp,
            )
        )
        return gfv_decision

    source = ConfiguredRuntimeContextInputSource(
        configuration=RuntimeContextConfiguration(
            account_type=account_type,
            risk_limits=risk_limits,
            compliance_limits=compliance_limits,
            kill_switch=kill_switch,
            execution_config=execution_config,
        ),
        timestamp_source=timestamp_source,
        market_state_source=market_state_source,
        market_quote_source=market_quote_source,
        gfv_decision_source=gfv_decision_source,
    )

    result = source(
        order_intent=order_intent,
        symbol="AAPL",
        snapshot=snapshot,
        session=session,
        cycle=3,
        symbol_index=2,
    )

    assert result.account_type is account_type
    assert result.timestamp == NOW
    assert result.market_state is market_state
    assert result.risk_limits is risk_limits
    assert result.compliance_limits is compliance_limits
    assert result.gfv_decision is gfv_decision
    assert result.kill_switch is kill_switch
    assert result.market_quote is market_quote
    assert result.execution_config is execution_config

    assert calls == [
        (
            "timestamp",
            order_intent,
            snapshot,
            session,
        ),
        (
            "market_state",
            order_intent,
            snapshot,
            session,
            NOW,
        ),
        (
            "gfv",
            order_intent,
            snapshot,
            session,
            NOW,
        ),
        (
            "market_quote",
            order_intent,
            snapshot,
            session,
            NOW,
        ),
    ]


def test_configured_runtime_input_source_rejects_naive_timestamp() -> None:
    session = _session()

    source = ConfiguredRuntimeContextInputSource(
        configuration=RuntimeContextConfiguration(
            account_type=object(),
            risk_limits=object(),
            compliance_limits=object(),
            kill_switch=object(),
            execution_config=object(),
        ),
        timestamp_source=lambda *_: datetime(2026, 7, 23, 14, 0),
        market_state_source=lambda *_: object(),
        market_quote_source=lambda *_: object(),
        gfv_decision_source=lambda *_: None,
    )

    with pytest.raises(
        ValueError,
        match="timestamp must be timezone-aware",
    ):
        source(
            order_intent=object(),
            symbol="AAPL",
            snapshot=object(),
            session=session,
            cycle=1,
            symbol_index=1,
        )


def test_configured_runtime_input_source_is_provider_compatible() -> None:
    session = _session()

    configuration = RuntimeContextConfiguration(
        account_type=object(),
        risk_limits=object(),
        compliance_limits=object(),
        kill_switch=object(),
        execution_config=object(),
    )
    source = ConfiguredRuntimeContextInputSource(
        configuration=configuration,
        timestamp_source=lambda *_: NOW,
        market_state_source=lambda *_: object(),
        market_quote_source=lambda *_: object(),
        gfv_decision_source=lambda *_: None,
    )

    result = source(
        order_intent=object(),
        symbol="AAPL",
        snapshot=object(),
        session=session,
        cycle=1,
        symbol_index=1,
    )

    assert result.account_type is configuration.account_type
    assert result.risk_limits is configuration.risk_limits
    assert result.compliance_limits is configuration.compliance_limits
    assert result.kill_switch is configuration.kill_switch
    assert result.execution_config is configuration.execution_config
