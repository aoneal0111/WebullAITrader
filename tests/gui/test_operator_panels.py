import os
from datetime import UTC, datetime, timedelta

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


def test_activity_panel_prepends_newest_event_without_rebuilding_existing_rows(
    application,
    monkeypatch,
) -> None:
    del application
    panel = ActivityPanel()

    older = ActivityEntry(
        occurred_at=datetime(2026, 7, 30, 16, 0, tzinfo=UTC),
        message="Older event",
        category="SYSTEM",
        severity="INFO",
        source="runtime",
    )
    newer = ActivityEntry(
        occurred_at=datetime(2026, 7, 30, 16, 0, 1, tzinfo=UTC),
        message="Newer event",
        category="SYSTEM",
        severity="INFO",
        source="runtime",
    )

    initial = ActivitySnapshot(
        entries=(older,),
        severity_options=("ALL", "INFO"),
        category_options=("ALL", "SYSTEM"),
        symbol_options=("ALL",),
    )
    updated = ActivitySnapshot(
        entries=(newer, older),
        severity_options=("ALL", "INFO"),
        category_options=("ALL", "SYSTEM"),
        symbol_options=("ALL",),
    )

    panel.render(initial)

    set_item_calls = []
    original_set_item = panel._table.setItem

    def count_set_item(row, column, item):
        set_item_calls.append((row, column))
        return original_set_item(row, column, item)

    monkeypatch.setattr(
        panel._table,
        "setItem",
        count_set_item,
    )

    panel.render(updated)

    assert panel._table.rowCount() == 2

    # A newest-first timeline update should create only the six cells
    # belonging to the newly inserted first row.
    assert set_item_calls == [
        (0, 0),
        (0, 1),
        (0, 2),
        (0, 3),
        (0, 4),
        (0, 5),
    ]

    assert panel._table.item(0, 5).text() == "Newer event"
    assert panel._table.item(1, 5).text() == "Older event"

def test_activity_panel_slides_bounded_newest_first_window_without_rebuilding_rows(
    application,
    monkeypatch,
) -> None:
    del application
    panel = ActivityPanel()

    base_time = datetime(
        2026,
        7,
        30,
        16,
        0,
        tzinfo=UTC,
    )

    entries = tuple(
        ActivityEntry(
            occurred_at=base_time + timedelta(seconds=index),
            message=f"Event {index}",
            category="SYSTEM",
            severity="INFO",
            source="runtime",
        )
        for index in range(100)
    )

    initial_entries = tuple(reversed(entries))

    newest = ActivityEntry(
        occurred_at=base_time + timedelta(seconds=100),
        message="Event 100",
        category="SYSTEM",
        severity="INFO",
        source="runtime",
    )

    updated_entries = (
        newest,
        *initial_entries[:-1],
    )

    initial = ActivitySnapshot(
        entries=initial_entries,
        severity_options=("ALL", "INFO"),
        category_options=("ALL", "SYSTEM"),
        symbol_options=("ALL",),
    )

    updated = ActivitySnapshot(
        entries=updated_entries,
        severity_options=("ALL", "INFO"),
        category_options=("ALL", "SYSTEM"),
        symbol_options=("ALL",),
    )

    panel.render(initial)

    assert panel._table.rowCount() == 100
    assert panel._table.item(0, 5).text() == "Event 99"
    assert panel._table.item(99, 5).text() == "Event 0"

    set_item_calls = []
    insert_row_calls = []
    remove_row_calls = []

    original_set_item = panel._table.setItem
    original_insert_row = panel._table.insertRow
    original_remove_row = panel._table.removeRow

    def count_set_item(row, column, item):
        set_item_calls.append((row, column))
        return original_set_item(row, column, item)

    def count_insert_row(row):
        insert_row_calls.append(row)
        return original_insert_row(row)

    def count_remove_row(row):
        remove_row_calls.append(row)
        return original_remove_row(row)

    monkeypatch.setattr(
        panel._table,
        "setItem",
        count_set_item,
    )
    monkeypatch.setattr(
        panel._table,
        "insertRow",
        count_insert_row,
    )
    monkeypatch.setattr(
        panel._table,
        "removeRow",
        count_remove_row,
    )

    panel.render(updated)

    assert panel._table.rowCount() == 100

    # Exactly one newest-first row is structurally inserted.
    assert insert_row_calls == [0]

    # Exactly one expired row is removed from the bottom after insertion.
    assert remove_row_calls == [100]

    # Only the six cells belonging to Event 100 are constructed.
    # The retained 99 rows must keep their existing Qt items.
    assert set_item_calls == [
        (0, 0),
        (0, 1),
        (0, 2),
        (0, 3),
        (0, 4),
        (0, 5),
    ]

    assert panel._table.item(0, 5).text() == "Event 100"
    assert panel._table.item(1, 5).text() == "Event 99"
    assert panel._table.item(99, 5).text() == "Event 1"

    # Event 0 aged out of the bounded live presentation.
    assert all(
        panel._table.item(row, 5).text() != "Event 0"
        for row in range(panel._table.rowCount())
    )