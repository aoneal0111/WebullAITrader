from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.gui.models import LifecycleExplorerSnapshot
from app.gui.projections.dashboard_projection import project_dashboard
from app.operations_core import ApplicationState
from app.read_models.trade_lifecycle import (
    TradeLifecycle,
    TradeLifecycleEntry,
    TradeLifecyclePhase,
    TradeLifecycleSnapshot,
    TradeLifecycleStatus,
)


NOW = datetime(2026, 7, 28, 17, 0, tzinfo=timezone.utc)


def test_dashboard_projects_lifecycles_and_expandable_entries() -> None:
    read_model = TradeLifecycleSnapshot(
        lifecycles=(
            TradeLifecycle(
                symbol="AAPL",
                entries=(
                    TradeLifecycleEntry(
                        timestamp=NOW,
                        phase=TradeLifecyclePhase.DECISION,
                        title="Enter long",
                        description="Entry approved.",
                        symbol="AAPL",
                        order_id="order-1",
                        cycle=2,
                    ),
                ),
                status=TradeLifecycleStatus.OPEN,
                opened_at=NOW,
                closed_at=None,
                realized_pnl=Decimal("42.13"),
            ),
        ),
        selected_symbol="AAPL",
    )

    explorer = project_dashboard(
        ApplicationState(),
        trade_lifecycle=read_model,
    ).lifecycle_explorer

    assert explorer.selected_symbol == "AAPL"
    assert explorer.rows[0].symbol == "AAPL"
    assert explorer.rows[0].status == "OPEN"
    assert explorer.rows[0].opened != "--"
    assert explorer.rows[0].closed == "--"
    assert explorer.rows[0].realized_pnl == "+$42.13"
    assert explorer.rows[0].entries[0].phase == "DECISION"
    assert explorer.rows[0].entries[0].summary == (
        "Enter long: Entry approved. (Order order-1 | Cycle 2)"
    )


def test_dashboard_defaults_to_empty_lifecycle_explorer() -> None:
    assert project_dashboard(ApplicationState()).lifecycle_explorer == (
        LifecycleExplorerSnapshot.initial()
    )


def test_dashboard_rejects_wrong_trade_lifecycle_snapshot() -> None:
    with pytest.raises(TypeError, match="TradeLifecycleSnapshot"):
        project_dashboard(
            ApplicationState(),
            trade_lifecycle=object(),  # type: ignore[arg-type]
        )
