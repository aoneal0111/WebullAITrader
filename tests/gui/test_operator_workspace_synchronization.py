import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

from app.gui.models import (
    DashboardSnapshot,
    DecisionCenterSnapshot,
    DecisionRow,
    LifecycleExplorerSnapshot,
    LifecycleRow,
    OrdersSnapshot,
    PortfolioSnapshot,
    PositionsSnapshot,
    TimelineRow,
    TimelineSnapshot,
)
from app.gui.pages.dashboard import DashboardPage
from app.operations_core import (
    OperatorDecisionSelected,
    OperatorSymbolSelected,
    OperatorTimelineSelected,
    OperatorTradeSelected,
)
from app.read_models.operator_workspace import (
    OperatorWorkspaceSnapshot,
    WorkspaceSelectionSource,
)


APPLICATION = QApplication.instance() or QApplication([])


def test_dashboard_routes_read_only_panel_selections_as_events() -> None:
    page = DashboardPage()
    events = []
    page.selection_requested.connect(events.append)
    snapshot = replace(
        DashboardSnapshot.initial(),
        portfolio=replace(
            PortfolioSnapshot.initial(),
            selected_symbol="AAPL",
        ),
        decisions=DecisionCenterSnapshot(
            cycle="Cycle 1",
            updated_at="Updated 12:00:00",
            rows=(
                DecisionRow(
                    symbol="AAPL",
                    action="ENTER LONG",
                    confidence="85%",
                    score="0.85",
                    rationale="approved",
                    decided_at="12:00:00",
                    selection_id="decision-1",
                ),
            ),
            selected_decision="decision-1",
        ),
        timeline=TimelineSnapshot(
            rows=(
                TimelineRow(
                    time="12:00:00",
                    category="DECISION",
                    severity="SUCCESS",
                    summary="AAPL approved",
                    selection_id="timeline-1",
                    symbol="AAPL",
                ),
            ),
            max_entries=500,
            selected_entry="timeline-1",
        ),
        lifecycle_explorer=LifecycleExplorerSnapshot(
            rows=(
                LifecycleRow(
                    symbol="AAPL",
                    status="OPEN",
                    opened="12:00:00",
                    closed="--",
                    realized_pnl="$0.00",
                    entries=(),
                ),
            ),
            selected_symbol="AAPL",
        ),
        positions=PositionsSnapshot(
            rows=(("AAPL", "1", "$100.00", "+$5.00"),),
            symbols=("AAPL",),
            selected_symbol="AAPL",
        ),
        orders=OrdersSnapshot(
            rows=(("BUY 1 AAPL", "FILLED", "12:00:00"),),
            symbols=("AAPL",),
            order_ids=("order-1",),
            selected_order="order-1",
        ),
        operator_workspace=OperatorWorkspaceSnapshot(
            selected_symbol="AAPL",
            selected_decision="decision-1",
            selection_source=WorkspaceSelectionSource.DECISION,
        ),
    )

    page.render(snapshot)
    page.decision_center._request_selection(0, 0)
    page.timeline_panel._request_selection(0, 0)
    page.trade_lifecycle_panel._request_selection(
        page.trade_lifecycle_panel.tree.topLevelItem(0),
        0,
    )
    page.positions_panel._request_selection(0, 0)
    page.orders_panel._request_selection(0, 0)

    assert isinstance(events[0], OperatorDecisionSelected)
    assert events[0].decision_id == "decision-1"
    assert isinstance(events[1], OperatorTimelineSelected)
    assert events[1].timeline_entry_id == "timeline-1"
    assert isinstance(events[2], OperatorTradeSelected)
    assert isinstance(events[3], OperatorSymbolSelected)
    assert events[3].selection_source == "POSITION"
    assert isinstance(events[4], OperatorSymbolSelected)
    assert events[4].selection_source == "ORDER"
    assert events[4].selection_id == "order-1"
    assert page.portfolio_metrics.selected_symbol.text() == "FOCUS: AAPL"
    assert page.trade_lifecycle_panel.tree.topLevelItem(0).isExpanded()
    assert (
        page.decision_center.table.item(0, 0).background().color().name()
        == "#243b53"
    )
    assert set(page.findChildren(QPushButton)) == (
        set(page.replay_panel.findChildren(QPushButton))
        | set(page.event_store_panel.findChildren(QPushButton))
        | set(page.experiment_panel.findChildren(QPushButton))
    )
    page.deleteLater()
