from datetime import datetime, timezone
from decimal import Decimal
import json

import pytest

from app.operations_core import (
    DecisionsUpdated,
    OperationsDecision,
    OperationsOrder,
    OrdersUpdated,
    PaperRuntimeSnapshot,
    PaperRuntimeUpdated,
    RuntimeStarting,
)
from app.recording import (
    RecordedSession,
    RecordingFormatError,
    RecordingSerializer,
)


NOW = datetime(2026, 7, 28, 20, 0, tzinfo=timezone.utc)


def events():
    return (
        RuntimeStarting(environment="PAPER", occurred_at=NOW),
        OrdersUpdated(
            orders=(
                OperationsOrder(
                    order_id="order-1",
                    symbol="AAPL",
                    side="BUY",
                    quantity="1",
                    status="FILLED",
                    updated_at=NOW,
                ),
            ),
            occurred_at=NOW,
        ),
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
                    decided_at=NOW,
                ),
            ),
            occurred_at=NOW,
        ),
        PaperRuntimeUpdated(
            snapshot=PaperRuntimeSnapshot(
                cycle=1,
                timestamp=NOW,
                session_id="paper-session",
                symbols=("AAPL",),
                decisions_processed=1,
                orders_attempted=1,
                orders_filled=1,
                orders_rejected=0,
                orders_not_filled=0,
                decisions_skipped=0,
                winning_fills=1,
                losing_fills=0,
                breakeven_fills=0,
                realized_pnl=Decimal("10"),
                unrealized_pnl=Decimal("5"),
                current_equity=Decimal("10015"),
                peak_equity=Decimal("10015"),
                current_drawdown=Decimal("0"),
                win_rate=Decimal("100"),
                total_return=Decimal("0.15"),
                maximum_drawdown=Decimal("0"),
            ),
            occurred_at=NOW,
        ),
    )


def recorded_session(source_events=None) -> RecordedSession:
    serializer = RecordingSerializer()
    source_events = events() if source_events is None else source_events
    return RecordedSession(
        session_id="session-1",
        started_at=NOW,
        ended_at=NOW,
        strategy_version="1.0",
        application_version="0.1.0",
        broker="BROKER_NEUTRAL",
        runtime_mode="PAPER",
        events=tuple(
            serializer.record_event(event, index)
            for index, event in enumerate(source_events, start=1)
        ),
    )


def test_serializer_round_trips_nested_immutable_events() -> None:
    serializer = RecordingSerializer()
    source_events = events()
    session = recorded_session(source_events)

    restored_session = serializer.deserialize(
        serializer.serialize(session)
    )
    restored_events = tuple(
        serializer.restore_event(event)
        for event in restored_session.events
    )

    assert restored_session == session
    assert restored_events == source_events


def test_serializer_ignores_unknown_extension_fields() -> None:
    serializer = RecordingSerializer()
    document = json.loads(
        serializer.serialize(recorded_session()).decode("utf-8")
    )
    document["future_envelope_field"] = {"enabled": True}
    document["session"]["future_session_field"] = "preserved externally"
    from hashlib import sha256

    canonical = json.dumps(
        document["session"],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    document["checksum"] = sha256(canonical).hexdigest()

    result = serializer.deserialize(
        json.dumps(
            document,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )

    assert result.session_id == "session-1"


def test_serializer_rejects_checksum_corruption() -> None:
    serializer = RecordingSerializer()
    document = json.loads(
        serializer.serialize(recorded_session()).decode("utf-8")
    )
    document["session"]["broker"] = "TAMPERED"

    with pytest.raises(RecordingFormatError, match="checksum"):
        serializer.deserialize(json.dumps(document).encode("utf-8"))


def test_serializer_rejects_unsupported_schema_version() -> None:
    serializer = RecordingSerializer()
    document = json.loads(
        serializer.serialize(recorded_session()).decode("utf-8")
    )
    document["schema_version"] = 2

    with pytest.raises(RecordingFormatError, match="schema version"):
        serializer.deserialize(json.dumps(document).encode("utf-8"))


@pytest.mark.parametrize("data", (b"", b"[]", b"not-json"))
def test_serializer_rejects_corrupted_documents(data: bytes) -> None:
    with pytest.raises(RecordingFormatError):
        RecordingSerializer().deserialize(data)
