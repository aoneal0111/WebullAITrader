from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.operations_core import (
    ApplicationState,
    OperationsOrder,
    OperationsPosition,
    PaperRuntimeSnapshot,
)
from app.read_models.portfolio import (
    PortfolioReadModelSnapshot,
    project_portfolio_read_model,
)


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def make_runtime() -> PaperRuntimeSnapshot:
    return PaperRuntimeSnapshot(
        cycle=3,
        timestamp=NOW,
        session_id="session-1",
        symbols=("AAPL", "MSFT"),
        decisions_processed=8,
        orders_attempted=5,
        orders_filled=3,
        orders_rejected=1,
        orders_not_filled=1,
        decisions_skipped=3,
        winning_fills=2,
        losing_fills=1,
        breakeven_fills=0,
        realized_pnl=Decimal("75.00"),
        unrealized_pnl=Decimal("25.00"),
        current_equity=Decimal("10100.00"),
        peak_equity=Decimal("10250.00"),
        current_drawdown=Decimal("150.00"),
        win_rate=Decimal("0.6667"),
        total_return=Decimal("0.01"),
        maximum_drawdown=Decimal("0.02"),
    )


def make_order() -> OperationsOrder:
    return OperationsOrder(
        order_id="order-1",
        symbol="AAPL",
        side="BUY",
        quantity="10",
        status="ACCEPTED",
        updated_at=NOW,
    )


def make_position() -> OperationsPosition:
    return OperationsPosition(
        account_id="account-1",
        symbol="AAPL",
        asset_type="EQUITY",
        quantity="10",
        average_cost="185.25",
        market_value="1900.00",
        unrealized_gain_loss="47.50",
        realized_gain_loss="12.25",
        currency="USD",
        updated_at=NOW,
    )


def test_empty_application_state_projects_initial_snapshot() -> None:
    assert project_portfolio_read_model(
        ApplicationState()
    ) == PortfolioReadModelSnapshot.initial()


def test_projection_uses_authoritative_runtime_accounting() -> None:
    snapshot = project_portfolio_read_model(
        ApplicationState(paper_runtime=make_runtime())
    )

    assert snapshot.timestamp == NOW
    assert snapshot.session_id == "session-1"
    assert snapshot.equity == Decimal("10100.00")
    assert snapshot.peak_equity == Decimal("10250.00")
    assert snapshot.realized_pnl == Decimal("75.00")
    assert snapshot.unrealized_pnl == Decimal("25.00")
    assert snapshot.current_drawdown == Decimal("150.00")
    assert snapshot.total_return == Decimal("0.01")
    assert snapshot.maximum_drawdown == Decimal("0.02")
    assert snapshot.win_rate == Decimal("0.6667")


def test_projection_counts_orders_and_positions() -> None:
    snapshot = project_portfolio_read_model(
        ApplicationState(
            orders=(make_order(),),
            positions=(make_position(),),
        )
    )

    assert snapshot.order_count == 1
    assert snapshot.position_count == 1


def test_projection_is_deterministic() -> None:
    state = ApplicationState(
        paper_runtime=make_runtime(),
        orders=(make_order(),),
        positions=(make_position(),),
    )

    assert project_portfolio_read_model(state) == project_portfolio_read_model(state)


def test_projector_rejects_non_application_state() -> None:
    with pytest.raises(TypeError, match="state must be an ApplicationState"):
        project_portfolio_read_model(object())  # type: ignore[arg-type]
