from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.gui.projections.dashboard_projection import project_dashboard
from app.operations_core import (
    ApplicationState,
    OperationsOrder,
    OperationsPosition,
)
from app.read_models.decisions import (
    DecisionReadModel,
    DecisionsReadModelSnapshot,
)
from app.read_models.operator_workspace import (
    OperatorWorkspaceSnapshot,
    WorkspaceSelectionSource,
)
from app.read_models.timeline import (
    TimelineCategory,
    TimelineEntry,
    TimelineSeverity,
    TimelineSnapshot,
)
from app.read_models.trade_lifecycle import (
    TradeLifecycle,
    TradeLifecycleEntry,
    TradeLifecyclePhase,
    TradeLifecycleSnapshot,
    TradeLifecycleStatus,
)


NOW = datetime(2026, 7, 28, 18, 0, tzinfo=timezone.utc)


def _decision(symbol: str) -> DecisionReadModel:
    return DecisionReadModel(
        symbol=symbol,
        action="ENTER_LONG",
        confidence=85,
        score=Decimal("0.85"),
        reasons=("approved",),
        source_action="BUY",
        position_quantity=Decimal("0"),
        strategy_version="1.0",
        decided_at=NOW,
    )


def _position(symbol: str) -> OperationsPosition:
    return OperationsPosition(
        account_id="paper-account",
        symbol=symbol,
        asset_type="EQUITY",
        quantity="1",
        average_cost="100",
        market_value="105",
        unrealized_gain_loss="5",
        realized_gain_loss="0",
        currency="USD",
        updated_at=NOW,
    )


def _order(symbol: str, order_id: str) -> OperationsOrder:
    return OperationsOrder(
        order_id=order_id,
        symbol=symbol,
        side="BUY",
        quantity="1",
        status="FILLED",
        updated_at=NOW,
    )


def _lifecycle(symbol: str) -> TradeLifecycle:
    return TradeLifecycle(
        symbol=symbol,
        entries=(
            TradeLifecycleEntry(
                timestamp=NOW,
                phase=TradeLifecyclePhase.DECISION,
                title="Enter long",
                description="Approved.",
                symbol=symbol,
            ),
        ),
        status=TradeLifecycleStatus.OPEN,
        opened_at=NOW,
        closed_at=None,
        realized_pnl=Decimal("0"),
    )


def test_dashboard_merges_all_views_around_selected_symbol() -> None:
    state = ApplicationState(
        orders=(
            _order("MSFT", "order-msft"),
            _order("AAPL", "order-aapl"),
        ),
        positions=(_position("MSFT"), _position("AAPL")),
    )
    decisions = DecisionsReadModelSnapshot(
        cycle=7,
        updated_at=NOW,
        decisions=(_decision("MSFT"), _decision("AAPL")),
    )
    timeline = TimelineSnapshot(
        entries=(
            TimelineEntry(
                timestamp=NOW,
                category=TimelineCategory.DECISION,
                severity=TimelineSeverity.SUCCESS,
                title="AAPL approved",
                description="Entry approved.",
                symbol="AAPL",
            ),
            TimelineEntry(
                timestamp=NOW,
                category=TimelineCategory.DECISION,
                severity=TimelineSeverity.INFO,
                title="MSFT held",
                description="No entry.",
                symbol="MSFT",
            ),
        )
    )
    lifecycles = TradeLifecycleSnapshot(
        lifecycles=(_lifecycle("MSFT"), _lifecycle("AAPL")),
        selected_symbol="MSFT",
    )
    unselected = project_dashboard(
        state,
        decisions=decisions,
        timeline=timeline,
        trade_lifecycle=lifecycles,
    )
    aapl_decision_id = next(
        row.selection_id
        for row in unselected.decisions.rows
        if row.symbol == "AAPL"
    )
    workspace = OperatorWorkspaceSnapshot(
        selected_symbol="AAPL",
        selected_decision=aapl_decision_id,
        selection_source=WorkspaceSelectionSource.DECISION,
    )

    dashboard = project_dashboard(
        state,
        decisions=decisions,
        timeline=timeline,
        trade_lifecycle=lifecycles,
        operator_workspace=workspace,
    )

    assert dashboard.operator_workspace is workspace
    assert tuple(row.symbol for row in dashboard.decisions.rows) == ("AAPL",)
    assert dashboard.decisions.selected_decision == aapl_decision_id
    assert dashboard.portfolio.selected_symbol == "AAPL"
    assert dashboard.positions.selected_symbol == "AAPL"
    assert dashboard.orders.selected_order == "order-aapl"
    assert dashboard.lifecycle_explorer.selected_symbol == "AAPL"
    selected_timeline = next(
        row
        for row in dashboard.timeline.rows
        if row.selection_id == dashboard.timeline.selected_entry
    )
    assert selected_timeline.symbol == "AAPL"


def test_dashboard_rejects_wrong_workspace_snapshot() -> None:
    with pytest.raises(TypeError, match="OperatorWorkspaceSnapshot"):
        project_dashboard(
            ApplicationState(),
            operator_workspace=object(),  # type: ignore[arg-type]
        )
