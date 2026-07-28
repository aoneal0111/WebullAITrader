from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.read_models.trade_lifecycle import (
    TradeLifecycle,
    TradeLifecycleEntry,
    TradeLifecyclePhase,
    TradeLifecycleSnapshot,
    TradeLifecycleStatus,
)


NOW = datetime(2026, 7, 28, 17, 0, tzinfo=timezone.utc)


def entry(**changes) -> TradeLifecycleEntry:
    values = {
        "timestamp": NOW,
        "phase": TradeLifecyclePhase.DECISION,
        "title": "Enter long",
        "description": "Strategy approved entry.",
        "symbol": "AAPL",
        "order_id": "order-1",
        "position_id": None,
        "cycle": 1,
    }
    values.update(changes)
    return TradeLifecycleEntry(**values)


def lifecycle(**changes) -> TradeLifecycle:
    values = {
        "symbol": "AAPL",
        "entries": (entry(),),
        "status": TradeLifecycleStatus.OPEN,
        "opened_at": NOW,
        "closed_at": None,
        "realized_pnl": Decimal("0"),
    }
    values.update(changes)
    return TradeLifecycle(**values)


def test_phase_and_status_enums_have_stable_values() -> None:
    assert len(TradeLifecyclePhase) == 15
    assert tuple(status.value for status in TradeLifecycleStatus) == (
        "OPEN",
        "CLOSED",
        "FAILED",
        "UNKNOWN",
    )


def test_models_are_frozen_and_slotted() -> None:
    model = lifecycle()
    snapshot = TradeLifecycleSnapshot(
        lifecycles=(model,),
        selected_symbol="AAPL",
    )

    with pytest.raises(FrozenInstanceError):
        model.status = TradeLifecycleStatus.CLOSED  # type: ignore[misc]
    with pytest.raises(AttributeError):
        snapshot.extra = ()  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"timestamp": NOW.replace(tzinfo=None)}, "timezone-aware"),
        ({"phase": "DECISION"}, "TradeLifecyclePhase"),
        ({"title": ""}, "title"),
        ({"description": " padded "}, "description"),
        ({"symbol": "aapl"}, "uppercase"),
        ({"order_id": ""}, "order_id"),
        ({"cycle": -1}, "cycle"),
    ),
)
def test_entry_validation(changes, message) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        entry(**changes)


def test_lifecycle_requires_immutable_matching_entries() -> None:
    with pytest.raises(TypeError, match="immutable tuple"):
        lifecycle(entries=[entry()])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="match lifecycle"):
        lifecycle(entries=(entry(symbol="MSFT"),))


def test_open_and_closed_status_require_consistent_timestamps() -> None:
    with pytest.raises(ValueError, match="open lifecycle"):
        lifecycle(opened_at=None)
    with pytest.raises(ValueError, match="closed lifecycle"):
        lifecycle(
            status=TradeLifecycleStatus.CLOSED,
            closed_at=None,
        )
    with pytest.raises(ValueError, match="cannot precede"):
        lifecycle(
            status=TradeLifecycleStatus.CLOSED,
            closed_at=NOW - timedelta(seconds=1),
        )


def test_lifecycle_requires_finite_decimal_pnl() -> None:
    with pytest.raises(ValueError, match="finite Decimal"):
        lifecycle(realized_pnl=Decimal("NaN"))


def test_snapshot_validates_uniqueness_and_selection() -> None:
    model = lifecycle()
    with pytest.raises(ValueError, match="unique"):
        TradeLifecycleSnapshot(lifecycles=(model, model))
    with pytest.raises(ValueError, match="existing lifecycle"):
        TradeLifecycleSnapshot(
            lifecycles=(model,),
            selected_symbol="MSFT",
        )


def test_initial_snapshot_is_empty() -> None:
    assert TradeLifecycleSnapshot.initial() == TradeLifecycleSnapshot()
