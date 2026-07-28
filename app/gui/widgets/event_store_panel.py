from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.event_store import EventStoreSnapshot


class EventStorePanel(QWidget):
    search_requested = Signal(str)
    session_requested = Signal(str)
    symbol_requested = Signal(str)
    event_type_requested = Signal(str)
    replay_requested = Signal(str)
    refresh_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        controls = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search historical events")
        self.symbol_filter = QComboBox()
        self.event_type_filter = QComboBox()
        self.refresh_button = QPushButton("Refresh")
        controls.addWidget(self.search, 2)
        controls.addWidget(QLabel("Symbol"))
        controls.addWidget(self.symbol_filter)
        controls.addWidget(QLabel("Event Type"))
        controls.addWidget(self.event_type_filter)
        controls.addWidget(self.refresh_button)
        root.addLayout(controls)

        content = QHBoxLayout()
        self.sessions = QListWidget()
        self.sessions.setMaximumWidth(260)
        self.results = QTableWidget(0, 5)
        self.results.setHorizontalHeaderLabels(
            ("Time", "Session", "Symbol", "Event", "Summary")
        )
        self.results.horizontalHeader().setStretchLastSection(True)
        self.results.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.results.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        content.addWidget(self.sessions)
        content.addWidget(self.results, 1)
        root.addLayout(content)

        self.status = QLabel("EMPTY · 0 sessions · 0 events")
        self.status.setObjectName("muted")
        root.addWidget(self.status)

        self.search.returnPressed.connect(self._search)
        self.symbol_filter.activated.connect(self._symbol)
        self.event_type_filter.activated.connect(self._event_type)
        self.sessions.itemClicked.connect(self._session)
        self.results.cellDoubleClicked.connect(self._replay)
        self.refresh_button.clicked.connect(self.refresh_requested)

    def render(self, snapshot: EventStoreSnapshot) -> None:
        if not isinstance(snapshot, EventStoreSnapshot):
            raise TypeError("snapshot must be EventStoreSnapshot")
        self.sessions.blockSignals(True)
        self.sessions.clear()
        for session in snapshot.sessions:
            item = QListWidgetItem(
                f"{session.session_id} ({session.event_count})"
            )
            item.setData(Qt.ItemDataRole.UserRole, session.session_id)
            self.sessions.addItem(item)
        self.sessions.blockSignals(False)

        self._replace_filter(
            self.symbol_filter,
            snapshot.available_symbols,
            "All Symbols",
        )
        self._replace_filter(
            self.event_type_filter,
            snapshot.available_event_types,
            "All Event Types",
        )

        self.results.setRowCount(len(snapshot.result.events))
        for row_index, event in enumerate(snapshot.result.events):
            values = (
                f"{event.timestamp.astimezone():%Y-%m-%d %H:%M:%S}",
                event.session_id,
                ", ".join(event.symbols) or "--",
                event.event_type,
                event.summary,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(
                    Qt.ItemDataRole.UserRole,
                    event.session_id,
                )
                self.results.setItem(row_index, column, item)
        self.status.setText(
            f"{snapshot.status.value} · "
            f"{len(snapshot.sessions)} sessions · "
            f"{snapshot.statistics.total_events} events · "
            f"{snapshot.result.statistics.matched_events} matched"
        )

    def _replace_filter(
        self,
        combo: QComboBox,
        values: tuple[str, ...],
        all_label: str,
    ) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(all_label, "")
        for value in values:
            combo.addItem(value, value)
        combo.blockSignals(False)

    def _search(self) -> None:
        value = self.search.text().strip()
        if value:
            self.search_requested.emit(value)

    def _symbol(self, index: int) -> None:
        value = self.symbol_filter.itemData(index)
        self.symbol_requested.emit(value if isinstance(value, str) else "")

    def _event_type(self, index: int) -> None:
        value = self.event_type_filter.itemData(index)
        self.event_type_requested.emit(
            value if isinstance(value, str) else ""
        )

    def _session(self, item: QListWidgetItem) -> None:
        session_id = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(session_id, str) and session_id:
            self.session_requested.emit(session_id)

    def _replay(self, row: int, column: int) -> None:
        item = self.results.item(row, column)
        if item is None:
            return
        session_id = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(session_id, str) and session_id:
            self.replay_requested.emit(session_id)
