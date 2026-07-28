from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.operations_core import TradeLifecycleUpdated


NOW = datetime(2026, 7, 28, 17, 0, tzinfo=timezone.utc)


def test_trade_lifecycle_event_is_immutable_and_broker_neutral() -> None:
    event = TradeLifecycleUpdated(
        symbol="AAPL",
        phase="EVIDENCE",
        title="Evidence approved",
        description="Evidence threshold passed.",
        order_id=None,
        position_id=None,
        cycle=3,
        realized_pnl=Decimal("12.50"),
        occurred_at=NOW,
    )

    assert event.symbol == "AAPL"
    assert event.phase == "EVIDENCE"
    assert event.realized_pnl == Decimal("12.50")


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"symbol": "aapl"}, "uppercase"),
        ({"phase": ""}, "phase"),
        ({"phase": "scanned"}, "uppercase"),
        ({"title": " padded "}, "title"),
        ({"cycle": -1}, "cycle"),
        ({"realized_pnl": Decimal("NaN")}, "finite Decimal"),
    ),
)
def test_trade_lifecycle_event_validation(changes, message) -> None:
    values = {
        "symbol": "AAPL",
        "phase": "SCANNED",
        "title": "Scanned",
        "description": "Candidate scanned.",
        "occurred_at": NOW,
    }
    values.update(changes)

    with pytest.raises(ValueError, match=message):
        TradeLifecycleUpdated(**values)
