import os
from datetime import UTC, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from app.gui.models import (
    ActivityEntry,
    ActivitySnapshot,
    DecisionDetail,
    DecisionRow,
    DecisionsSnapshot,
    OrdersSnapshot,
    PositionsSnapshot,
    ReplayWorkspaceSnapshot,
    TimelineFilter,
    WatchlistRow,
    WatchlistSnapshot,
)
from app.gui.widgets.activity_panel import ActivityPanel
from app.gui.widgets.decisions_panel import DecisionsPanel
from app.gui.widgets.orders_panel import OrdersPanel
from app.gui.widgets.positions_panel import PositionsPanel
from app.gui.widgets.replay_status_panel import ReplayStatusPanel
from app.gui.widgets.watchlist_panel import WatchlistPanel


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def test_timeline_panel_emits_immutable_filter_intent(application) -> None:
    del application
    panel = ActivityPanel()
    filters = []
    panel.filters_changed.connect(filters.append)
    panel.render(
        ActivitySnapshot(
            entries=(
                ActivityEntry(
                    occurred_at=datetime(2026, 7, 30, tzinfo=UTC),
                    message="Order accepted",
                    category="ORDER",
                    severity="SUCCESS",
                    source="runtime",
                    related_symbol="AAPL",
                ),
            ),
            severity_options=("ALL", "SUCCESS"),
            category_options=("ALL", "ORDER"),
            symbol_options=("ALL", "AAPL"),
        )
    )

    panel._severity.setCurrentText("SUCCESS")
    panel._category.setCurrentText("ORDER")
    panel._symbol.setCurrentText("AAPL")
    panel._search.setText("accepted")

    assert filters[-1] == TimelineFilter(
        severity="SUCCESS",
        category="ORDER",
        symbol="AAPL",
        search="accepted",
    )
    assert panel._table.rowCount() == 1


def test_activity_panel_skips_identical_snapshot_table_rebuild(
    application,
    monkeypatch,
) -> None:
    del application
    panel = ActivityPanel()
    snapshot = ActivitySnapshot(
        entries=(
            ActivityEntry(
                occurred_at=datetime(2026, 7, 30, tzinfo=UTC),
                message="Order accepted",
                category="ORDER",
                severity="SUCCESS",
                source="runtime",
                related_symbol="AAPL",
            ),
        ),
        severity_options=("ALL", "SUCCESS"),
        category_options=("ALL", "ORDER"),
        symbol_options=("ALL", "AAPL"),
    )

    set_item_calls = 0
    original_set_item = panel._table.setItem

    def count_set_item(row, column, item):
        nonlocal set_item_calls
        set_item_calls += 1
        return original_set_item(row, column, item)

    monkeypatch.setattr(
        panel._table,
        "setItem",
        count_set_item,
    )

    panel.render(snapshot)
    first_render_calls = set_item_calls

    assert first_render_calls > 0
    assert panel._table.rowCount() == 1

    panel.render(snapshot)

    assert set_item_calls == first_render_calls

    changed = ActivitySnapshot(
        entries=(
            *snapshot.entries,
            ActivityEntry(
                occurred_at=datetime(
                    2026,
                    7,
                    30,
                    0,
                    0,
                    1,
                    tzinfo=UTC,
                ),
                message="Position opened",
                category="POSITION",
                severity="INFO",
                source="runtime",
                related_symbol="AAPL",
            ),
        ),
        severity_options=("ALL", "SUCCESS", "INFO"),
        category_options=("ALL", "ORDER", "POSITION"),
        symbol_options=("ALL", "AAPL"),
    )

    panel.render(changed)

    assert set_item_calls > first_render_calls
    assert panel._table.rowCount() == 2

def test_decision_panel_emits_selected_structured_identity(application) -> None:
    del application
    panel = DecisionsPanel()
    selected = []
    panel.decision_selected.connect(selected.append)
    row = DecisionRow(
        decision_id="decision-1",
        timestamp=datetime(2026, 7, 30, tzinfo=UTC),
        strategy="momentum",
        symbol="AAPL",
        action="BUY",
        confidence="90%",
        reasoning="Momentum breakout",
        risk="APPROVED",
        quantity="10",
        order_id="order-1",
        outcome="FILLED",
    )
    detail = DecisionDetail(
        decision_id="decision-1",
        title="BUY AAPL",
        confidence="90%",
        reasoning="Momentum breakout",
        risk="APPROVED",
        requested_quantity="10",
        resulting_order_id="order-1",
        lifecycle=("Decision generated", "Order order-1", "Filled"),
        execution_outcome="FILLED",
    )
    panel.render(DecisionsSnapshot(rows=(row,), selected=detail))

    panel._table.clearSelection()
    panel._table.selectRow(0)

    assert selected[-1] == "decision-1"
    assert panel._reasoning.text() == "Momentum breakout"
    assert panel._outcome.text() == "FILLED"


def test_watchlist_panel_emits_sort_field_and_renders_selection(
    application,
) -> None:
    del application
    panel = WatchlistPanel()
    requested = []
    panel.sort_requested.connect(requested.append)
    panel.render(
        WatchlistSnapshot(
            rows=(
                WatchlistRow(
                    symbol="AAPL",
                    selected=True,
                    latest_price="101.00",
                    change="+1.00",
                    change_percent="+1.00%",
                    bid="100.90",
                    ask="101.10",
                    volume="1,000",
                    market_status="OPEN",
                    last_update="10:00:00",
                    stale="LIVE",
                    rank="1",
                    score="91",
                    freshness="LIVE",
                    session="REGULAR",
                ),
            ),
            sort_field="latest_price",
        )
    )

    panel._table.horizontalHeader().sectionClicked.emit(3)

    assert requested == ["latest_price"]
    assert panel._table.item(0, 0).text() == "1"
    assert panel._table.item(0, 1).text() == "\u25cf AAPL"
    assert panel._table.item(0, 2).text() == "91"
    assert panel._table.item(0, 11).text() == "LIVE"
    assert panel._table.item(0, 12).text() == "REGULAR"


def test_dashboard_replay_status_renders_presenter_model(application) -> None:
    del application
    panel = ReplayStatusPanel()
    panel.render(
        ReplayWorkspaceSnapshot(
            status="Paused",
            current_position="3 / 10",
            events_processed="3",
            total_events="10",
            replay_speed="2\u00d7",
            elapsed_time="00:00:05.000",
            maximum_event_index=10,
            can_play=True,
            can_pause=False,
            can_step=True,
            can_restart=True,
            can_seek=True,
        )
    )

    assert panel._values["Status"].text() == "Paused"
    assert panel._values["Position"].text() == "3 / 10"
    assert panel._values["Speed"].text() == "2\u00d7"


@pytest.mark.parametrize(
    ("panel", "snapshot", "message"),
    (
        (PositionsPanel, PositionsSnapshot.initial(), "No positions"),
        (OrdersPanel, OrdersSnapshot.initial(), "No active orders"),
        (ActivityPanel, ActivitySnapshot.initial(), "No mission events"),
        (
            WatchlistPanel,
            WatchlistSnapshot(),
            "Atlas is scanning",
        ),
    ),
)
def test_empty_projection_tables_show_helpful_empty_states(
    application,
    panel,
    snapshot,
    message,
) -> None:
    del application
    widget = panel()
    widget.render(snapshot)

    assert widget._table.rowCount() == 0
    assert widget._table._empty_state.isVisibleTo(widget._table)
    assert message in widget._table._empty_state.text()
