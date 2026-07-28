from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.read_models.portfolio import PortfolioReadModelSnapshot


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def test_initial_snapshot_contains_zero_values() -> None:
    snapshot = PortfolioReadModelSnapshot.initial()

    assert snapshot.timestamp is None
    assert snapshot.session_id is None
    assert snapshot.equity == Decimal("0")
    assert snapshot.order_count == 0
    assert snapshot.position_count == 0


def test_snapshot_is_immutable() -> None:
    snapshot = PortfolioReadModelSnapshot.initial()

    with pytest.raises(FrozenInstanceError):
        snapshot.order_count = 1  # type: ignore[misc]


def test_snapshot_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(ValueError, match="timestamp must be timezone-aware"):
        PortfolioReadModelSnapshot(
            timestamp=datetime(2026, 7, 27, 12, 0),
        )


def test_snapshot_requires_decimal_financial_values() -> None:
    with pytest.raises(TypeError, match="equity must be a Decimal"):
        PortfolioReadModelSnapshot(
            equity=100.0,  # type: ignore[arg-type]
        )


def test_snapshot_rejects_negative_counts() -> None:
    with pytest.raises(ValueError, match="order_count must be nonnegative"):
        PortfolioReadModelSnapshot(order_count=-1)


def test_snapshot_preserves_runtime_values() -> None:
    snapshot = PortfolioReadModelSnapshot(
        timestamp=NOW,
        session_id="session-1",
        equity=Decimal("10100.00"),
        peak_equity=Decimal("10250.00"),
        realized_pnl=Decimal("75.00"),
        unrealized_pnl=Decimal("25.00"),
        current_drawdown=Decimal("150.00"),
        total_return=Decimal("0.01"),
        maximum_drawdown=Decimal("0.02"),
        win_rate=Decimal("0.60"),
        order_count=4,
        position_count=2,
    )

    assert snapshot.timestamp == NOW
    assert snapshot.session_id == "session-1"
    assert snapshot.equity == Decimal("10100.00")
    assert snapshot.position_count == 2
