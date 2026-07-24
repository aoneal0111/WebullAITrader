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
