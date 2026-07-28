import os
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QAbstractItemView

from app.event_store import (
    EventStoreSnapshot,
    EventStoreStatus,
    IndexedEvent,
    IndexedSession,
    QueryResult,
    QueryStatistics,
)
from app.gui.widgets.event_store_panel import EventStorePanel
from app.operations_core import RuntimeStarting


APPLICATION = QApplication.instance() or QApplication([])
NOW = datetime(2026, 7, 28, 21, 0, tzinfo=timezone.utc)


def snapshot() -> EventStoreSnapshot:
    event = IndexedEvent(
        session_id="session-1",
        sequence_number=1,
        timestamp=NOW,
        event_type="RuntimeStarting",
        symbols=("AAPL",),
        order_ids=(),
        position_ids=(),
        decisions=(),
        lifecycle_phases=(),
        summary="RuntimeStarting: PAPER",
        event=RuntimeStarting(occurred_at=NOW),
    )
    statistics = QueryStatistics(
        total_sessions=1,
        total_events=1,
        matched_events=1,
        earliest_timestamp=NOW,
        latest_timestamp=NOW,
        event_type_counts=(("RuntimeStarting", 1),),
    )
    return EventStoreSnapshot(
        status=EventStoreStatus.READY,
        sessions=(
            IndexedSession(
                "session-1",
                "session.json",
                NOW,
                NOW,
                "1.0",
                "0.1.0",
                "BROKER_NEUTRAL",
                "PAPER",
                1,
            ),
        ),
        all_events=(event,),
        result=QueryResult("all", (event,), statistics),
        statistics=statistics,
        available_symbols=("AAPL",),
        available_event_types=("RuntimeStarting",),
        last_refresh=NOW,
        errors=(),
    )


def test_panel_renders_filters_and_emits_replay_intent() -> None:
    panel = EventStorePanel()
    replay = []
    searches = []
    panel.replay_requested.connect(replay.append)
    panel.search_requested.connect(searches.append)

    panel.render(snapshot())
    panel.search.setText("AAPL")
    panel._search()
    panel._replay(0, 0)

    assert panel.sessions.count() == 1
    assert panel.symbol_filter.itemText(1) == "AAPL"
    assert panel.event_type_filter.itemText(1) == "RuntimeStarting"
    assert panel.results.rowCount() == 1
    assert panel.results.item(0, 3).text() == "RuntimeStarting"
    assert searches == ["AAPL"]
    assert replay == ["session-1"]
    assert panel.results.editTriggers() == (
        QAbstractItemView.EditTrigger.NoEditTriggers
    )
    panel.deleteLater()
