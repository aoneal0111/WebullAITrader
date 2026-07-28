from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.operations_core import (
    DecisionsUpdated,
    OperationsDecision,
    OperationsOrder,
    OrdersUpdated,
    TradeLifecycleUpdated,
)
from app.recording import RecordedSession, RecordingSerializer


NOW = datetime(2026, 7, 28, 21, 0, tzinfo=timezone.utc)


@pytest.fixture
def event_store_sessions():
    serializer = RecordingSerializer()

    def make(session_id: str, offset: int = 0):
        start = NOW + timedelta(minutes=offset)
        events = (
            DecisionsUpdated(
                cycle=1,
                decisions=(
                    OperationsDecision(
                        symbol="AAPL",
                        action="ENTER_LONG",
                        confidence=90,
                        score=Decimal("0.9"),
                        reasons=("approved",),
                        source_action="BUY",
                        position_quantity=Decimal("0"),
                        strategy_version="1.0",
                        decided_at=start,
                    ),
                ),
                occurred_at=start,
            ),
            OrdersUpdated(
                orders=(
                    OperationsOrder(
                        order_id=f"order-{session_id}",
                        symbol="AAPL",
                        side="BUY",
                        quantity="1",
                        status="FILLED",
                        updated_at=start + timedelta(seconds=1),
                    ),
                ),
                occurred_at=start + timedelta(seconds=1),
            ),
            TradeLifecycleUpdated(
                symbol="AAPL",
                phase="FILLED",
                title="Filled",
                description="Order filled.",
                order_id=f"order-{session_id}",
                position_id=f"position-{session_id}",
                cycle=1,
                occurred_at=start + timedelta(seconds=2),
            ),
        )
        session = RecordedSession(
            session_id=session_id,
            started_at=start,
            ended_at=start + timedelta(seconds=2),
            strategy_version="1.0",
            application_version="0.1.0",
            broker="BROKER_NEUTRAL",
            runtime_mode="PAPER",
            events=tuple(
                serializer.record_event(event, index)
                for index, event in enumerate(events, start=1)
            ),
        )
        return session, events

    return make
