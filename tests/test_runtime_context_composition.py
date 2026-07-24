from datetime import UTC, datetime

from app.composition.runtime_context import (
    create_runtime_context_provider,
)
from app.execution_coordinator.runtime_context_input_source import (
    ConfiguredRuntimeContextInputSource,
    RuntimeContextConfiguration,
)
from app.execution_coordinator.runtime_context_provider import (
    RuntimeCoordinationContextProvider,
)


def _configuration() -> RuntimeContextConfiguration:
    return RuntimeContextConfiguration(
        account_type=object(),
        risk_limits=object(),
        compliance_limits=object(),
        kill_switch=object(),
        execution_config=object(),
    )


def _timestamp_source(order_intent, snapshot, session):
    del order_intent, snapshot, session
    return datetime(2026, 1, 1, tzinfo=UTC)


def _market_state_source(order_intent, snapshot, session, timestamp):
    del order_intent, snapshot, session, timestamp
    return object()


def _market_quote_source(order_intent, snapshot, session, timestamp):
    del order_intent, snapshot, session, timestamp
    return object()


def _gfv_decision_source(order_intent, snapshot, session, timestamp):
    del order_intent, snapshot, session, timestamp
    return object()


def _create_provider() -> RuntimeCoordinationContextProvider:
    return create_runtime_context_provider(
        configuration=_configuration(),
        timestamp_source=_timestamp_source,
        market_state_source=_market_state_source,
        market_quote_source=_market_quote_source,
        gfv_decision_source=_gfv_decision_source,
    )


def test_create_runtime_context_provider_composes_configured_input_source():
    provider = _create_provider()

    assert isinstance(provider, RuntimeCoordinationContextProvider)
    assert isinstance(
        provider.input_source,
        ConfiguredRuntimeContextInputSource,
    )


def test_create_runtime_context_provider_returns_fresh_instances():
    first = _create_provider()
    second = _create_provider()

    assert first is not second
    assert first.input_source is not second.input_source
